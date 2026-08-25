"""Master Data API.

Boots only when the environment is complete, and says exactly what is missing
when it is not. Everything past /health and /auth/login requires a token.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.db import close_pools, healthcheck, init_pools
from api.deps import require_any_cap, require_cap
from api.errors import register_error_handlers
from api.ingest_queue import get_queue, init_queue
from api.routers import (
    ai,
    auth,
    billing,
    config,
    customers,
    events,
    ingest,
    kpis,
    notifications,
    orders,
    org,
    products,
    worker,
)
from api.settings import get_settings

logger = logging.getLogger(__name__)

DESCRIPTION = """
API de **Master Data**, plataforma de analítica para operaciones de ecommerce
contraentrega (COD) multi-país en LATAM.

Todos los KPI se leen de vistas `mart.*`; la API no calcula métricas. El
aislamiento entre clientes se hace por `tenant_id` del JWT **y** por la variable
de sesión `norte.tenant_id`, que hace que las vistas devuelvan cero filas si no
está puesta.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    init_pools(settings)
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)

    queue = init_queue(settings)
    await queue.recover_pending()

    logger.info("Master Data API lista en %s:%s", settings.api_host, settings.api_port)
    try:
        yield
    finally:
        await get_queue().drain()
        close_pools()
        logger.info("Master Data API detenida limpiamente")


def create_app() -> FastAPI:
    settings = get_settings()

    # The interactive docs map every route, parameter and error code of the
    # API. Useful on a laptop; a free reconnaissance report on the internet.
    production = settings.environment == "production"
    app = FastAPI(
        title="Master Data API",
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    register_error_handlers(app)

    @app.get("/health", tags=["system"], summary="Estado del servicio")
    def health() -> JSONResponse:
        payload = healthcheck()
        return JSONResponse(status_code=200 if payload["status"] == "ok" else 503, content=payload)

    # Authorisation by surface, not by endpoint.
    #
    # A role is mounted once here instead of being repeated on ~60 handlers,
    # which is what makes `uploader` safe to add: a person who may only upload
    # files is refused at the door of every reading surface, including endpoints
    # written after this line. `read` also covers configuration READS - the
    # writes inside that router keep their own owner/analyst guards.
    read_only = [Depends(require_cap("read"))]

    app.include_router(auth.router)
    app.include_router(org.router)
    app.include_router(billing.router)
    # Not read-only: picking a country and a connection is part of uploading a
    # file, so the one screen an `uploader` may use needs this router too.
    app.include_router(
        config.router, dependencies=[Depends(require_any_cap("read", "ingest"))]
    )
    # NOT mounted with a router-wide `ingest` guard, unlike the surfaces above.
    # `POST /ingest/webhook/{token}` lives in this router and authenticates with
    # the token in its own path - it is called by n8n, Make and Zapier, which
    # hold no session and never send a bearer token. A guard on the router
    # answers every one of those calls with 401 "Falta el token de acceso", so
    # the capability is asserted on each human-facing endpoint instead. See
    # `ingest_guard` in api/routers/ingest.py.
    app.include_router(ingest.router)
    app.include_router(kpis.router, dependencies=read_only)
    app.include_router(products.router, dependencies=read_only)
    app.include_router(orders.router, dependencies=read_only)
    app.include_router(customers.router, dependencies=read_only)
    app.include_router(ai.router, dependencies=read_only)
    # Same reasoning as `config`: an uploader watches their own loads through
    # the event feed, and the feed itself cuts them to `upload_job.*` events.
    # Notifications share the guard so the bell renders for every role; the
    # rows an uploader can reach are the ones the digest and the loads write.
    app.include_router(
        events.router, dependencies=[Depends(require_any_cap("read", "ingest"))]
    )
    app.include_router(
        notifications.router, dependencies=[Depends(require_any_cap("read", "ingest"))]
    )
    app.include_router(worker.router)

    return app


app = create_app()
