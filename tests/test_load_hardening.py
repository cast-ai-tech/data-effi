"""Lo que arregló la prueba de carga (scripts/loadtest.py), fijado para que no vuelva.

- La ingesta graba por tandas y el resultado es el mismo que en una sola.
- Pool lleno y consulta que supera el tope responden 503 claros, no 500.
- /health no declara caído un servicio que solo está ocupado.
"""

from __future__ import annotations

from contextlib import contextmanager

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

import pipeline.ingest as ingest_module
from api import db
from api.errors import register_error_handlers
from pipeline.ingest import IngestEngine, MemoryStore
from pipeline.models import BatchKind
from tests.conftest import CONNECTION_ID, COUNTRY, CURRENCY, PLATFORM, TENANT_ID, TODAY


def _load(payload: bytes, pii_salt: str) -> tuple[MemoryStore, dict]:
    store = MemoryStore()
    report = IngestEngine(store, pii_salt=pii_salt, today=TODAY).ingest(
        payload=payload, source_name="guias.csv", kind=BatchKind.SHIPMENTS,
        tenant_id=TENANT_ID, connection_id=CONNECTION_ID, country_code=COUNTRY,
        platform_code=PLATFORM, default_currency=CURRENCY,
    )
    counts = {
        "total": report.rows_total,
        "inserted": report.rows_inserted,
        "updated": report.rows_updated,
        "skipped": report.rows_skipped,
        "failed": report.rows_failed,
        "stored": report.rows_stored,
    }
    return store, counts


def test_ingesta_por_tandas_da_el_mismo_resultado(monkeypatch, guias_dia1, pii_salt):
    whole_store, whole = _load(guias_dia1, pii_salt)

    # Tandas de 2 filas: cada guía cae en una escritura distinta.
    monkeypatch.setattr(ingest_module, "FLUSH_ROWS", 2)
    chunked_store, chunked = _load(guias_dia1, pii_salt)

    assert whole["total"] > 2
    assert chunked == whole
    assert set(chunked_store.shipments) == set(whole_store.shipments)
    for key, record in whole_store.shipments.items():
        assert chunked_store.shipments[key].status_code == record.status_code
        assert chunked_store.shipments[key].declared_value == record.declared_value
    assert sum(len(rows) for rows in chunked_store.source_rows.values()) == whole["stored"]


def _app_raising(exc: Exception) -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def test_pool_lleno_responde_503_con_reintento():
    response = _app_raising(PoolTimeout("couldn't get a connection after 10.00 sec")).get("/boom")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["error"]["code"] == "service_busy"


def test_consulta_demasiado_lenta_responde_503_claro():
    response = _app_raising(psycopg.errors.QueryCanceled("canceling statement")).get("/boom")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "query_timeout"


def test_health_con_pool_ocupado_no_es_caida(monkeypatch):
    class BusyPool:
        @contextmanager
        def connection(self, timeout=None):
            raise PoolTimeout("all connections in use")
            yield  # pragma: no cover

    monkeypatch.setattr(db, "get_pool", lambda: BusyPool())
    payload = db.healthcheck()
    assert payload["status"] == "ok"
    assert payload["database"] == "busy"
