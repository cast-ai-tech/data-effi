"""Conectar Effi con la extensión: el código, el canje y lo que nunca se filtra.

Sin base de datos: una tabla de códigos en memoria responde a las mismas
sentencias que el router manda, así que lo que se prueba es la lógica del
canje (caduca, se usa una vez, no cruza de empresa, respeta el límite) y no
Postgres. Lo que depende del motor - el trigger de tenant, el CHECK de
`auth_mode`, RLS - se vigila leyendo la migración 070.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from api import credentials
from api.errors import ApiError, NotFound
from api.routers import effi_pairing as ep
from api.schemas import ConnectionPreflightResponse, EffiPairingRedeemRequest

COOKIE_VALUE = "a1b2c3d4e5f6secretodesesion0123456789"


class _World:
    """Códigos y conexiones en memoria, y todo lo que el canje escribe."""

    def __init__(self):
        self.pairings: dict[str, dict] = {}
        self.connections: dict = {}
        self.stored: list[dict] = []
        self.outcomes: list[tuple] = []
        self.sql: list[str] = []
        self.rate_allowed = True
        self.rate_checks = 0
        self.claims = 0
        self.preflight_status = "ok"

    def add_connection(self, tenant_id=None, name="Effi Colombia"):
        cid, tid = uuid4(), tenant_id or uuid4()
        self.connections[cid] = {"tenant_id": tid, "name": name}
        return cid, tid

    def add_pairing(self, connection_id, *, expires_in=timedelta(minutes=10), revoked=False):
        code = ep.generate_code()
        row = {
            "id": uuid4(),
            "tenant_id": self.connections[connection_id]["tenant_id"],
            "connection_id": connection_id,
            "created_by": uuid4(),
            "created_at": datetime.now(UTC),
            "code_hash": ep.hash_code(code),
            "expires_at": datetime.now(UTC) + expires_in,
            "redeemed_at": None,
            "revoked_at": datetime.now(UTC) if revoked else None,
        }
        self.pairings[row["code_hash"]] = row
        return code, row

    # -- what the router calls ------------------------------------------
    def fetch_one(self, conn, sql, params=None):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        if "raw.register_rate_limit_hit" in flat:
            return {"allowed": self.rate_allowed}
        if flat.startswith("UPDATE core.connection_pairing SET redeemed_at"):
            self.claims += 1
            _ip, _ua, code_hash = params
            row = self.pairings.get(code_hash)
            assert "redeemed_at IS NULL" in flat and "revoked_at IS NULL" in flat
            assert "expires_at > now()" in flat
            if (
                row is None or row["redeemed_at"] is not None
                or row["revoked_at"] is not None
                or row["expires_at"] <= datetime.now(UTC)
            ):
                return None
            row["redeemed_at"] = datetime.now(UTC)
            return dict(row)
        if "FROM core.connection c JOIN core.platform p" in flat:
            cid, tid = params
            found = self.connections.get(cid)
            if found is None or found["tenant_id"] != tid:
                return None
            return {"id": cid, "name": found["name"], "platform_code": "effi",
                    "platform_name": "Effi"}
        raise AssertionError(f"SQL inesperado: {flat}")

    def execute(self, conn, sql, params=None):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        if flat.startswith("UPDATE core.connection_pairing SET outcome"):
            self.outcomes.append(params)
        return 1


@pytest.fixture
def world(monkeypatch):
    w = _World()

    class _Conn:
        def commit(self):
            pass

        def rollback(self):
            pass

    @contextmanager
    def _connection(*_a, **_k):
        yield _Conn()

    def _rate(conn, **k):
        w.rate_checks += 1
        return w.rate_allowed

    def _store(conn, **k):
        w.stored.append(k)

    def _preflight(conn, **k):
        return ConnectionPreflightResponse(
            connection_id=k["connection_id"],
            credential_status=w.preflight_status,
            is_usable=w.preflight_status == "ok",
            summary="resumen",
            permissions=[],
        )

    monkeypatch.setattr(ep, "connection", _connection)
    monkeypatch.setattr(ep, "fetch_one", w.fetch_one)
    monkeypatch.setattr(ep, "execute", w.execute)
    monkeypatch.setattr(ep, "check_rate_limit", _rate)
    monkeypatch.setattr(ep, "client_ip", lambda request: "203.0.113.9")
    monkeypatch.setattr(ep, "client_ip_inet", lambda request: "203.0.113.9")
    monkeypatch.setattr(ep, "run_preflight_for_connection", _preflight)
    monkeypatch.setattr(credentials, "store_browser_session", _store)
    monkeypatch.delenv("EFFI_SESSION_COOKIE", raising=False)
    monkeypatch.delenv("EFFI_EXTRA_SESSION_COOKIES", raising=False)
    return w


SETTINGS = SimpleNamespace(rate_limit_effi_pairing_per_minute=10)


def _payload(code, **extra):
    body = {
        "code": code,
        "cookies": [
            {"name": "ci_session", "value": COOKIE_VALUE},
            {"name": "_ga", "value": "GA1.2.analitica"},
        ],
        "user_agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/140 OPR/125",
    }
    body.update(extra)
    return EffiPairingRedeemRequest.model_validate(body)


def _redeem(code, **extra):
    return ep.redeem_pairing(_payload(code, **extra), request=None, settings=SETTINGS)


# -- el código ------------------------------------------------------------------


def test_el_codigo_se_lee_y_se_teclea():
    code = ep.generate_code()
    assert len(code) == 14 and code.count("-") == 2
    for ch in "01ILOU":
        assert ch not in code.replace("-", "")


def test_con_o_sin_guiones_es_el_mismo_codigo():
    code = ep.generate_code()
    loose = code.replace("-", " ").lower()
    assert ep.hash_code(code) == ep.hash_code(loose)
    assert ep.normalize_code("nada") is None
    assert ep.normalize_code("AAAA-AAAA-AAA1") is None  # '1' no está en el alfabeto


def test_la_base_guarda_el_hash_nunca_el_codigo():
    code = ep.generate_code()
    digest = ep.hash_code(code)
    assert len(digest) == 64 and code.replace("-", "") not in digest


# -- el canje ---------------------------------------------------------------------


def test_un_codigo_valido_guarda_la_sesion_en_su_conexion(world):
    cid, tid = world.add_connection()
    code, _ = world.add_pairing(cid)

    result = _redeem(code)

    assert result.connected is True
    assert result.credential_status == "ok"
    assert len(world.stored) == 1
    stored = world.stored[0]
    assert stored["connection_id"] == cid and stored["tenant_id"] == tid
    # Solo la cookie de sesión: la de analítica se queda en el navegador.
    assert stored["token"] == f"ci_session={COOKIE_VALUE}"
    assert stored["user_agent"].startswith("Mozilla/5.0")
    assert world.outcomes and world.outcomes[-1][0] == "connected"


def test_un_codigo_se_usa_una_sola_vez(world):
    cid, _ = world.add_connection()
    code, _ = world.add_pairing(cid)

    _redeem(code)
    with pytest.raises(NotFound):
        _redeem(code)

    assert len(world.stored) == 1, "El segundo canje volvió a escribir la sesión"


def test_un_codigo_caducado_no_sirve(world):
    cid, _ = world.add_connection()
    code, _ = world.add_pairing(cid, expires_in=timedelta(seconds=-1))

    with pytest.raises(NotFound):
        _redeem(code)
    assert not world.stored


def test_un_codigo_reemplazado_no_sirve(world):
    cid, _ = world.add_connection()
    code, _ = world.add_pairing(cid, revoked=True)

    with pytest.raises(NotFound):
        _redeem(code)
    assert not world.stored


def test_un_codigo_equivocado_no_sirve_y_no_dice_por_que(world):
    cid, _ = world.add_connection()
    world.add_pairing(cid)

    with pytest.raises(NotFound) as wrong:
        _redeem(ep.generate_code())
    with pytest.raises(NotFound) as garbage:
        _redeem("no-es-un-codigo")

    assert wrong.value.message == garbage.value.message == ep.NOT_USABLE_CODE
    assert not world.stored


def test_el_limite_va_antes_que_el_codigo_y_no_lo_gasta(world):
    cid, _ = world.add_connection()
    code, row = world.add_pairing(cid)
    world.rate_allowed = False

    with pytest.raises(ApiError) as exc:
        _redeem(code)

    assert exc.value.status_code == 429
    assert world.claims == 0, "Con el límite agotado no se debe ni mirar el código"
    assert row["redeemed_at"] is None


def test_sin_cookie_de_sesion_no_se_gasta_el_codigo(world):
    cid, _ = world.add_connection()
    code, row = world.add_pairing(cid)

    with pytest.raises(ApiError) as exc:
        _redeem(code, cookies=[{"name": "_ga", "value": "GA1.2.x"}])

    assert exc.value.code == "session_cookie_missing"
    assert row["redeemed_at"] is None, "Faltó la sesión y aun así se quemó el código"
    assert world.claims == 0


def test_una_cookie_ya_vencida_no_se_guarda(world):
    cid, _ = world.add_connection()
    code, row = world.add_pairing(cid)
    ayer = (datetime.now(UTC) - timedelta(days=1)).timestamp()

    with pytest.raises(ApiError) as exc:
        _redeem(code, cookies=[{"name": "ci_session", "value": COOKIE_VALUE,
                                "expires_at": ayer}])
    assert exc.value.code == "session_cookie_expired"
    assert row["redeemed_at"] is None


def test_la_fecha_de_la_cookie_tiene_tope(world):
    cid, _ = world.add_connection()
    code, _ = world.add_pairing(cid)
    en_un_ano = (datetime.now(UTC) + timedelta(days=365)).timestamp()

    _redeem(code, cookies=[{"name": "ci_session", "value": COOKIE_VALUE,
                            "expires_at": en_un_ano}])

    expires = world.stored[0]["expires_at"]
    assert expires <= datetime.now(UTC) + ep.MAX_SESSION_LIFETIME + timedelta(seconds=5)


def test_el_canje_no_puede_elegir_la_conexion_de_otra_empresa(world):
    """IDOR: el JSON no manda. La conexión y la empresa salen del código."""
    mine, my_tenant = world.add_connection(name="Mía")
    theirs, their_tenant = world.add_connection(name="Ajena")
    code, _ = world.add_pairing(mine)

    _redeem(code, connection_id=str(theirs), tenant_id=str(their_tenant))

    assert [s["connection_id"] for s in world.stored] == [mine]
    assert world.stored[0]["tenant_id"] == my_tenant


def test_una_sesion_que_effi_rechaza_se_informa_sin_romper(world):
    cid, _ = world.add_connection()
    code, _ = world.add_pairing(cid)
    world.preflight_status = "session_expired"

    result = _redeem(code)

    assert result.connected is False
    assert result.credential_status == "session_expired"
    assert world.outcomes[-1][0] == "session_rejected"


def test_si_la_prueba_falla_la_sesion_queda_guardada(world, monkeypatch):
    cid, _ = world.add_connection()
    code, _ = world.add_pairing(cid)

    def boom(conn, **k):
        raise ConnectionError(f"Cookie: ci_session={COOKIE_VALUE}")

    monkeypatch.setattr(ep, "run_preflight_for_connection", boom)

    result = _redeem(code)

    assert world.stored, "Un fallo de red al probar no puede tirar la sesión recibida"
    assert result.connected is False
    assert COOKIE_VALUE not in result.summary
    assert world.outcomes[-1][0] == "unverified"


# -- lo que nunca se filtra ----------------------------------------------------------


def test_la_cookie_no_aparece_en_logs_ni_en_la_respuesta(world, caplog):
    cid, _ = world.add_connection()
    code, _ = world.add_pairing(cid)
    caplog.set_level(logging.DEBUG)

    result = _redeem(code)

    assert COOKIE_VALUE not in caplog.text
    assert code not in caplog.text and code.replace("-", "") not in caplog.text
    assert COOKIE_VALUE not in result.model_dump_json()
    for params in world.outcomes:
        assert COOKIE_VALUE not in repr(params)


def test_el_repr_del_payload_no_muestra_secretos():
    code = ep.generate_code()
    text = repr(_payload(code))
    assert COOKIE_VALUE not in text
    assert code not in text


def test_un_valor_con_inyeccion_se_rechaza_sin_repetirlo():
    malicious = "abc;admin=1"
    with pytest.raises(ValidationError) as exc:
        _payload(ep.generate_code(), cookies=[{"name": "ci_session", "value": malicious}])
    # El manejador de la API devuelve solo `msg` (api/errors.py): ahí no puede ir.
    for err in exc.value.errors():
        assert malicious not in err["msg"]


def test_ninguna_respuesta_del_router_declara_la_cookie():
    from api.schemas import (
        EffiPairingRedeemResponse,
        EffiPairingResponse,
        EffiPairingStatusResponse,
    )

    for model in (EffiPairingRedeemResponse, EffiPairingResponse, EffiPairingStatusResponse):
        fields = set(model.model_fields)
        assert not fields & {"cookies", "cookie", "token", "session", "value"}, model


# -- quién puede generar el código ------------------------------------------------------


def _route(path, method):
    for route in ep.router.routes:
        if route.path == path and method in route.methods:
            return route
    raise AssertionError(f"No existe {method} {path}")


def test_solo_el_dueno_genera_y_consulta_codigos():
    for path, method in (
        ("/config/effi/connections/{connection_id}/pairing", "POST"),
        ("/config/effi/connections/{connection_id}/pairing/{pairing_id}", "GET"),
    ):
        route = _route(path, method)
        names = [d.dependency.__qualname__ for d in route.dependencies]
        assert any("require_role" in n for n in names), f"{method} {path} sin guardia de dueño"


def test_el_canje_no_pide_jwt_ni_id_de_conexion():
    route = _route("/config/effi/pairing/redeem", "POST")
    assert not route.dependencies
    assert "connection_id" not in EffiPairingRedeemRequest.model_fields


def test_generar_codigo_respeta_el_alcance(monkeypatch):
    """Otra empresa u otro país: 404 antes de escribir nada."""
    writes = []

    def out_of_scope(conn, user, connection_id):
        raise NotFound("Esa conexión no existe en tu workspace")

    monkeypatch.setattr(ep, "_connection_for_credential", out_of_scope)
    monkeypatch.setattr(ep, "execute", lambda *a, **k: writes.append(a))
    monkeypatch.setattr(ep, "fetch_one", lambda *a, **k: writes.append(a))

    with pytest.raises(NotFound):
        ep.create_pairing(
            uuid4(), ep.EffiPairingCreateRequest(consent_granted=True),
            request=None, conn=object(), user=SimpleNamespace(tenant_id=uuid4(), id=uuid4()),
            settings=SimpleNamespace(public_api_url="https://api.example"),
        )
    assert not writes


def test_generar_codigo_revoca_el_anterior_y_pide_consentimiento(monkeypatch):
    sql = []
    monkeypatch.setattr(
        ep, "_connection_for_credential",
        lambda conn, user, cid: {"platform_code": "effi", "platform_name": "Effi"},
    )
    monkeypatch.setattr(credentials, "vault_available", lambda: True)
    monkeypatch.setattr(ep, "execute", lambda conn, q, p=None: sql.append(" ".join(q.split())))
    monkeypatch.setattr(ep, "fetch_one", lambda conn, q, p=None: {"id": uuid4()})
    user = SimpleNamespace(tenant_id=uuid4(), id=uuid4())
    settings = SimpleNamespace(public_api_url="https://api.example")

    with pytest.raises(ApiError) as exc:
        ep.create_pairing(uuid4(), ep.EffiPairingCreateRequest(), request=None,
                          conn=object(), user=user, settings=settings)
    assert exc.value.code == "consent_required"

    result = ep.create_pairing(uuid4(), ep.EffiPairingCreateRequest(consent_granted=True),
                               request=None, conn=object(), user=user, settings=settings)
    assert any("SET revoked_at = now()" in s for s in sql)
    assert result.ttl_seconds == 600
    assert ep.normalize_code(result.code) is not None


def test_estado_del_codigo():
    now = datetime.now(UTC)
    base = {"redeemed_at": None, "revoked_at": None, "outcome": None,
            "expires_at": now + timedelta(minutes=5)}
    assert ep.pairing_state(base) == "pending"
    assert ep.pairing_state({**base, "expires_at": now - timedelta(seconds=1)}) == "expired"
    assert ep.pairing_state({**base, "revoked_at": now}) == "revoked"
    assert ep.pairing_state({**base, "redeemed_at": now, "outcome": "connected"}) == "connected"


# -- CORS solo para la extensión, solo en el canje --------------------------------------


def _cors_app(origins=""):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.extension_cors import build_middleware

    settings = SimpleNamespace(
        effi_extension_origin_list=[o for o in origins.split(",") if o]
    )
    app = FastAPI()
    app.middleware("http")(build_middleware(settings, ep.REDEEM_PATH))

    @app.post(ep.REDEEM_PATH)
    def redeem():
        return {"ok": True}

    @app.get("/kpis")
    def other():
        return {"ok": True}

    return TestClient(app)


EXT = "chrome-extension://" + "a" * 32


def test_la_extension_pasa_el_preflight_del_canje():
    client = _cors_app()
    res = client.options(ep.REDEEM_PATH, headers={
        "Origin": EXT, "Access-Control-Request-Method": "POST"})
    assert res.status_code == 204
    assert res.headers["access-control-allow-origin"] == EXT
    assert "access-control-allow-credentials" not in res.headers


def test_otro_origen_no_pasa_y_otras_rutas_no_se_abren():
    client = _cors_app()
    res = client.options(ep.REDEEM_PATH, headers={
        "Origin": "https://malo.example", "Access-Control-Request-Method": "POST"})
    assert res.status_code == 403
    res = client.get("/kpis", headers={"Origin": EXT})
    assert "access-control-allow-origin" not in res.headers


def test_la_lista_de_extensiones_se_puede_cerrar():
    other = "chrome-extension://" + "b" * 32
    client = _cors_app(origins=EXT)
    ok = client.post(ep.REDEEM_PATH, headers={"Origin": EXT})
    no = client.post(ep.REDEEM_PATH, headers={"Origin": other})
    assert ok.headers.get("access-control-allow-origin") == EXT
    assert "access-control-allow-origin" not in no.headers


# -- el motor ---------------------------------------------------------------------------


def test_la_migracion_no_toca_public_y_ata_el_codigo_a_su_empresa():
    sql = Path("migrations/070_effi_extension_pairing.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    assert "public." not in code
    assert "enforce_pairing_tenant" in code
    assert "FORCE ROW LEVEL SECURITY" in code
    assert "code_hash" in code and "char(64)" in code
    assert "'session_expired'" in code
    assert "auth_mode <> 'password' OR secret_enc IS NOT NULL" in code
