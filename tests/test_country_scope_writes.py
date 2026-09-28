"""Alcance por país en las ESCRITURAS y en los caminos que el guardia no ve.

`_guard_country` solo sabe leer `?country=` de la query. Todo lo que trae el
país en el cuerpo, en un id, o que no trae país, tiene que recortarse a mano.
Estos tests fijan cada uno de esos agujeros sin base de datos: las consultas se
sustituyen, porque lo que se comprueba es la decisión, no el SQL.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

pytest.importorskip("fastapi")

from api.deps import CurrentUser  # noqa: E402
from api.errors import Forbidden, NotFound  # noqa: E402

TENANT = uuid4()


def _user(role: str = "owner", countries: tuple[str, ...] | None = ("GT",)) -> CurrentUser:
    return CurrentUser(
        id=uuid4(), tenant_id=TENANT, email="socio@example.com", role=role,
        countries=countries,
    )


class _Boom:
    """Una conexión que no debe usarse: el rechazo va antes de cualquier consulta."""

    def cursor(self, *_a, **_k):  # pragma: no cover - si se llama, el test falla
        raise AssertionError("se consultó la base antes de rechazar")


# =============================================================================
# Invitar: nadie reparte más alcance del que tiene
# =============================================================================


def test_un_owner_limitado_no_invita_a_alguien_sin_limite():
    from api.routers.auth import invite
    from api.schemas import InviteRequest

    with pytest.raises(Forbidden):
        invite(InviteRequest(email="yo-otra-vez@example.com", role="owner"), _Boom(), _user())


def test_un_owner_limitado_no_invita_a_otro_pais():
    from api.routers.auth import invite
    from api.schemas import InviteRequest

    payload = InviteRequest(email="x@example.com", role="viewer", country_scope=["CO"])
    with pytest.raises(Forbidden):
        invite(payload, _Boom(), _user())


def test_un_owner_limitado_si_invita_dentro_de_su_alcance(monkeypatch):
    import api.routers.auth as auth
    from api.schemas import InviteRequest

    # Pasa el guardia y llega a la base: ahí se corta, que es lo que se quería ver.
    monkeypatch.setattr(auth, "_assert_countries_active", lambda *_a: None)

    class _Reached(Exception):
        pass

    def fetch_one(*_a, **_k):
        raise _Reached

    monkeypatch.setattr(auth, "fetch_one", fetch_one)
    payload = InviteRequest(email="x@example.com", role="viewer", country_scope=["gt"])
    with pytest.raises(_Reached):
        auth.invite(payload, object(), _user())


# =============================================================================
# Activar/desactivar un país: el país viaja en el cuerpo
# =============================================================================


def test_un_owner_limitado_no_apaga_un_pais_ajeno():
    from api.routers.config import activate_country
    from api.schemas import ActivateCountryRequest

    with pytest.raises(Forbidden):
        activate_country(
            ActivateCountryRequest(country_code="co", is_active=False), _Boom(), _user()
        )


# =============================================================================
# Productos: el costo entra en el margen de todos los países que lo vendieron
# =============================================================================


@pytest.mark.parametrize(
    ("inside", "outside", "expected"),
    [
        (True, True, Forbidden),      # compartido: lo ve, pero el cambio llega a otros
        (None, True, NotFound),       # solo de otros países: ni existe para él
        (True, None, None),           # solo de su país
        (None, None, None),           # sin guías: creado a mano, no afecta a nadie
    ],
)
def test_editar_un_producto_respeta_el_alcance(monkeypatch, inside, outside, expected):
    import api.routers.products as products

    monkeypatch.setattr(
        products, "fetch_one", lambda *_a, **_k: {"inside": inside, "outside": outside}
    )
    if expected is None:
        products._assert_product_writable(object(), _user("analyst"), uuid4())
    else:
        with pytest.raises(expected):
            products._assert_product_writable(object(), _user("analyst"), uuid4())


def test_sin_limite_de_pais_no_se_consulta_nada():
    import api.routers.products as products

    products._assert_product_writable(_Boom(), _user("analyst", countries=None), uuid4())


# =============================================================================
# Copiloto: los hilos guardan cifras, y son de la sociedad
# =============================================================================


def test_la_lista_de_hilos_se_recorta_al_alcance(monkeypatch):
    import ai.features
    from api.routers import ai as ai_router

    now = "2026-09-01T00:00:00+00:00"
    hilos = [
        {"id": uuid4(), "country_code": "CO", "title": "Colombia", "created_at": now,
         "last_message_at": now, "message_count": 2},
        {"id": uuid4(), "country_code": "GT", "title": "Guatemala", "created_at": now,
         "last_message_at": now, "message_count": 2},
        {"id": uuid4(), "country_code": None, "title": "Sin país", "created_at": now,
         "last_message_at": now, "message_count": 1},
    ]
    monkeypatch.setattr(ai.features, "list_conversations", lambda *_a, **_k: hilos)

    response = ai_router.conversations(object(), _user("analyst"))
    assert [c.title for c in response.conversations] == ["Guatemala"]


def test_un_hilo_de_otro_pais_es_404(monkeypatch):
    import ai.features
    from api.routers import ai as ai_router

    monkeypatch.setattr(
        ai.features, "get_conversation",
        lambda *_a, **_k: {"id": uuid4(), "country_code": "CO", "messages": []},
    )
    with pytest.raises(NotFound):
        ai_router.conversation_detail(uuid4(), object(), _user("analyst"))


# =============================================================================
# Umbrales: la campana es de todos, la configuración no
# =============================================================================


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from api.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/none")
    monkeypatch.setenv("JWT_SECRET", "j" * 48)
    monkeypatch.setenv("PII_HASH_SALT", "s" * 48)
    monkeypatch.setenv("WORKER_TRIGGER_SECRET", "w" * 48)
    get_settings.cache_clear()

    from api.main import create_app

    # Sin `with`: no corre el lifespan, así que no se abre ningún pool. El
    # guardia de la ruta tiene que responder antes de pedir una conexión.
    yield TestClient(create_app(), raise_server_exceptions=False)
    get_settings.cache_clear()


def _token(role: str) -> str:
    from api.security import create_access_token
    from api.settings import get_settings

    token, _ = create_access_token(
        get_settings(), user_id=uuid4(), tenant_id=TENANT, email="x@example.com", role=role
    )
    return token


@pytest.mark.parametrize("role", ["viewer", "uploader"])
def test_quien_no_configura_no_cambia_umbrales(client, role):
    response = client.put(
        "/notifications/thresholds?country=GT",
        headers={"Authorization": f"Bearer {_token(role)}"},
        json={"thresholds": {"return_rate": "40"}},
    )
    assert response.status_code == 403, response.text


def test_un_uploader_no_lee_umbrales(client):
    response = client.get(
        "/notifications/thresholds?country=GT",
        headers={"Authorization": f"Bearer {_token('uploader')}"},
    )
    assert response.status_code == 403, response.text
