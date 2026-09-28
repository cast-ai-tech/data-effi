"""Uniform error shape.

Every failure the API returns looks the same:

    {"error": {"code": "forbidden", "message": "...", "detail": {...}}}

The message is written for a person to read, in Spanish, because it is shown in
the UI. The code is for the client to branch on.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Raise this anywhere; the handler turns it into the standard envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}


class NotFound(ApiError):
    def __init__(self, message: str = "No se encontró el recurso", **kwargs: Any) -> None:
        super().__init__("not_found", message, status_code=status.HTTP_404_NOT_FOUND, **kwargs)


class Unauthorized(ApiError):
    def __init__(self, message: str = "Necesitas iniciar sesión", **kwargs: Any) -> None:
        super().__init__("unauthorized", message, status_code=status.HTTP_401_UNAUTHORIZED, **kwargs)


class Forbidden(ApiError):
    def __init__(self, message: str = "Tu rol no permite esta acción", **kwargs: Any) -> None:
        super().__init__("forbidden", message, status_code=status.HTTP_403_FORBIDDEN, **kwargs)


class PaymentRequired(ApiError):
    """The free month ended, or the plan does not allow this (migration 048)."""

    def __init__(self, message: str, *, code: str = "subscription_required", **kwargs: Any) -> None:
        super().__init__(code, message, status_code=status.HTTP_402_PAYMENT_REQUIRED, **kwargs)


class Conflict(ApiError):
    def __init__(self, message: str = "El recurso ya existe", **kwargs: Any) -> None:
        super().__init__("conflict", message, status_code=status.HTTP_409_CONFLICT, **kwargs)


class InvalidDateRange(ApiError):
    """A range whose end is before its start.

    422 rather than 400, and its own code rather than the generic validation
    envelope, because the two dates are individually valid - it is the pair that
    is wrong, and the client needs to tell the two cases apart to point at the
    right field.
    """

    def __init__(self, message: str) -> None:
        # Starlette renamed the 422 constant; the numeric code is unchanged.
        super().__init__("invalid_date_range", message, status_code=422)


class InvalidDateField(ApiError):
    """A `date_field` that names no date this platform has.

    Its own code, separate from `invalid_date_range`, because the fix is a
    different one: the caller picked an impossible value, not an impossible
    pair.
    """

    def __init__(self, message: str) -> None:
        super().__init__("invalid_date_field", message, status_code=422)


class InvalidPlatform(ApiError):
    """A `platform` that is not in the catalogue.

    Its own code so the interface can say "esa plataforma no existe" instead of
    "campos inválidos", and so a typo never silently widens to "todas".
    """

    def __init__(self, message: str) -> None:
        super().__init__("invalid_platform", message, status_code=422)


class RateLimited(ApiError):
    def __init__(
        self, message: str = "Demasiados intentos. Espera un minuto e inténtalo de nuevo."
    ) -> None:
        super().__init__(
            "rate_limited", message, status_code=status.HTTP_429_TOO_MANY_REQUESTS
        )


class PayloadTooLarge(ApiError):
    def __init__(self, message: str) -> None:
        super().__init__(
            "payload_too_large", message, status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        )


def _envelope(code: str, message: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "detail": detail or {}}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": ".".join(str(p) for p in err.get("loc", [])[1:]), "reason": err.get("msg")}
            for err in exc.errors()
        ]
        return JSONResponse(
            # Starlette renamed this constant; the numeric code is unchanged.
            status_code=422,
            content=_envelope(
                "validation_error",
                "Revisa los datos enviados: hay campos inválidos.",
                {"fields": fields},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(codes.get(exc.status_code, "http_error"), str(exc.detail)),
        )

    import psycopg

    @app.exception_handler(psycopg.errors.UniqueViolation)
    async def handle_unique_violation(request: Request, exc: Exception) -> JSONResponse:
        # Dos peticiones que crean lo mismo a la vez (dos registros con el mismo
        # correo, dos clics en "aceptar invitación") pasan las dos el SELECT
        # previo y la segunda choca con el índice único. Eso es un conflicto,
        # no "algo falló de nuestro lado": 409, sin nombres de índice ni SQL.
        logger.info("unique violation on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_envelope(
                "conflict",
                "Eso ya existe (quizá lo acabas de crear en otra pestaña). Recarga la página.",
            ),
        )

    from psycopg_pool import PoolTimeout

    @app.exception_handler(PoolTimeout)
    async def handle_pool_timeout(request: Request, exc: Exception) -> JSONResponse:
        # Todas las conexiones a la base están ocupadas. No es un error de la
        # petición ni un fallo: es saturación, y pasa en segundos. 503 con
        # Retry-After para que el cliente reintente en vez de mostrar "falló".
        logger.warning("pool exhausted on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "5"},
            content=_envelope(
                "service_busy",
                "Hay mucha gente consultando en este momento. Vuelve a intentarlo en "
                "unos segundos.",
            ),
        )

    @app.exception_handler(psycopg.errors.QueryCanceled)
    async def handle_query_canceled(request: Request, exc: Exception) -> JSONResponse:
        # La consulta superó el tope (DB_STATEMENT_TIMEOUT_MS). Casi siempre es
        # un rango de fechas muy grande; se dice eso en vez de un 500 genérico.
        logger.warning("statement timeout on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "10"},
            content=_envelope(
                "query_timeout",
                "La consulta tardó demasiado. Prueba con un rango de fechas más corto o "
                "vuelve a intentarlo en un momento.",
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Log the real cause; never leak internals (or a DSN) to the client.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(
                "internal_error",
                "Algo falló de nuestro lado. El equipo ya tiene el detalle en los logs.",
            ),
        )
