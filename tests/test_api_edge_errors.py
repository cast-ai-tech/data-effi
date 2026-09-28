"""Bordes que respondían 500 o el código equivocado. Sin base de datos."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

import psycopg  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.errors import register_error_handlers  # noqa: E402
from api.security import constant_time_equals  # noqa: E402


def test_un_choque_con_un_indice_unico_es_409_y_no_500():
    """Dos registros simultáneos con el mismo correo pasan los dos el SELECT."""
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/crear")
    def crear() -> None:
        raise psycopg.errors.UniqueViolation(
            'duplicate key value violates unique constraint "ux_app_user_email_lower"'
        )

    response = TestClient(app, raise_server_exceptions=False).post("/crear")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "conflict"
    # Ni el nombre del índice ni el SQL llegan al cliente.
    assert "ux_app_user" not in response.text


def test_un_secreto_con_acentos_se_rechaza_sin_reventar():
    """`compare_digest` con str no ASCII lanza TypeError: un 401 se volvía 500."""
    assert constant_time_equals("contraseña-ñ", "otra-cosa") is False
    assert constant_time_equals("clave-é", "clave-é") is True


def test_un_job_del_worker_que_falla_es_500(monkeypatch):
    import worker.main
    from api.routers import worker as worker_router
    from api.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/none")
    monkeypatch.setenv("JWT_SECRET", "j" * 48)
    monkeypatch.setenv("PII_HASH_SALT", "s" * 48)
    monkeypatch.setenv("WORKER_TRIGGER_SECRET", "w" * 48)
    get_settings.cache_clear()

    def explota(_job):
        raise RuntimeError("proveedor caído")

    monkeypatch.setattr(worker.main, "run_named_job", explota)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(worker_router.router)
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/worker/trigger/refresh_fx", headers={"X-Worker-Secret": "w" * 48}
        )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "job_failed"


def test_un_codigo_de_captura_se_gasta_de_forma_atomica():
    """Dos envíos a la vez con un código de un solo uso: solo uno puede ganar.

    `_valid_token` mira `uses < max_uses` en otra transacción, así que la
    condición tiene que repetirse en el mismo UPDATE que gasta el uso.
    """
    from pathlib import Path

    cuerpo = Path("api/routers/captures.py").read_text(encoding="utf-8")
    cuerpo = cuerpo.split("def submit_capture", 1)[1].split("\ndef ", 1)[0]
    assert "WHERE id = %s AND uses < max_uses" in cuerpo
    assert cuerpo.find("uses = uses + 1") < cuerpo.find("INSERT INTO raw.capture"), (
        "el uso se gasta antes de guardar, para que el perdedor no guarde nada"
    )
