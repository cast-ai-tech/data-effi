"""Effi tier-3 connector: fetches the user's own reports using their session.

WHY THIS IS TIER 3
------------------
Effi publishes no API. The only way to get a merchant's own operational data out
of it is to ask for the same report export the merchant can download by hand, in
the browser, while logged in. That means Master Data acts on the user's behalf with the
user's credentials - a materially different thing from calling a documented API,
and it carries obligations that tier 1 and 2 do not.

THE RULES THIS MODULE ENFORCES
------------------------------
1. NO CONSENT, NO FETCH. Every call requires a `consent_granted_at` timestamp
   from core.connection. Without it this module raises and does nothing.
2. NO CREDENTIALS IN CODE. The session token is read from the environment
   variable named by the connection's `secret_ref`. It is never logged, never
   persisted, never included in an error message.
3. NO EVASION. Master Data identifies itself in the User-Agent, obeys a minimum delay
   between requests, and stops on the first 401/403 instead of retrying. If Effi
   does not want this traffic, the correct response is to stop - not to disguise
   it.
4. READ ONLY. This connector requests report exports. It never writes, never
   mutates an order, never touches anything but a download endpoint.

See docs/tier3-politica.md for the policy this implements, and read it before
enabling a tier-3 connection for a customer.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pipeline.models import BatchKind

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://effi.com.co"
DEFAULT_TIMEOUT_SECONDS = 60.0
MIN_SECONDS_BETWEEN_REQUESTS = 2.0
USER_AGENT = "MasterData-Analytics/1.0 (+operador autorizado por el comerciante)"

# Report paths per data kind. Overridable by environment so a change on Effi's
# side does not require a code deploy.
# Rutas reales, capturadas de una cuenta de Effi el 2026-08-26. El Effi de
# verdad no tiene un /reportes/*/export limpio: cada reporte es una vista del
# panel que se vuelca a Excel desde /app/<vista>/excel. El cuerpo vuelve como
# `application/vnd.ms-excel` (el .xls viejo, no xlsx).
#
# OJO - PENDIENTE: estas vistas no se filtran con `fecha_inicio/fecha_fin`. La
# captura mostró que guías usa `desde` (+ `vigente`) y movimientos usa
# `fecha_origen_desde`, y ambas llevan además una tanda de selectores de columna
# `c1..cN` cuyos VALORES la captura no trae (solo los nombres). Hasta tener esos
# valores, `fetch_report` arma el query con params que Effi ignora. Ver
# tools/effi-capture/README.md.
REPORT_PATHS: dict[BatchKind, str] = {
    BatchKind.SHIPMENTS: "/app/guia_transporte/excel",
    BatchKind.MOVEMENTS: "/app/movimiento_dinero_effi/excel",
}


class ConsentError(RuntimeError):
    """A tier-3 fetch was attempted without recorded consent. Always a bug."""


class SessionExpiredError(RuntimeError):
    """The stored session is no longer valid. The user must re-authorize."""


class FetchError(RuntimeError):
    """The fetch failed for a reason the caller should surface, not retry blindly."""


class TransientFetchError(FetchError):
    """Effi was unreachable, slow, overloaded or rate-limiting.

    Nothing is wrong with the connection itself, so the worker records the error
    and tries again on its NEXT scheduled run - it must not park the connection
    in `error`, which the worker never picks up again on its own.
    """


class PermissionDeniedError(FetchError):
    """The session is alive but may not read this report: a missing permission."""


@dataclass(slots=True)
class FetchResult:
    """A downloaded report, ready to hand to the same IngestEngine an upload uses."""

    filename: str
    payload: bytes
    kind: BatchKind
    fetched_at: datetime

    @property
    def size_bytes(self) -> int:
        return len(self.payload)


class EffiSessionFetcher:
    """Downloads report exports for one Effi connection.

    Deliberately not a scraper: it calls the export endpoint the merchant's own
    browser calls, parses nothing, and hands the raw bytes straight to the
    ingestion pipeline - the identical path a manual upload takes.
    """

    def __init__(
        self,
        *,
        session_token: str,
        consent_granted_at: datetime | None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        min_interval_seconds: float = MIN_SECONDS_BETWEEN_REQUESTS,
        browser_user_agent: str | None = None,
    ) -> None:
        if consent_granted_at is None:
            raise ConsentError(
                "Esta conexión no tiene consentimiento registrado. "
                "No se puede consultar Effi en nombre del usuario. "
                "Ver docs/tier3-politica.md"
            )
        if not session_token:
            raise FetchError("No hay sesión configurada para esta conexión de Effi")

        self._token = session_token
        # How the session travels. Mirrors EffiLoginContract.session_carrier:
        # a 'json' token goes as a bearer header, anything else as a Cookie.
        self._carrier = (os.environ.get("EFFI_SESSION_CARRIER", "").strip() or "cookie").lower()
        self._consent_granted_at = consent_granted_at
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._min_interval = min_interval_seconds
        self._last_request_at: float = 0.0
        # Migration 070: the User-Agent of the browser that made a session sent
        # by the extension. Only REPLAYED when the operator opts in - see
        # `_user_agent()` for why that is not the default.
        self._browser_user_agent = (browser_user_agent or "").strip() or None

    @property
    def base_url(self) -> str:
        """Where this connection points. Public so the preflight can build paths
        without reaching into a private attribute."""
        return self._base_url

    # -- construction ---------------------------------------------------
    @classmethod
    def from_env(
        cls,
        *,
        secret_ref: str,
        consent_granted_at: datetime | None,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> EffiSessionFetcher:
        """Build from the environment variable named by the connection's secret_ref.

        The database stores the NAME of the variable. The value never leaves the
        environment, so a database dump can never leak a customer's session.
        """
        if not secret_ref:
            raise FetchError("La conexión no declara secret_ref: no hay dónde buscar la sesión")

        token = os.environ.get(secret_ref, "")
        if not token:
            raise FetchError(
                f"Falta la variable de entorno {secret_ref} con la sesión de Effi. "
                "Configúrala en el servidor; nunca en el código."
            )

        return cls(
            session_token=token,
            consent_granted_at=consent_granted_at,
            base_url=base_url or os.environ.get("EFFI_BASE_URL") or DEFAULT_BASE_URL,
            **kwargs,
        )

    @classmethod
    def from_session(
        cls,
        session: Any,
        *,
        consent_granted_at: datetime | None,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> EffiSessionFetcher:
        """Build from a session the authenticator produced (migration 051).

        The env-var path above is still the safest place to keep a secret and is
        still preferred where an operator maintains it. This is the path that
        lets a merchant connect their own account without anyone touching the
        server - see pipeline/vault.py for the trade that makes.

        Both paths converge here: from this line on, a session fetched with a
        stored password and a session pasted into an env var are the same object
        under the same rules.
        """
        kwargs.setdefault("browser_user_agent", getattr(session, "user_agent", None))
        return cls(
            session_token=session.token,
            consent_granted_at=consent_granted_at,
            base_url=base_url or os.environ.get("EFFI_BASE_URL") or DEFAULT_BASE_URL,
            **kwargs,
        )

    # -- fetching -------------------------------------------------------
    def fetch_report(
        self,
        kind: BatchKind,
        *,
        date_from: date,
        date_to: date,
    ) -> FetchResult:
        """Download one report export as raw bytes.

        The bytes go straight into IngestEngine.ingest() - same content_hash,
        same idempotence, same merge rules as a file a human uploads. A tier-3
        fetch has no privileged path into the database.
        """
        if kind not in REPORT_PATHS:
            raise FetchError(f"Effi no expone un reporte para '{kind.value}'")
        if date_from > date_to:
            raise FetchError("El rango de fechas está invertido")

        path = os.environ.get(f"EFFI_PATH_{kind.name}", "").strip() or REPORT_PATHS[kind]
        url = f"{self._base_url}{path}"
        params = {
            "fecha_inicio": date_from.isoformat(),
            "fecha_fin": date_to.isoformat(),
            "formato": "xlsx",
        }

        self._respect_rate_limit()
        response = self._request(url, params)

        payload = response.content
        if not payload:
            raise FetchError("Effi devolvió un reporte vacío")

        content_type = response.headers.get("content-type", "")
        if _looks_like_login_page(payload) or (
            "text/html" in content_type and not _looks_like_html_table(payload)
        ):
            # A login form where a spreadsheet was expected means the session
            # bounced us to the login page. An HTML <table> is NOT that: Effi's
            # "Excel" exports are HTML tables, and the reader parses them.
            raise SessionExpiredError(
                "La sesión de Effi expiró. El usuario debe volver a autorizar la conexión."
            )

        extension = "xlsx" if "sheet" in content_type or "excel" in content_type else "csv"
        filename = f"effi_{kind.value}_{date_from:%Y%m%d}_{date_to:%Y%m%d}.{extension}"

        logger.info(
            "effi fetch ok: kind=%s range=%s..%s bytes=%d",
            kind.value, date_from, date_to, len(payload),
        )
        return FetchResult(
            filename=filename,
            payload=payload,
            kind=kind,
            fetched_at=datetime.now(UTC),
        )

    # -- preflight ------------------------------------------------------
    def probe(self, url: str, params: dict[str, str]) -> str:
        """Ask "may we read this?" and answer in one word, without downloading.

        Used only by connectors/effi/permissions.py to check a merchant's Effi
        role before a sync fails at 3am for a reason they could have fixed in two
        minutes. It differs from `fetch_report` in exactly two ways, both of them
        deliberate limits:

          - the range is one day, set by the caller, so a permission check never
            pulls a year of a merchant's data to answer a yes/no question;
          - the body is discarded. Nothing a probe reads is ever ingested.

        A 403 is `denied` rather than an exception because being denied is a
        NORMAL, expected result here - it is the answer the merchant asked for -
        while in `fetch_report` the same 403 is a genuine failure. Only 401 and a
        login redirect still raise, because those mean the session is gone and
        every further probe would report `denied` for the wrong reason.
        """
        self._respect_rate_limit()

        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise FetchError("Falta la dependencia httpx para el conector de Effi") from exc

        headers = self._headers()

        try:
            with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
                response = client.get(url, params=params, headers=headers)
        except Exception as exc:
            logger.info("effi probe unreachable: %s", type(exc).__name__)
            return "unreachable"

        if response.status_code == 401:
            raise SessionExpiredError(
                "Effi rechazó la sesión durante la comprobación de permisos."
            )
        if response.status_code in (301, 302, 303, 307, 308):
            raise SessionExpiredError(
                "Effi redirigió al login durante la comprobación de permisos."
            )
        if response.status_code == 403:
            return "denied"
        if response.status_code == 429:
            return "unreachable"
        if response.status_code >= 400:
            return "unreachable"

        body = response.content or b""
        if _looks_like_login_page(body):
            raise SessionExpiredError(
                "Effi devolvió la pantalla de entrada durante la comprobación de permisos."
            )
        # A 200 carrying HTML where a spreadsheet belongs is the panel rendering
        # its own "no tienes permiso" page with a success code - unless it is
        # the HTML table Effi uses as its "Excel" export.
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type and not _looks_like_html_table(body):
            return "denied"
        return "granted"

    # -- internals ------------------------------------------------------
    def _request(self, url: str, params: dict[str, str]) -> Any:
        try:
            import httpx
        except ImportError as exc:      # pragma: no cover - declared dependency
            raise FetchError("Falta la dependencia httpx para el conector de Effi") from exc

        headers = self._headers()

        try:
            with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
                response = client.get(url, params=params, headers=headers)
        except Exception as exc:
            # The token lives in `headers`; never let an exception carry it.
            raise TransientFetchError(
                f"No se pudo contactar a Effi: {type(exc).__name__}"
            ) from None

        if response.status_code == 401:
            # Do not retry, do not rotate user agents, do not work around it.
            raise SessionExpiredError(
                "Effi rechazó la sesión (HTTP 401). El usuario debe volver a autorizar."
            )
        if response.status_code == 403:
            # A live session that may not read THIS report: a missing permission
            # (the preflight reads a 403 the same way). Calling it an expired
            # session would send the merchant to retype a password that was fine.
            raise PermissionDeniedError(
                "Effi negó el acceso a este reporte (HTTP 403): al usuario conectado "
                "le falta un permiso. Pulsa «Probar conexión» para ver cuál."
            )
        if response.status_code in (301, 302, 303, 307, 308):
            raise SessionExpiredError(
                "Effi redirigió la petición, normalmente al login. La sesión ya no sirve."
            )
        if response.status_code == 429:
            raise TransientFetchError(
                "Effi pidió reducir el ritmo (HTTP 429). Se reintentará más tarde."
            )
        if response.status_code >= 500:
            raise TransientFetchError(
                f"Effi respondió HTTP {response.status_code}. Se reintentará en la "
                "siguiente sincronización."
            )
        if response.status_code >= 400:
            raise FetchError(f"Effi respondió HTTP {response.status_code}")

        return response

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": self._user_agent(),
            "Accept": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
                "application/vnd.ms-excel,text/csv"
            ),
        }
        if self._carrier == "json":
            headers["Authorization"] = f"Bearer {self._token}"
        else:
            headers["Cookie"] = self._token
        return headers

    def _user_agent(self) -> str:
        """Our honest User-Agent, unless the operator opted into replaying the browser's.

        Rule 3 above (NO EVASION) is why the default is our own name. The one
        exception is a session the merchant made in their own browser and sent
        with the extension: some CodeIgniter setups bind `ci_session` to the
        User-Agent that created it, and then the session only works if the same
        string comes back. That is the merchant's own browser session used for
        the merchant's own reports, not a disguise - but it is still a choice an
        operator makes on purpose (EFFI_REPLAY_BROWSER_USER_AGENT=true), after
        seeing Effi reject the session with our name on it.
        """
        replay = os.environ.get("EFFI_REPLAY_BROWSER_USER_AGENT", "").strip().lower()
        if self._browser_user_agent and replay in ("1", "true", "yes", "si", "sí"):
            return self._browser_user_agent
        return USER_AGENT

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def __repr__(self) -> str:      # pragma: no cover - defensive
        # Never let a repr in a log or a traceback expose the session token.
        return f"<EffiSessionFetcher base_url={self._base_url!r} token=***>"


def _head(payload: bytes, size: int = 65536) -> bytes:
    return payload[:size].lower()


def _looks_like_html_table(payload: bytes) -> bool:
    return b"<table" in _head(payload)


def _looks_like_login_page(payload: bytes) -> bool:
    """An HTML page with a password input: the login form, whatever the header says.

    Checked on the body because a panel can answer an expired session with a 200
    and the ORIGINAL content type. A spreadsheet never carries a password input.
    """
    head = _head(payload)
    if not head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return False
    return any(
        marker in head
        for marker in (b'type="password"', b"type='password'", b"type=password")
    )
