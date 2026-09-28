"""Lo que un intento fallido deja escrito, aunque la petición termine en error.

Sin base de datos: se sustituye `connection` por una que registra si se
confirmó o se deshizo. El punto que se fija es de transacciones, no de SQL.

EL BUG QUE ESTO IMPIDE QUE VUELVA
El límite por IP y el `login_failed` se escribían en la conexión de la propia
petición. Un 401 deshace esa transacción, y con ella el golpe al contador y la
fila de auditoría: la fuerza bruta contra `/auth/login` no tenía tope y no
dejaba rastro.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi import Request  # noqa: E402

import api.deps as deps  # noqa: E402
import api.routers.auth as auth  # noqa: E402
from api.errors import RateLimited, Unauthorized  # noqa: E402
from api.schemas import LoginRequest  # noqa: E402


class _Conn:
    def __init__(self, name: str) -> None:
        self.name = name
        self.committed = False


def _fake_connection(opened: list[_Conn]):
    @contextmanager
    def connection(*_args, **_kwargs):
        conn = _Conn(f"own-{len(opened)}")
        opened.append(conn)
        yield conn
        conn.committed = True       # solo si el bloque terminó sin excepción

    return connection


def _request() -> Request:
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("10.0.0.9", 1)}
    )


_SETTINGS = SimpleNamespace(
    rate_limit_auth_per_minute=10, proxy_shared_secret=None, trust_proxy_headers=True
)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(deps, "get_settings", lambda: _SETTINGS)


def test_el_limite_no_comparte_la_conexion_del_endpoint():
    """Si pidiera `UnscopedDbDep`, FastAPI le daría la misma conexión que al login."""
    dependency = deps.rate_limit("auth", "rate_limit_auth_per_minute")
    assert "conn" not in inspect.signature(dependency).parameters


def test_el_golpe_al_contador_queda_confirmado_antes_del_endpoint(monkeypatch):
    opened: list[_Conn] = []
    hits: list[str] = []
    monkeypatch.setattr(deps, "connection", _fake_connection(opened))

    def check(conn, **_kw):
        hits.append(conn.name)
        return True

    monkeypatch.setattr(deps, "check_rate_limit", check)

    deps.rate_limit("auth", "rate_limit_auth_per_minute")(_request(), _SETTINGS)

    assert hits == ["own-0"]
    assert opened[0].committed, "el contador tiene que confirmarse en su propia transacción"


def test_pasado_el_limite_responde_429(monkeypatch):
    monkeypatch.setattr(deps, "connection", _fake_connection([]))
    monkeypatch.setattr(deps, "check_rate_limit", lambda conn, **_kw: False)

    with pytest.raises(RateLimited):
        deps.rate_limit("auth", "rate_limit_auth_per_minute")(_request(), _SETTINGS)


def test_un_login_fallido_queda_auditado_fuera_de_la_transaccion_que_se_deshace(monkeypatch):
    opened: list[_Conn] = []
    recorded: list[tuple[str, str]] = []
    request_conn = _Conn("request")

    monkeypatch.setattr(auth, "connection", _fake_connection(opened))
    monkeypatch.setattr(auth, "fetch_one", lambda *_a, **_k: None)     # no existe la cuenta
    monkeypatch.setattr(
        auth, "_record_auth_event",
        lambda conn, *, event, **_kw: recorded.append((conn.name, event)),
    )

    with pytest.raises(Unauthorized):
        auth.login(
            LoginRequest(email="nadie@example.com", password="x"),
            _request(), request_conn, SimpleNamespace(),
        )

    assert recorded == [("own-0", "login_failed")]
    assert opened[0].committed


def test_si_la_auditoria_falla_la_persona_recibe_igual_su_401(monkeypatch):
    @contextmanager
    def broken(*_a, **_k):
        raise RuntimeError("base caída")
        yield  # pragma: no cover

    monkeypatch.setattr(auth, "connection", broken)
    monkeypatch.setattr(auth, "fetch_one", lambda *_a, **_k: None)

    with pytest.raises(Unauthorized):
        auth.login(
            LoginRequest(email="nadie@example.com", password="x"),
            _request(), _Conn("request"), SimpleNamespace(),
        )
