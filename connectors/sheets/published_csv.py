"""Google Sheets connector: a sheet the merchant published to the web as CSV.

WHY THERE IS NO OAUTH HERE
--------------------------
"Archivo → Compartir → Publicar en la web → CSV" turns a sheet into a plain
public HTTPS URL that returns comma-separated text to anybody who asks. No
token, no consent screen, no Google Cloud project, no scopes to review. That is
the entire integration, and building an OAuth flow to read a document that is
already public would add a login and protect nothing.

The price is that the operator must understand what they did: a published sheet
IS public. That is said out loud in the platform's `setup_hint` and again in the
comment on `core.connection.source_url` (migration 013).

THE RULES THIS MODULE ENFORCES
------------------------------
1. HTTPS ONLY, docs.google.com ONLY. The URL comes from a user, and an endpoint
   that fetches a user-supplied address is how a server ends up reading
   169.254.169.254 or an internal admin panel on the operator's behalf. Anything
   that is not an `https://docs.google.com/...` address is refused outright -
   no redirects followed, no other host, no other scheme.
2. NO EVASION. Data Effi identifies itself in the User-Agent, waits between
   requests, and does not retry a 401/403. If Google says no, the answer is no.
3. NO URL IN THE LOGS. A published URL is public, but the same column could one
   day hold one with `?key=` or a long opaque id in it, and a log line is
   forever. Only scheme, host and path are ever logged; the query string is
   replaced by a marker.
4. RAW BYTES OUT. The fetch returns exactly what the sheet returned. It goes
   through the same IngestEngine an upload does - same content hash, same
   idempotence, same merge rules. A sheet has no privileged path into the
   database.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from pipeline.models import BatchKind

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 45.0
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
USER_AGENT = "DataEffi-Analytics/1.0 (+hoja publicada por el comerciante)"

# The one host this connector will ever fetch from. Not a suffix match: an
# attacker registering `docs.google.com.evil.tld` would pass a suffix check.
ALLOWED_HOST = "docs.google.com"
MAX_SHEET_BYTES = 25 * 1024 * 1024


class InvalidSheetUrlError(ValueError):
    """The URL is not a Google Sheets address we are willing to fetch."""


class SheetFetchError(RuntimeError):
    """The fetch failed for a reason the caller should surface, not retry blindly."""


class SheetNotPublishedError(SheetFetchError):
    """Google answered with a login or an HTML page: the sheet is not published."""


@dataclass(slots=True)
class SheetFetchResult:
    """A downloaded sheet, ready to hand to the same IngestEngine an upload uses."""

    filename: str
    payload: bytes
    kind: BatchKind
    fetched_at: datetime

    @property
    def size_bytes(self) -> int:
        return len(self.payload)


def validate_published_url(raw_url: str) -> str:
    """Return the URL if it is one we may fetch, or say why not, in Spanish.

    Used by the API before storing a connection AND by the fetcher before every
    request. Two callers, one rule: a row that somehow skipped the API still
    cannot make the server fetch an arbitrary address.
    """
    url = (raw_url or "").strip()
    if not url:
        raise InvalidSheetUrlError("Falta la URL de la hoja publicada.")

    parts = urlsplit(url)
    if parts.scheme != "https":
        raise InvalidSheetUrlError(
            "La URL debe empezar por https://. Data Effi no descarga hojas por http."
        )
    # `hostname` is lowercased and strips any user:password@ prefix, which is the
    # classic way to make a URL look like it points somewhere it does not.
    if parts.hostname != ALLOWED_HOST:
        raise InvalidSheetUrlError(
            "Solo se aceptan hojas de Google publicadas en la web "
            f"(https://{ALLOWED_HOST}/...). Revisa que copiaste la URL que te dio "
            "Archivo → Compartir → Publicar en la web → CSV."
        )
    if parts.username or parts.password:
        raise InvalidSheetUrlError("La URL no puede llevar usuario ni contraseña.")
    return url


def redact_url(url: str) -> str:
    """Scheme, host and path. Never the query string - it may carry a token."""
    parts = urlsplit(url)
    marker = "?***" if parts.query else ""
    return f"{parts.scheme}://{parts.hostname or '?'}{parts.path}{marker}"


class PublishedSheetFetcher:
    """Downloads one published Google Sheet as CSV bytes.

    Deliberately not a scraper: it requests the same public CSV URL the merchant
    can open in a browser, parses nothing, and hands the raw bytes straight to
    the ingestion pipeline.
    """

    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        min_interval_seconds: float = MIN_SECONDS_BETWEEN_REQUESTS,
        max_bytes: int = MAX_SHEET_BYTES,
    ) -> None:
        self._url = validate_published_url(url)
        self._timeout = timeout_seconds
        self._min_interval = min_interval_seconds
        self._max_bytes = max_bytes
        self._last_request_at: float = 0.0

    # -- construction ---------------------------------------------------
    @classmethod
    def from_connection(
        cls,
        *,
        source_url: str | None,
        secret_ref: str | None = None,
        **kwargs: Any,
    ) -> PublishedSheetFetcher:
        """Build from a connection row.

        `source_url` holds the published URL directly, because it is public
        (migration 013). `secret_ref` remains supported for the operator who
        prefers the usual rule and keeps the URL in an environment variable -
        the value still never leaves the server that way.
        """
        if source_url:
            return cls(url=source_url, **kwargs)

        if not secret_ref:
            raise SheetFetchError(
                "Esta conexión no tiene URL de la hoja. Pega la URL que te dio "
                "Archivo → Compartir → Publicar en la web → CSV."
            )

        url = os.environ.get(secret_ref, "")
        if not url:
            raise SheetFetchError(
                f"Falta la variable de entorno {secret_ref} con la URL de la hoja. "
                "Configúrala en el servidor; nunca en el código."
            )
        return cls(url=url, **kwargs)

    # -- fetching -------------------------------------------------------
    def fetch(self, kind: BatchKind) -> SheetFetchResult:
        """Download the sheet as raw bytes.

        The bytes go straight into IngestEngine.ingest() - same content_hash,
        same idempotence, same merge rules as a file a human uploads.
        """
        self._respect_rate_limit()
        payload = self._request()

        if not payload:
            raise SheetFetchError("La hoja publicada está vacía.")

        stamp = datetime.now(UTC)
        logger.info(
            "sheet fetch ok: url=%s kind=%s bytes=%d",
            redact_url(self._url), kind.value, len(payload),
        )
        return SheetFetchResult(
            filename=f"google_sheet_{kind.value}_{stamp:%Y%m%d_%H%M}.csv",
            payload=payload,
            kind=kind,
            fetched_at=stamp,
        )

    # -- internals ------------------------------------------------------
    def _request(self) -> bytes:
        try:
            import httpx
        except ImportError as exc:      # pragma: no cover - declared dependency
            raise SheetFetchError("Falta la dependencia httpx para leer Google Sheets") from exc

        headers = {"User-Agent": USER_AGENT, "Accept": "text/csv,text/plain"}

        try:
            # follow_redirects=False on purpose: a redirect is how a fetch that
            # started at an allowed host ends up somewhere else entirely.
            with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
                response = client.get(self._url, headers=headers)
        except Exception as exc:
            # The URL may carry a token; never let an exception carry it either.
            raise SheetFetchError(
                f"No se pudo contactar a Google Sheets: {type(exc).__name__}"
            ) from None

        if response.status_code in (401, 403):
            # Do not retry, do not rotate user agents, do not work around it.
            raise SheetNotPublishedError(
                "Google rechazó la descarga (HTTP "
                f"{response.status_code}). La hoja no está publicada en la web: "
                "abre Archivo → Compartir → Publicar en la web y elige CSV."
            )
        if response.status_code in (301, 302, 303, 307, 308):
            raise SheetNotPublishedError(
                "Google redirigió la petición, normalmente al login. La hoja no está "
                "publicada en la web."
            )
        if response.status_code == 404:
            raise SheetFetchError("Esa hoja ya no existe o cambió de dirección.")
        if response.status_code == 429:
            raise SheetFetchError(
                "Google pidió reducir el ritmo (HTTP 429). Se reintentará más tarde."
            )
        if response.status_code >= 400:
            raise SheetFetchError(f"Google respondió HTTP {response.status_code}")

        content_type = response.headers.get("content-type", "")
        payload = response.content

        if "text/html" in content_type or payload[:64].lstrip().lower().startswith(b"<"):
            # HTML where CSV was expected means we were handed a login page.
            raise SheetNotPublishedError(
                "Google devolvió una página web en vez de un CSV. Vuelve a publicar "
                "la hoja eligiendo el formato CSV."
            )
        if len(payload) > self._max_bytes:
            raise SheetFetchError(
                f"La hoja pesa {len(payload) / 1_048_576:.1f} MB y el máximo es "
                f"{self._max_bytes / 1_048_576:.0f} MB."
            )
        return payload

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def __repr__(self) -> str:      # pragma: no cover - defensive
        # Never let a repr in a log or a traceback expose a query string.
        return f"<PublishedSheetFetcher url={redact_url(self._url)!r}>"
