"""File ingestion endpoints.

Upload accepts several files at once, writes each to disk, records a job row, and
returns immediately. Processing happens on the bounded queue; the frontend polls
`/ingest/jobs`. Nothing about a load lives only in memory.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from api.db import fetch_all, fetch_one
from api.deps import CurrentUserDep, DbDep, SettingsDep, rate_limit, require_role
from api.errors import ApiError, NotFound, PayloadTooLarge
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
AnalystDep = Annotated[object, Depends(require_role("analyst"))]

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
MAX_FILES_PER_UPLOAD = 20


def _safe_filename(name: str) -> str:
    """Strip anything that could escape the upload directory."""
    base = Path(name or "archivo").name
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._") or "archivo"
    return cleaned[:200]


@router.post(
    "/upload",
    response_model=UploadAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[ingest_rate_limit],
    summary="Subir uno o varios reportes",
)
async def upload(
    conn: DbDep,
    user: CurrentUserDep,
    settings: SettingsDep,
    connection_id: Annotated[UUID, Form(description="Conexión a la que pertenecen los archivos")],
    kind: Annotated[str, Form(description="shipments | movements | ads | cs")] = "shipments",
    files: Annotated[list[UploadFile], File()] = ...,
) -> UploadAcceptedResponse:
    if not user.at_least("analyst"):
        raise ApiError(
            "forbidden", "Tu rol no permite cargar datos",
            status_code=status.HTTP_403_FORBIDDEN,
        )

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
        "SELECT id FROM core.connection WHERE id = %s AND tenant_id = %s",
        (connection_id, user.tenant_id),
    )
    if owner is None:
        raise NotFound("Esa conexión no existe en tu workspace")

    upload_dir = Path(settings.upload_dir) / str(user.tenant_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[UploadJobResponse] = []
    for upload_file in files:
        filename = _safe_filename(upload_file.filename or "")
        if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
            raise ApiError(
                "unsupported_file",
                f"'{filename}': formato no soportado. "
                f"Se aceptan {', '.join(SUPPORTED_EXTENSIONS)}.",
            )

        payload = await upload_file.read()
        if len(payload) > settings.max_upload_bytes:
            raise PayloadTooLarge(
                f"'{filename}' pesa {len(payload) / 1_048_576:.1f} MB y el máximo "
                f"es {settings.max_upload_mb} MB."
            )
        if not payload:
            raise ApiError("empty_file", f"'{filename}' está vacío")

        stored_path = upload_dir / f"{uuid.uuid4()}_{filename}"
        stored_path.write_bytes(payload)

        row = fetch_one(
            conn,
            """
            INSERT INTO raw.upload_job
                (tenant_id, connection_id, uploaded_by, filename, kind, size_bytes, storage_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, filename, kind, size_bytes, status, batch_id, error,
                      queued_at, finished_at
            """,
            (
                user.tenant_id, connection_id, user.id, filename,
                batch_kind.value, len(payload), str(stored_path),
            ),
        )
        jobs.append(UploadJobResponse(**row))

    # Commit before queueing: the worker must be able to see the rows.
    conn.commit()
    queue = get_queue()
    for job in jobs:
        queue.submit(job.id)

    return UploadAcceptedResponse(
        jobs=jobs,
        message=f"{len(jobs)} archivo(s) en cola. El progreso aparece en esta misma pantalla.",
    )


@router.get("/jobs", response_model=list[UploadJobResponse], summary="Estado de las cargas")
def list_jobs(
    conn: DbDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[UploadJobResponse]:
    rows = fetch_all(
        conn,
        """
        SELECT id, filename, kind, size_bytes, status, batch_id, error, queued_at, finished_at
        FROM raw.upload_job WHERE tenant_id = %s
        ORDER BY queued_at DESC LIMIT %s
        """,
        (user.tenant_id, limit),
    )
    return [UploadJobResponse(**row) for row in rows]


@router.get("/jobs/{job_id}", response_model=UploadJobResponse, summary="Estado de una carga")
def get_job(job_id: UUID, conn: DbDep, user: CurrentUserDep) -> UploadJobResponse:
    row = fetch_one(
        conn,
        """
        SELECT id, filename, kind, size_bytes, status, batch_id, error, queued_at, finished_at
        FROM raw.upload_job WHERE id = %s AND tenant_id = %s
        """,
        (job_id, user.tenant_id),
    )
    if row is None:
        raise NotFound("Esa carga no existe")
    return UploadJobResponse(**row)


@router.get("/batches", response_model=PaginatedBatches, summary="Historial de cargas")
def list_batches(
    conn: DbDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
) -> PaginatedBatches:
    where = ""
    params: list = []
    if country:
        where = "WHERE country_code = %s"
        params.append(country.upper())

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
    "/batches/{batch_id}", response_model=BatchDetail, summary="Detalle de una carga"
)
def get_batch(batch_id: UUID, conn: DbDep, user: CurrentUserDep) -> BatchDetail:
    row = fetch_one(conn, "SELECT * FROM mart.v_batch_history WHERE batch_id = %s", (batch_id,))
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
