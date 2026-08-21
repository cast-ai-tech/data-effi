"""Data Effi API.

Boots only when the environment is complete, and says exactly what is missing
when it is not. Everything past /health and /auth/login requires a token.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.db import close_pools, healthcheck, init_pools
from api.errors import register_error_handlers
from api.ingest_queue import get_queue, init_queue
from api.routers import ai, auth, config, customers, ingest, kpis, orders, products, worker
from api.settings import get_settings

logger = logging.getLogger(__name__)

DESCRIPTION = """
API de **Data Effi**, plataforma de analítica para operaciones de ecommerce
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

    logger.info("Data Effi API lista en %s:%s", settings.api_host, settings.api_port)
    try:
        yield
    finally:
        await get_queue().drain()
        close_pools()
        logger.info("Data Effi API detenida limpiamente")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Data Effi API",
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
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

    app.include_router(auth.router)
    app.include_router(config.router)
    app.include_router(ingest.router)
    app.include_router(kpis.router)
    app.include_router(products.router)
    app.include_router(orders.router)
    app.include_router(customers.router)
    app.include_router(ai.router)
    app.include_router(worker.router)

    return app


app = create_app()
