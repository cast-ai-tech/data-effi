"""HTTP client for Dropi's Integrations API.

WHAT IS KNOWN, AND FROM WHERE
-----------------------------
Dropi publishes no public API reference. What this module relies on comes from
Dropi's own integration documents, circulated to integrators and mirrored at:

  [A] "Integrations Core + Dropi" -
      https://es.scribd.com/document/804372978/Integrations-Core-Dropi-2
  [B] "Documentación de Dropi" -
      https://es.scribd.com/document/683043719/Documentacion-de-Dropi-docx-7

and from probing the hosts without credentials (2026-09-28):

  - Every request carries the integration key in a header named
    `dropi-integration-key` [A]. The same document also spells it
    `dropi-integracion-key` further down, so the name is a constant below.
  - Base URL: `https://api.dropi.co/integrations` (production) and
    `https://test-api.dropi.co/integrations` (test) [A].
  - `GET /orders/myorders` lists the account's orders. Parameters [A][B]:
    `result_number` (page size), `start` (where the page begins), `from` /
    `untill` (yes, two l's) as yyyy-mm-dd, `filter_date_by` ("FECHA DE CREADO"
    or "FECHA DE CAMBIO DE ESTATUS"), `orderBy`, `orderDirection`, `status`.
  - Responses are an envelope `{"isSuccess", "status", "message", "objects",
    "count"}` [B]. An unauthenticated call to `/integrations/orders/myorders`
    on api.dropi.co AND api.dropi.gt answers HTTP 401 with
    `{"isSuccess": false, "message": "Access denied", "status": 401}` (probed).
  - api.dropi.{co,mx,pe,cl,pa,ec,gt,cr} all resolve and answer (probed).

WHAT IS NOT KNOWN - every one of these is a named constant with a TODO:
  - whether every country serves the same `/integrations` path on its host;
  - whether `start` is a row offset or a page number;
  - the maximum `result_number` the server honours;
  - any rate limit (no headers seen, none documented).

THE RULES THIS MODULE ENFORCES
------------------------------
1. READ ONLY. Only GET. Dropi's API can create orders and generate guides; this
   client has no method that could.
2. THE TOKEN NEVER LEAVES. Not in a log line, not in an exception, not in a
   repr. An error carries the HTTP status and Dropi's `message` field, trimmed.
3. A 401/403 IS FINAL. It is not retried: the token is wrong or revoked and only
   a person can fix that. 429 and 5xx are retried with backoff, a bounded
   number of times, honouring Retry-After.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# -- verified ----------------------------------------------------------------
ORDERS_PATH = "/orders/myorders"
USER_AGENT = "MasterData-Analytics/1.0 (+lectura de ordenes con token del comerciante)"
DATE_FILTER_CREATED = "FECHA DE CREADO"

# -- to verify with a real account ---------------------------------------------
# TODO verificar con cuenta real: [A] escribe `dropi-integration-key` en la
# explicación y `dropi-integracion-key` en los ejemplos. Se usa la primera.
INTEGRATION_KEY_HEADER = "dropi-integration-key"

# TODO verificar con cuenta real: los hosts responden (sondeo 2026-09-28) y
# api.dropi.co y api.dropi.gt exponen /integrations/orders/myorders (401 sin
# token). Que los demás países usen el MISMO prefijo /integrations es una
# suposición.
DROPI_API_BASE_URLS: dict[str, str] = {
    "CO": "https://api.dropi.co/integrations",
    "MX": "https://api.dropi.mx/integrations",
    "PE": "https://api.dropi.pe/integrations",
    "CL": "https://api.dropi.cl/integrations",
    "PA": "https://api.dropi.pa/integrations",
    "EC": "https://api.dropi.ec/integrations",
    "GT": "https://api.dropi.gt/integrations",
    "CR": "https://api.dropi.cr/integrations",
}

# TODO verificar con cuenta real: tamaño máximo de página que acepta Dropi.
PAGE_SIZE = 100
# TODO verificar con cuenta real: [B] dice "Valor inicial desde el cual se
# quiere obtener los resultados" (ejemplo 1). Se asume desplazamiento por
# filas, como `startData` de productos en [A]. Si fuera número de página, el
# conteo `count` de la respuesta lo delata: ver OrdersPage.expected_total.
START_IS_ROW_OFFSET = True
FIRST_START = 0
# TODO verificar con cuenta real: no hay límite documentado ni cabeceras de
# límite. Un pedido cada medio segundo es conservador.
MIN_SECONDS_BETWEEN_REQUESTS = 0.5

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0
# A hard stop so one account can never keep the worker busy forever: 50 pages
# of 100 is 5,000 orders per window slice.
MAX_PAGES_PER_WINDOW = 50

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class DropiError(RuntimeError):
    """Something the caller should surface. The message is safe to show."""


class DropiAuthError(DropiError):
    """401/403: the token is wrong, revoked or from another country. Final."""


class DropiTransientError(DropiError):
    """Rate limit, 5xx or network after every retry. Try again next run."""


class DropiResponseError(DropiError):
    """Dropi answered, but not with the envelope this client understands."""


class UnsupportedCountryError(DropiError):
    """No known API host for this country."""


def base_url_for(country_code: str | None) -> str:
    code = (country_code or "").strip().upper()
    try:
        return DROPI_API_BASE_URLS[code]
    except KeyError:
        raise UnsupportedCountryError(
            f"Master Data todavía no conoce la dirección de la API de Dropi para "
            f"'{code or '?'}'. Países con API: {', '.join(sorted(DROPI_API_BASE_URLS))}."
        ) from None


@dataclass(slots=True)
class OrdersPage:
    orders: list[dict[str, Any]]
    # `count` from the envelope when Dropi sends it; None otherwise.
    expected_total: int | None = None


@dataclass(slots=True)
class FetchSummary:
    """What one window returned, for the job report and the cursor."""

    orders: list[dict[str, Any]] = field(default_factory=list)
    pages: int = 0
    expected_total: int | None = None
    truncated: bool = False

    @property
    def warning(self) -> str | None:
        if self.truncated:
            return (
                f"Se alcanzó el tope de {MAX_PAGES_PER_WINDOW} páginas en una ventana; "
                "la siguiente pasada sigue desde donde quedó el cursor."
            )
        if self.expected_total is not None and self.expected_total > len(self.orders):
            return (
                f"Dropi dijo que había {self.expected_total} órdenes y llegaron "
                f"{len(self.orders)}. Revisar la paginación con una cuenta real."
            )
        return None


class DropiClient:
    """Read-only client for one Dropi account in one country."""

    def __init__(
        self,
        *,
        token: str,
        country_code: str,
        base_url: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        min_interval_seconds: float = MIN_SECONDS_BETWEEN_REQUESTS,
        max_attempts: int = MAX_ATTEMPTS,
        page_size: int = PAGE_SIZE,
        transport: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        token = (token or "").strip()
        if not token:
            raise DropiAuthError("Falta el token de integración de Dropi.")
        self._token = token
        self._country = country_code.upper()
        self._base_url = (base_url or base_url_for(country_code)).rstrip("/")
        self._timeout = timeout_seconds
        self._min_interval = min_interval_seconds
        self._max_attempts = max(1, max_attempts)
        self._page_size = page_size
        self._transport = transport
        self._sleep = sleep
        self._last_request_at = 0.0

    def __repr__(self) -> str:
        return f"<DropiClient country={self._country} base={self._base_url} token=***>"

    # -- public ---------------------------------------------------------
    def test_connection(self) -> OrdersPage:
        """One page of one order. Proves the token and the host, nothing else."""
        return self._get_orders({"result_number": 1, "start": FIRST_START})

    def iter_order_pages(self, *, date_from: date, date_to: date) -> Iterator[OrdersPage]:
        """Every order CREATED in [date_from, date_to], page by page."""
        start = FIRST_START
        for page_number in range(MAX_PAGES_PER_WINDOW):
            params = {
                "result_number": self._page_size,
                "start": start,
                "from": date_from.isoformat(),
                "untill": date_to.isoformat(),   # sic: así lo escribe Dropi
                "filter_date_by": DATE_FILTER_CREATED,
                "orderBy": "id",
                "orderDirection": "asc",
            }
            page = self._get_orders(params)
            yield page
            if len(page.orders) < self._page_size:
                return
            start = start + len(page.orders) if START_IS_ROW_OFFSET else page_number + 2
        logger.warning(
            "dropi: tope de %d páginas alcanzado country=%s window=%s..%s",
            MAX_PAGES_PER_WINDOW, self._country, date_from, date_to,
        )

    def fetch_orders(self, *, date_from: date, date_to: date) -> FetchSummary:
        """All pages of one window, deduplicated by Dropi order id."""
        summary = FetchSummary()
        seen: set[str] = set()
        last_len = 0
        for page in self.iter_order_pages(date_from=date_from, date_to=date_to):
            summary.pages += 1
            last_len = len(page.orders)
            if page.expected_total is not None:
                summary.expected_total = page.expected_total
            new = 0
            for order in page.orders:
                key = str(order.get("id")) if order.get("id") is not None else None
                if key is not None and key in seen:
                    continue
                if key is not None:
                    seen.add(key)
                summary.orders.append(order)
                new += 1
            if page.orders and new == 0:
                # A full page of orders already seen means `start` is not moving
                # the way this client thinks. Stop instead of looping.
                logger.warning("dropi: página repetida; se detiene la paginación")
                break
        summary.truncated = summary.pages >= MAX_PAGES_PER_WINDOW and last_len >= self._page_size
        return summary

    # -- internals ------------------------------------------------------
    def _get_orders(self, params: dict[str, Any]) -> OrdersPage:
        body = self._request("GET", ORDERS_PATH, params=params)
        return parse_orders_envelope(body)

    def _request(self, method: str, path: str, *, params: dict[str, Any]) -> Any:
        import httpx

        headers = {
            INTEGRATION_KEY_HEADER: self._token,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        url = f"{self._base_url}{path}"
        last_problem = "sin respuesta"

        for attempt in range(1, self._max_attempts + 1):
            self._respect_rate_limit()
            try:
                with httpx.Client(
                    timeout=self._timeout, follow_redirects=False, transport=self._transport
                ) as client:
                    response = client.request(method, url, params=params, headers=headers)
            except httpx.TimeoutException:
                last_problem = "Dropi no respondió a tiempo"
                self._backoff(attempt, None)
                continue
            except httpx.TransportError as exc:
                # The exception text can carry the URL; only its type is kept.
                last_problem = f"no se pudo contactar a Dropi ({type(exc).__name__})"
                self._backoff(attempt, None)
                continue

            status = response.status_code
            if status in (401, 403):
                raise DropiAuthError(
                    f"Dropi rechazó el token de integración (HTTP {status}"
                    f"{_message_suffix(response)}). Genera uno nuevo en Dropi → "
                    "Integraciones y pégalo en Gestionar. Revisa también que sea de "
                    "la cuenta del país de esta conexión."
                )
            if status in RETRYABLE_STATUSES:
                last_problem = (
                    "Dropi pidió bajar el ritmo (HTTP 429)" if status == 429
                    else f"Dropi respondió HTTP {status}"
                )
                self._backoff(attempt, response.headers.get("retry-after"))
                continue
            if status in (301, 302, 303, 307, 308):
                raise DropiResponseError(
                    f"Dropi redirigió la petición (HTTP {status}). La dirección de la API "
                    "para este país puede haber cambiado."
                )
            if status == 404:
                raise DropiResponseError(
                    "La API de Dropi no tiene esa ruta en este país (HTTP 404)."
                )
            if status >= 400:
                raise DropiResponseError(
                    f"Dropi respondió HTTP {status}{_message_suffix(response)}."
                )
            try:
                return response.json()
            except ValueError:
                raise DropiResponseError(
                    "Dropi respondió algo que no es JSON. La API pudo haber cambiado."
                ) from None

        raise DropiTransientError(
            f"{last_problem} después de {self._max_attempts} intentos. "
            "Se reintentará en la próxima sincronización."
        )

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if attempt >= self._max_attempts:
            return
        wait = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        if retry_after:
            try:
                wait = max(wait, float(retry_after))
            except ValueError:
                pass
        self._sleep(min(wait, MAX_BACKOFF_SECONDS))

    def _respect_rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_at
        if self._last_request_at and elapsed < self._min_interval:
            self._sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()


def parse_orders_envelope(body: Any) -> OrdersPage:
    """`{"isSuccess": true, "objects": [...], "count": n}` -> OrdersPage."""
    if isinstance(body, list):
        # TODO verificar con cuenta real: [B] siempre muestra el sobre; una
        # lista desnuda se acepta por si acaso.
        return OrdersPage(orders=[o for o in body if isinstance(o, dict)])
    if not isinstance(body, dict):
        raise DropiResponseError("Dropi respondió con un formato inesperado.")

    if body.get("isSuccess") is False:
        message = _clean_message(body.get("message"))
        raise DropiResponseError(
            f"Dropi rechazó la consulta{': ' + message if message else ''}."
        )

    objects = body.get("objects")
    if objects is None:
        objects = []
    if isinstance(objects, dict):
        # Some Laravel paginators nest the list one level down.
        objects = objects.get("data") or []
    if not isinstance(objects, list):
        raise DropiResponseError("Dropi respondió sin la lista de órdenes (`objects`).")

    count = body.get("count")
    try:
        expected = int(count) if count is not None else None
    except (TypeError, ValueError):
        expected = None
    return OrdersPage(orders=[o for o in objects if isinstance(o, dict)], expected_total=expected)


def _clean_message(raw: Any) -> str:
    text = " ".join(str(raw or "").split())
    return text[:160]


def _message_suffix(response: Any) -> str:
    try:
        message = _clean_message(response.json().get("message"))
    except Exception:
        return ""
    return f": {message}" if message else ""
