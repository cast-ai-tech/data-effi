"""File ingestion endpoints.

Upload accepts several files at once, writes each to disk, records a job row, and
returns immediately. Processing happens on the bounded queue; the frontend polls
`/ingest/jobs`. Nothing about a load lives only in memory.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from psycopg.types.json import Json
from starlette.datastructures import UploadFile as StarletteUploadFile

from api.db import check_rate_limit, connection, execute, fetch_all, fetch_one, fetch_required
from api.deps import (
    CurrentUserDep,
    DbDep,
    SettingsDep,
    client_ip,
    country_scope_sql,
    rate_limit,
    require_cap,
    tenant_of,
)
from api.errors import ApiError, NotFound, PayloadTooLarge
from api.events import emit
from api.ingest_queue import get_queue
from api.schemas import (
    BatchDetail,
    BatchSummary,
    DetectResponse,
    DiscrepancyResponse,
    PaginatedBatches,
    UploadAcceptedResponse,
    UploadJobResponse,
)
from api.security import hash_token
from api.settings import Settings
from pipeline.models import BatchKind
from pipeline.profiles import build_profile_header_map, detect_country, detect_profile
from pipeline.readers import (
    SUPPORTED_EXTENSIONS,
    EmptyFileError,
    UnsupportedFileError,
    read_tabular,
    sniff_format,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

ingest_rate_limit = Depends(rate_limit("ingest", "rate_limit_ingest_per_minute"))

# Asserted per endpoint rather than on the router, because `/webhook/{token}`
# below is machine-facing: its credential is the token in the path, and it must
# stay reachable without a bearer token. Every OTHER endpoint here is used by a
# person with a session, so each one carries this guard explicitly - including
# any added later, which is the trade-off of not mounting it once.
ingest_guard = Depends(require_cap("ingest"))

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
MAX_FILES_PER_UPLOAD = 20


def _safe_filename(name: str) -> str:
    """Strip anything that could escape the upload directory."""
    base = Path(name or "archivo").name
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._") or "archivo"
    return cleaned[:200]


async def _read_within_limits(upload_file: UploadFile, settings) -> tuple[str, bytes]:
    """Safe name and bytes, or the Spanish reason why not.

    Shared by /upload and /detect so a file that one accepts is exactly the file
    the other accepts. A preview that says yes to something the upload then
    refuses is worse than no preview.
    """
    filename = _safe_filename(upload_file.filename or "")
    if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
        raise ApiError(
            "unsupported_file",
            f"'{filename}': formato no soportado. "
            f"Se aceptan {', '.join(SUPPORTED_EXTENSIONS)}.",
        )

    # Read in chunks and stop at the limit instead of buffering the whole file
    # first: twenty 25 MB files read whole is 500 MB of RAM on a box that has
    # 512, and the size check would arrive after the damage.
    too_large = PayloadTooLarge(
        f"'{filename}' supera el máximo de {settings.max_upload_mb} MB."
    )
    declared = getattr(upload_file, "size", None)
    if declared is not None and declared > settings.max_upload_bytes:
        raise too_large

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload_file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise too_large
        chunks.append(chunk)
    payload = b"".join(chunks)

    if not payload:
        raise ApiError("empty_file", f"'{filename}' está vacío")

    return filename, payload


@router.post(
    "/upload",
    response_model=UploadAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[ingest_guard, ingest_rate_limit],
    summary="Subir uno o varios reportes",
)
async def upload(
    conn: DbDep,
    user: CurrentUserDep,
    settings: SettingsDep,
    connection_id: Annotated[UUID, Form(description="Conexión a la que pertenecen los archivos")],
    files: Annotated[list[UploadFile], File()],
    kind: Annotated[str, Form(description="shipments | movements | ads | cs")] = "shipments",
    reprocess: Annotated[
        bool,
        Form(
            description=(
                "Volver a procesar un archivo ya cargado. Úsalo cuando el motor "
                "cambió desde la carga anterior; no duplica datos."
            )
        ),
    ] = False,
) -> UploadAcceptedResponse:
    # Who may upload is decided by `ingest_guard` on the route, not by seniority
    # here: `uploader` outranks nobody and must still be let through. Keeping a
    # second `at_least("analyst")` check was what silently refused that role.
    try:
        batch_kind = BatchKind(kind)
    except ValueError as exc:
        raise ApiError("invalid_kind", f"Tipo de carga inválido: {kind!r}") from exc

    if len(files) > MAX_FILES_PER_UPLOAD:
        raise ApiError(
            "too_many_files",
            f"Máximo {MAX_FILES_PER_UPLOAD} archivos por carga. Enviaste {len(files)}.",
        )

    owner = fetch_one(
        conn,
        "SELECT id, country_code FROM core.connection WHERE id = %s AND tenant_id = %s",
        (connection_id, user.tenant_id),
    )
    if owner is None:
        raise NotFound("Esa conexión no existe en tu workspace")

    # Subir por una conexión de otro país es escribir en un país que no puedes
    # leer, y el archivo cargado ya no se puede "desver": queda en los totales
    # de esa operación. Una conexión global se rechaza por lo mismo - mezcla
    # países - y por eso el mensaje distingue los dos casos.
    if user.countries is not None:
        destino = (owner["country_code"] or "").upper()
        if not destino:
            raise ApiError(
                "forbidden",
                "Esa conexión recibe datos de varios países y tu usuario está "
                f"limitado a: {', '.join(user.countries)}.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if destino not in user.countries:
            raise ApiError(
                "forbidden",
                f"Esa conexión carga datos de {destino} y tu usuario solo tiene "
                f"acceso a: {', '.join(user.countries)}.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

    upload_dir = Path(settings.upload_dir) / str(user.tenant_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[UploadJobResponse] = []
    for upload_file in files:
        filename, payload = await _read_within_limits(upload_file, settings)

        stored_path = upload_dir / f"{uuid.uuid4()}_{filename}"
        stored_path.write_bytes(payload)

        row = fetch_required(
            conn,
            """
            INSERT INTO raw.upload_job
                (tenant_id, connection_id, uploaded_by, filename, kind, size_bytes,
                 storage_path, reprocess)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, filename, kind, size_bytes, status, batch_id, error,
                      queued_at, finished_at
            """,
            (
                user.tenant_id, connection_id, user.id, filename,
                batch_kind.value, len(payload), str(stored_path), reprocess,
            ),
        )
        jobs.append(UploadJobResponse(**row))
        # The screen hears "queued" in the same commit that makes the job real.
        emit(
            conn, tenant_of(user), "upload_job.updated",
            country_code=owner["country_code"],
            payload=_job_event(row, "queued"),
        )

    # Commit before queueing: the worker must be able to see the rows.
    conn.commit()
    queue = get_queue()
    for job in jobs:
        queue.submit(job.id)

    return UploadAcceptedResponse(
        jobs=jobs,
        message=f"{len(jobs)} archivo(s) en cola. El progreso aparece en esta misma pantalla.",
    )


# =============================================================================
# Webhook ingestion
#
# NO JWT HERE. The token in the path IS the credential: whoever holds it may
# push data into exactly one connection and do nothing else. That is the point -
# an n8n scenario cannot hold a user password, and should not.
#
# What the token buys is a place in the SAME queue an upload uses. A JSON payload
# becomes a CSV in memory, is written down like any other file, and is handed to
# IngestEngine: same content hash, same idempotence, same merge rules, same PII
# hashing. There is no privileged path into the database.
# =============================================================================

WEBHOOK_JOB_NAME = "webhook_ingest"
MAX_WEBHOOK_ROWS = 50_000


@router.post(
    "/webhook/{token}",
    response_model=UploadAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Recibir datos desde una automatización (n8n · Make · Zapier)",
)
async def webhook_ingest(
    token: str, request: Request, settings: SettingsDep
) -> UploadAcceptedResponse:
    """Accept `{"kind": ..., "rows": [...]}` or a plain file, and queue it."""
    target = _webhook_connection(token)

    if target is None:
        # Never say whether the token ever existed, expired or was revoked: that
        # difference is exactly what a token-guessing script measures. The
        # per-IP limit is here rather than at the top so that a scanner cannot
        # fill raw.job_run either.
        if _allowed(request, "webhook_unknown", client_ip(request), settings):
            _record_webhook_run(None, "failed", {"reason": "unknown_token"}, "Token no válido")
        raise NotFound("Webhook no encontrado")

    tenant_id = target["tenant_id"]
    if not _allowed(request, "webhook", str(target["id"]), settings):
        _record_webhook_run(
            tenant_id, "failed", {"connection_id": str(target["id"])},
            "Rate limit del webhook alcanzado",
        )
        raise ApiError(
            "rate_limited",
            "Este webhook recibió demasiadas peticiones. Espera un minuto.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    try:
        filename, payload, declared_kind = await _webhook_payload(request, settings)
        batch_kind = _webhook_kind(declared_kind, target)
        job = _queue_webhook_job(target, filename, payload, batch_kind, settings)
    except ApiError as exc:
        _record_webhook_run(
            tenant_id, "failed", {"connection_id": str(target["id"]), "code": exc.code},
            exc.message,
        )
        raise

    _record_webhook_run(
        tenant_id,
        "ok",
        {
            "connection_id": str(target["id"]),
            "job_id": str(job.id),
            "kind": job.kind,
            "size_bytes": job.size_bytes,
        },
        None,
    )
    return UploadAcceptedResponse(
        jobs=[job],
        message="Datos recibidos y en cola. El resultado aparece en el historial de cargas.",
    )


def _allowed(request: Request, scope: str, subject: str, settings: Settings) -> bool:
    with connection(service=True) as conn:
        return check_rate_limit(
            conn, scope=scope, subject=subject, limit=settings.rate_limit_ingest_per_minute
        )


def _webhook_connection(token: str) -> dict[str, Any] | None:
    """Find the connection this token belongs to, by hash.

    A service connection, because there is no user and therefore no tenant to
    scope to yet - the token is what tells us which tenant this is.
    """
    if not token or len(token) > 200:
        return None

    with connection(service=True) as conn:
        return fetch_one(
            conn,
            """
            SELECT c.id, c.tenant_id, c.country_code, c.platform_code, c.default_kind
            FROM core.connection c
            WHERE c.webhook_token_hash = %s AND c.status = 'active'
            """,
            (hash_token(token),),
        )


def _webhook_kind(declared: str | None, target: dict[str, Any]) -> BatchKind:
    raw = declared or target["default_kind"]
    if not raw:
        raise ApiError(
            "kind_required",
            'No sabemos qué tipo de datos son. Envía "kind" en el cuerpo '
            "(shipments, movements, ads o cs) o define el tipo por defecto de la conexión.",
        )
    try:
        return BatchKind(raw)
    except ValueError as exc:
        raise ApiError("invalid_kind", f"Tipo de carga inválido: {raw!r}") from exc


async def _webhook_payload(
    request: Request, settings: Settings
) -> tuple[str, bytes, str | None]:
    """Bytes to ingest, whatever shape the automation sent them in.

    Three shapes, one destination. JSON rows become a CSV in memory so they take
    the identical path an uploaded file takes; nothing here writes to core.*.
    """
    content_type = request.headers.get("content-type", "").lower()
    stamp = datetime.now(UTC)

    if content_type.startswith("multipart/form-data"):
        _refuse_declared_oversize(request, settings)
        form = await request.form()
        form_kind = form.get("kind")
        declared_kind = form_kind if isinstance(form_kind, str) else None
        for value in form.values():
            if isinstance(value, StarletteUploadFile):
                payload = await value.read()
                _refuse_oversize(len(payload), settings)
                name = _safe_filename(value.filename or "webhook.csv")
                if not name.lower().endswith(SUPPORTED_EXTENSIONS):
                    raise ApiError(
                        "unsupported_file",
                        f"'{name}': formato no soportado. "
                        f"Se aceptan {', '.join(SUPPORTED_EXTENSIONS)}.",
                    )
                return name, payload, declared_kind
        raise ApiError("empty_payload", "El formulario no traía ningún archivo.")

    body = await _read_body_limited(request, settings)
    if not body:
        raise ApiError("empty_payload", "El cuerpo de la petición está vacío.")

    if "json" in content_type or body.lstrip()[:1] in (b"{", b"["):
        rows, declared_kind = _json_rows(body)
        return f"webhook_{stamp:%Y%m%d_%H%M%S}.csv", _rows_to_csv(rows), declared_kind

    # A raw file body: exactly what /ingest/upload would have received.
    filename = _safe_filename(
        request.headers.get("x-filename") or f"webhook_{stamp:%Y%m%d_%H%M%S}.csv"
    )
    if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
        filename = f"{filename}.csv"
    return filename, body, request.headers.get("x-kind")


def _json_rows(body: bytes) -> tuple[list[dict[str, Any]], str | None]:
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise ApiError("invalid_json", "El cuerpo no es JSON válido.") from exc

    declared_kind: str | None = None
    if isinstance(parsed, list):
        rows: Any = parsed
    elif isinstance(parsed, dict):
        rows = parsed.get("rows")
        raw_kind = parsed.get("kind")
        declared_kind = str(raw_kind) if raw_kind is not None else None
    else:
        raise ApiError("invalid_json", 'Se esperaba un objeto con "rows" o una lista de filas.')

    if not isinstance(rows, list) or not rows:
        raise ApiError(
            "empty_payload",
            'No llegó ninguna fila. Envía {"kind": "shipments", "rows": [ ... ]}.',
        )
    if len(rows) > MAX_WEBHOOK_ROWS:
        raise ApiError(
            "too_many_rows",
            f"Máximo {MAX_WEBHOOK_ROWS} filas por envío. Recibimos {len(rows)}.",
        )
    if not all(isinstance(row, dict) for row in rows):
        raise ApiError("invalid_rows", "Cada fila debe ser un objeto con sus columnas.")
    return rows, declared_kind


def _rows_to_csv(rows: list[dict[str, Any]]) -> bytes:
    """JSON objects -> a CSV the readers already know how to parse.

    Column order is first-seen order across every row, so a later row carrying an
    extra field widens the sheet instead of losing the field.
    """
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            name = str(key)
            if name not in seen:
                seen.add(name)
                columns.append(name)

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({name: _csv_scalar(row.get(name)) for name in columns})
    return buffer.getvalue().encode("utf-8")


def _csv_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _refuse_declared_oversize(request: Request, settings: Settings) -> None:
    declared = request.headers.get("content-length", "")
    if declared.isdigit():
        _refuse_oversize(int(declared), settings)


def _refuse_oversize(size: int, settings: Settings) -> None:
    if size > settings.max_upload_bytes:
        raise PayloadTooLarge(
            f"El envío pesa {size / 1_048_576:.1f} MB y el máximo es "
            f"{settings.max_upload_mb} MB."
        )


async def _read_body_limited(request: Request, settings: Settings) -> bytes:
    """Read the body, refusing to buffer more than the upload limit.

    Checked twice on purpose: Content-Length lets us say no before reading a
    byte, and the running total covers a chunked request that declares nothing.
    """
    _refuse_declared_oversize(request, settings)

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        _refuse_oversize(total, settings)
        chunks.append(chunk)
    return b"".join(chunks)


def _queue_webhook_job(
    target: dict[str, Any],
    filename: str,
    payload: bytes,
    kind: BatchKind,
    settings: Settings,
) -> UploadJobResponse:
    """Write the payload down and queue it, exactly as /ingest/upload does."""
    upload_dir = Path(settings.upload_dir) / str(target["tenant_id"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_path = upload_dir / f"{uuid.uuid4()}_{filename}"
    stored_path.write_bytes(payload)

    with connection(service=True) as conn:
        row = fetch_required(
            conn,
            """
            INSERT INTO raw.upload_job
                (tenant_id, connection_id, uploaded_by, filename, kind, size_bytes, storage_path)
            VALUES (%s, %s, NULL, %s, %s, %s, %s)
            RETURNING id, filename, kind, size_bytes, status, batch_id, error,
                      queued_at, finished_at
            """,
            (
                target["tenant_id"], target["id"], filename, kind.value,
                len(payload), str(stored_path),
            ),
        )

        emit(
            conn, target["tenant_id"], "upload_job.updated",
            country_code=target.get("country_code"),
            payload=_job_event(row, "queued"),
        )

    job = UploadJobResponse(**row)
    get_queue().submit(job.id)
    return job


def _job_event(row: dict[str, Any], status_value: str) -> dict[str, Any]:
    """The payload of `upload_job.updated`: identifiers and state, no text."""
    return {
        "job_id": row["id"],
        "status": status_value,
        "kind": row["kind"],
        "filename": row["filename"],
    }


def _record_webhook_run(
    tenant_id: UUID | None, status_value: str, result: dict[str, Any], error: str | None
) -> None:
    """Every webhook call lands in raw.job_run, successful or not.

    An integration that quietly stopped working is the failure mode this exists
    to prevent: `SELECT ... FROM raw.job_run WHERE job_name = 'webhook_ingest'`
    is how anybody finds out.
    """
    try:
        with connection(service=True) as conn:
            execute(
                conn,
                """
                INSERT INTO raw.job_run (job_name, tenant_id, status, finished_at, result, error)
                VALUES (%s, %s, %s, now(), %s, %s)
                """,
                (WEBHOOK_JOB_NAME, tenant_id, status_value, Json(result), error),
            )
    except Exception:      # pragma: no cover - logging must never break ingestion
        logger.warning("could not record webhook job_run", exc_info=True)


@router.post(
    "/detect",
    response_model=DetectResponse,
    dependencies=[ingest_guard, ingest_rate_limit],
    summary="Analizar un archivo sin guardarlo ni cargarlo",
)
async def detect(
    user: CurrentUserDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="Un solo archivo. No se almacena.")],
) -> DetectResponse:
    """Say what Data Effi understood about a file, before anything is committed.

    Nothing is written: not to disk, not to `raw.upload_job`, not to the queue.
    This is what lets the upload screen say "esto es un reporte de guías de
    Ecuador, 1.649 filas, 43 columnas reconocidas" while the operator can still
    change their mind.
    """
    filename, payload = await _read_within_limits(file, settings)
    file_format = sniff_format(payload, filename)

    try:
        headers, rows = read_tabular(payload, filename)
    except (UnsupportedFileError, EmptyFileError) as exc:
        raise ApiError("unreadable_file", str(exc)) from exc

    clean_headers = [str(header).strip() for header in headers]
    profile = detect_profile(headers)

    if profile is None:
        # No known report shape. The alias matcher that would guess these
        # columns only runs during ingestion, so saying "no reconocido" is
        # honest; guessing here and mapping differently later would not be.
        return DetectResponse(
            filename=filename,
            format=file_format,
            profile_code=None,
            profile_label=None,
            detected_country_code=None,
            detected_country_raw=None,
            row_count=len(rows),
            column_count=len(headers),
            mapped_columns={},
            unmapped_columns=[header for header in clean_headers if header],
        )

    header_map, unmapped = build_profile_header_map(headers, profile)
    country_code, country_raw = detect_country(headers, rows, profile)

    return DetectResponse(
        filename=filename,
        format=file_format,
        profile_code=profile.code,
        profile_label=profile.label,
        detected_country_code=country_code,
        detected_country_raw=country_raw,
        row_count=len(rows),
        column_count=len(headers),
        mapped_columns={
            clean_headers[position]: field_name
            for position, field_name in sorted(header_map.items())
        },
        unmapped_columns=unmapped,
    )


@router.get(
    "/jobs",
    response_model=list[UploadJobResponse],
    dependencies=[ingest_guard],
    summary="Estado de las cargas",
)
def list_jobs(
    conn: DbDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[UploadJobResponse]:
    # El país de una carga vive en su conexión, no en la carga: duplicarlo sería
    # una segunda fuente de verdad. Por eso el alcance se aplica con un JOIN.
    scope_sql, scope_params = country_scope_sql(user, "c.country_code")
    rows = fetch_all(
        conn,
        f"""
        SELECT j.id, j.filename, j.kind, j.size_bytes, j.status, j.batch_id, j.error,
               j.queued_at, j.finished_at
        FROM raw.upload_job j
        JOIN core.connection c ON c.id = j.connection_id
        WHERE j.tenant_id = %s{scope_sql}
        ORDER BY j.queued_at DESC LIMIT %s
        """,
        (user.tenant_id, *scope_params, limit),
    )
    return [UploadJobResponse(**row) for row in rows]


@router.get(
    "/jobs/{job_id}",
    response_model=UploadJobResponse,
    dependencies=[ingest_guard],
    summary="Estado de una carga",
)
def get_job(job_id: UUID, conn: DbDep, user: CurrentUserDep) -> UploadJobResponse:
    scope_sql, scope_params = country_scope_sql(user, "c.country_code")
    row = fetch_one(
        conn,
        f"""
        SELECT j.id, j.filename, j.kind, j.size_bytes, j.status, j.batch_id, j.error,
               j.queued_at, j.finished_at
        FROM raw.upload_job j
        JOIN core.connection c ON c.id = j.connection_id
        WHERE j.id = %s AND j.tenant_id = %s{scope_sql}
        """,
        (job_id, user.tenant_id, *scope_params),
    )
    if row is None:
        # Mismo 404 que para una carga inexistente: que exista en un país que no
        # puedes ver no es algo que debas poder averiguar por el código de error.
        raise NotFound("Esa carga no existe")
    return UploadJobResponse(**row)


@router.get(
    "/batches",
    response_model=PaginatedBatches,
    dependencies=[ingest_guard],
    summary="Historial de cargas",
)
def list_batches(
    conn: DbDep,
    user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
) -> PaginatedBatches:
    clauses: list[str] = []
    params: list = []
    if country:
        clauses.append("country_code = %s")
        params.append(country.upper())

    # Recortado en SQL y no sobre las filas ya traídas: con paginación, filtrar
    # después deja páginas cortas y un `total` que miente.
    scope_sql, scope_params = country_scope_sql(user)
    if scope_sql:
        clauses.append(scope_sql.replace(" AND ", "", 1))
        params.extend(scope_params)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total_row = fetch_one(
        conn, f"SELECT count(*) AS n FROM mart.v_batch_history {where}", tuple(params)
    )
    rows = fetch_all(
        conn,
        f"""
        SELECT * FROM mart.v_batch_history {where}
        ORDER BY started_at DESC LIMIT %s OFFSET %s
        """,
        (*params, page_size, (page - 1) * page_size),
    )
    return PaginatedBatches(
        items=[BatchSummary(**row) for row in rows],
        total=total_row["n"] if total_row else 0,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/batches/{batch_id}",
    response_model=BatchDetail,
    dependencies=[ingest_guard],
    summary="Detalle de una carga",
)
def get_batch(batch_id: UUID, conn: DbDep, user: CurrentUserDep) -> BatchDetail:
    scope_sql, scope_params = country_scope_sql(user)
    row = fetch_one(
        conn,
        f"SELECT * FROM mart.v_batch_history WHERE batch_id = %s{scope_sql}",
        (batch_id, *scope_params),
    )
    if row is None:
        raise NotFound("Esa carga no existe en tu workspace")

    report_row = fetch_one(
        conn, "SELECT report FROM raw.load_batch WHERE id = %s AND tenant_id = %s",
        (batch_id, user.tenant_id),
    )
    discrepancies = fetch_all(
        conn,
        """
        SELECT entity, entity_key, field_name, old_value, new_value, detected_at
        FROM raw.load_discrepancy WHERE batch_id = %s AND tenant_id = %s
        ORDER BY detected_at LIMIT 500
        """,
        (batch_id, user.tenant_id),
    )

    return BatchDetail(
        batch=BatchSummary(**row),
        report=(report_row or {}).get("report") or {},
        discrepancies=[DiscrepancyResponse(**d) for d in discrepancies],
    )
