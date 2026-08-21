"""Product catalogue API: the half of a product no report can tell you.

Ingestion invents products from names in files. This suite is about the other
path - a person adding one, correcting a cost, retiring one - and about the
single distinction the catalogue exists to report: whether a human ever
confirmed the cost.
"""

from __future__ import annotations

import os

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "owner@catalogo.co"
OWNER_PASSWORD = "una-clave-larga-de-catalogo"
VIEWER_PASSWORD = "clave-larga-de-viewer-123"


@pytest.fixture(scope="module")
def api_dsn() -> str:
    if not resolve_test_dsn():
        pytest.skip("No DATABASE_URL configured")
    try:
        return recreate_test_database()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")


@pytest.fixture(scope="module")
def client(api_dsn):
    """A TestClient wired to the throwaway test database."""
    from fastapi.testclient import TestClient

    os.environ["DATABASE_URL"] = api_dsn
    os.environ["DATABASE_URL_READONLY"] = ""
    os.environ["AI_ENABLED"] = "false"
    os.environ["UPLOAD_DIR"] = "uploads/test"
    os.environ.setdefault("JWT_SECRET", "t" * 48)
    os.environ.setdefault("PII_HASH_SALT", "s" * 48)
    os.environ.setdefault("WORKER_TRIGGER_SECRET", "w" * 48)

    from api.settings import get_settings

    get_settings.cache_clear()

    from api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()


@pytest.fixture(scope="module")
def owner_token(client) -> str:
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Dueña del Catálogo",
            "tenant_name": "Catálogo Demo",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def viewer_token(client, owner_token) -> str:
    invite = client.post(
        "/auth/invite",
        json={"email": "viewer@catalogo.co", "role": "viewer"},
        headers=auth(owner_token),
    )
    assert invite.status_code == 201, invite.text

    accepted = client.post(
        "/auth/accept-invite",
        json={
            "token": invite.json()["invitation_token"],
            "password": VIEWER_PASSWORD,
            "full_name": "Solo Lectura",
        },
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_product(client, token: str, **fields) -> dict:
    response = client.post("/products", json=fields, headers=auth(token))
    assert response.status_code == 201, response.text
    return response.json()


# =============================================================================
# Creating
# =============================================================================


@pytest.fixture(scope="module")
def reviewed_product(client, owner_token) -> dict:
    """A product created by a person, with a cost they typed."""
    return create_product(
        client,
        owner_token,
        name="Faja Reductora Premium",
        sku="FAJA-PREM-01",
        category="Fajas",
        supplier_name="Proveedor Andino",
        unit_cost="15000.00",
        list_price="45000.00",
        target_margin_pct="60.00",
        weight_kg="0.450",
        currency_code="COP",
        notes="Costo confirmado por teléfono con el proveedor.",
    )


def test_creating_a_product_stores_what_the_operator_typed(reviewed_product):
    assert reviewed_product["product_name"] == "Faja Reductora Premium"
    assert reviewed_product["sku"] == "FAJA-PREM-01"
    assert reviewed_product["category"] == "Fajas"
    assert reviewed_product["currency_code"] == "COP"
    assert reviewed_product["is_active"] is True
    # Money crosses the wire as a JSON number, never a string.
    assert reviewed_product["unit_cost"] == 15000.0
    assert reviewed_product["list_price"] == 45000.0
    assert reviewed_product["weight_kg"] == 0.45


def test_creating_a_product_gets_or_creates_its_supplier(reviewed_product):
    assert reviewed_product["supplier_name"] == "Proveedor Andino"


def test_a_cost_typed_by_a_person_counts_as_reviewed(reviewed_product):
    assert reviewed_product["reviewed_at"] is not None
    assert reviewed_product["catalogue_status"] == "ok"


def test_a_new_product_has_no_shipments_yet(reviewed_product):
    assert reviewed_product["shipments"] == 0
    assert reviewed_product["delivered"] == 0
    assert reviewed_product["last_shipment_date"] is None


def test_the_same_supplier_is_reused_not_duplicated(client, owner_token, reviewed_product):
    """Two products from one supplier must not split it into two rows."""
    second = create_product(
        client,
        owner_token,
        name="Faja Reductora Clásica",
        supplier_name="  proveedor andino  ",
        unit_cost="12000.00",
    )
    assert second["supplier_name"] == "Proveedor Andino"


def test_creating_a_product_that_already_exists_is_a_conflict(client, owner_token, reviewed_product):
    response = client.post(
        "/products", json={"name": "faja  reductora  premium"}, headers=auth(owner_token)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_an_unknown_product_is_a_clean_404(client, owner_token):
    response = client.get(
        "/products/00000000-0000-0000-0000-000000000000", headers=auth(owner_token)
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# =============================================================================
# Patching a cost is what makes a product reviewed
# =============================================================================


@pytest.fixture(scope="module")
def unreviewed_product(client, owner_token) -> dict:
    """A product with no cost - the shape ingestion leaves behind."""
    return create_product(client, owner_token, name="Reloj Inteligente Sin Costo")


def test_a_product_without_a_cost_is_flagged_and_unreviewed(unreviewed_product):
    assert unreviewed_product["unit_cost"] is None
    assert unreviewed_product["reviewed_at"] is None
    assert unreviewed_product["catalogue_status"] == "sin_costo"


def test_patching_the_cost_sets_reviewed_at(client, owner_token, unreviewed_product):
    response = client.patch(
        f"/products/{unreviewed_product['product_id']}",
        json={"unit_cost": "80000.00", "currency_code": "COP"},
        headers=auth(owner_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["unit_cost"] == 80000.0
    assert body["reviewed_at"] is not None, "un costo que un humano confirmó debe quedar revisado"
    assert body["catalogue_status"] == "ok"


def test_the_cost_change_is_recorded_as_manual(client, owner_token, unreviewed_product):
    response = client.get(
        f"/products/{unreviewed_product['product_id']}", headers=auth(owner_token)
    )
    assert response.status_code == 200
    history = response.json()["cost_history"]
    assert history, "cambiar el costo debe dejar rastro"
    assert history[0]["unit_cost"] == 80000.0
    assert history[0]["source"] == "manual"


def test_patching_without_a_cost_leaves_the_review_alone(client, owner_token, unreviewed_product):
    before = client.get(
        f"/products/{unreviewed_product['product_id']}", headers=auth(owner_token)
    ).json()["product"]

    response = client.patch(
        f"/products/{unreviewed_product['product_id']}",
        json={"notes": "Se vende mucho en Antioquia"},
        headers=auth(owner_token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["notes"] == "Se vende mucho en Antioquia"
    assert body["reviewed_at"] == before["reviewed_at"]
    # Fields left out of the payload keep their value.
    assert body["unit_cost"] == 80000.0


def test_an_empty_string_clears_a_text_field(client, owner_token):
    """The UI sends "" when the operator empties a box. It has to mean empty.

    Omitting a field means "no lo toques". Sending it empty means "bórralo".
    If those two collapse into one, clearing a box looks like the old value
    coming back on its own, which is how the operator learns not to trust it.
    """
    product = create_product(
        client,
        owner_token,
        name="Producto Con Datos Que Se Borran",
        sku="BORRAR-01",
        category="Temporal",
        supplier_name="Proveedor Temporal",
        notes="Nota que el operador va a borrar",
    )
    assert product["supplier_name"] == "Proveedor Temporal"

    response = client.patch(
        f"/products/{product['product_id']}",
        json={"sku": "", "category": "", "supplier_name": "", "notes": ""},
        headers=auth(owner_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["sku"] == ""
    assert body["category"] == ""
    assert body["notes"] == ""
    assert body["supplier_name"] is None, "vaciar el proveedor debe quitarlo, no ignorarse"


def test_omitting_a_field_still_leaves_it_alone(client, owner_token):
    """The other half of the same rule: silence is not a change."""
    product = create_product(
        client,
        owner_token,
        name="Producto Que No Se Toca",
        sku="INTACTO-01",
        supplier_name="Proveedor Intacto",
    )

    response = client.patch(
        f"/products/{product['product_id']}",
        json={"category": "Solo cambio la categoría"},
        headers=auth(owner_token),
    )
    assert response.status_code == 200
    body = response.json()

    assert body["category"] == "Solo cambio la categoría"
    assert body["sku"] == "INTACTO-01"
    assert body["supplier_name"] == "Proveedor Intacto"


# =============================================================================
# Merge-patch: absent, set, and cleared are three different things
# =============================================================================


@pytest.fixture(scope="module")
def fully_populated_product(client, owner_token) -> dict:
    """Every nullable column filled, so each one can be cleared on its own."""
    return create_product(
        client,
        owner_token,
        name="Producto Completamente Lleno",
        sku="LLENO-01",
        category="Categoría",
        supplier_name="Proveedor Lleno",
        unit_cost="1000000.00",
        list_price="2000000.00",
        target_margin_pct="50.00",
        weight_kg="1.500",
        currency_code="COP",
        notes="Una nota",
    )


@pytest.mark.parametrize(
    "field",
    [
        "sku",
        "category",
        "supplier_name",
        "notes",
        "list_price",
        "target_margin_pct",
        "weight_kg",
        "currency_code",
    ],
)
def test_an_explicit_null_clears_each_nullable_column(client, owner_token, field):
    """The case COALESCE could not express: null means borrar, not "no lo toques".

    Numeric columns matter most here - an empty string is not even representable
    for them, so before merge-patch there was no way to undo a wrong number.
    """
    product = create_product(
        client,
        owner_token,
        name=f"Producto Para Vaciar {field}",
        sku="VACIAR-01",
        category="Categoría",
        supplier_name="Proveedor Para Vaciar",
        unit_cost="500.00",
        list_price="900.00",
        target_margin_pct="40.00",
        weight_kg="2.000",
        currency_code="COP",
        notes="Nota",
    )
    # supplier_name is the request field; the response reports supplier_name too.
    response = client.patch(
        f"/products/{product['product_id']}",
        json={field: None},
        headers=auth(owner_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()[field] is None, f"{field} debía quedar vacío"


def test_clearing_the_cost_also_withdraws_the_review(client, owner_token, fully_populated_product):
    """A typo of 1.000.000 has to be undoable, not just overwritable.

    Clearing the cost has to clear the review with it: otherwise the catalogue
    keeps reporting the product as confirmed by a human when it has no cost.
    """
    product_id = fully_populated_product["product_id"]
    assert fully_populated_product["reviewed_at"] is not None

    response = client.patch(
        f"/products/{product_id}", json={"unit_cost": None}, headers=auth(owner_token)
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["unit_cost"] is None
    assert body["reviewed_at"] is None, "sin costo no puede seguir contando como revisado"
    assert body["catalogue_status"] == "sin_costo"


def test_clearing_the_cost_writes_no_history_row(client, owner_token, fully_populated_product):
    """The 009 trigger only records non-null costs. Confirming that on purpose."""
    response = client.get(
        f"/products/{fully_populated_product['product_id']}", headers=auth(owner_token)
    )
    assert response.status_code == 200
    history = response.json()["cost_history"]

    assert all(entry["unit_cost"] is not None for entry in history)
    # The original 1.000.000 is still on the record; only the live value is gone.
    assert history[0]["unit_cost"] == 1000000.0


def test_a_null_name_is_refused_instead_of_hitting_the_constraint(
    client, owner_token, reviewed_product
):
    response = client.patch(
        f"/products/{reviewed_product['product_id']}",
        json={"name": None},
        headers=auth(owner_token),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_a_null_is_active_is_refused(client, owner_token, reviewed_product):
    response = client.patch(
        f"/products/{reviewed_product['product_id']}",
        json={"is_active": None},
        headers=auth(owner_token),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_an_empty_body_changes_nothing(client, owner_token, reviewed_product):
    """No fields sent means no UPDATE at all, not an UPDATE with an empty SET."""
    before = client.get(
        f"/products/{reviewed_product['product_id']}", headers=auth(owner_token)
    ).json()["product"]

    response = client.patch(
        f"/products/{reviewed_product['product_id']}", json={}, headers=auth(owner_token)
    )
    assert response.status_code == 200, response.text
    assert response.json() == before


def test_clearing_one_field_leaves_its_neighbours_alone(client, owner_token):
    """The whole point of merge-patch: one null must not blank the whole row."""
    product = create_product(
        client,
        owner_token,
        name="Producto De Un Solo Borrado",
        sku="VECINO-01",
        category="Categoría Vecina",
        supplier_name="Proveedor Vecino",
        unit_cost="700.00",
        notes="Nota vecina",
    )

    response = client.patch(
        f"/products/{product['product_id']}", json={"notes": None}, headers=auth(owner_token)
    )
    assert response.status_code == 200
    body = response.json()

    assert body["notes"] is None
    assert body["sku"] == "VECINO-01"
    assert body["category"] == "Categoría Vecina"
    assert body["supplier_name"] == "Proveedor Vecino"
    assert body["unit_cost"] == 700.0
    assert body["reviewed_at"] is not None, "un borrado de notas no toca la revisión"


# =============================================================================
# The product ingestion made, not this API
# =============================================================================


@pytest.fixture(scope="module")
def ingested_product(client, owner_token, api_dsn) -> str:
    """A product exactly as a file load leaves it: a cost, and nobody vouching.

    Written straight to core.product because that is the only way to reach the
    `sin_revisar` state - this API always stamps a reviewer when it writes a
    cost, and ingestion never does. Most rows in a real catalogue look like this.
    """
    tenant_id = client.get("/auth/me", headers=auth(owner_token)).json()["tenant_id"]

    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute(
            """
            INSERT INTO core.product (tenant_id, name, name_norm, unit_cost, currency_code)
            VALUES (%s, %s, core.normalize_text(%s), %s, %s)
            RETURNING id
            """,
            (tenant_id, "Producto De Una Carga", "Producto De Una Carga", 25000, "COP"),
        )
        product_id = cur.fetchone()[0]
        conn.commit()

    return str(product_id)


def test_an_ingested_product_is_flagged_as_unreviewed(client, owner_token, ingested_product):
    response = client.get(f"/products/{ingested_product}", headers=auth(owner_token))
    assert response.status_code == 200
    product = response.json()["product"]

    assert product["unit_cost"] == 25000.0
    assert product["reviewed_at"] is None
    assert product["catalogue_status"] == "sin_revisar"


def test_the_ingested_cost_is_recorded_as_import_not_manual(
    client, owner_token, ingested_product
):
    """Where the cost came from is the whole point of the history table."""
    response = client.get(f"/products/{ingested_product}", headers=auth(owner_token))
    history = response.json()["cost_history"]

    assert history[0]["source"] == "import"
    assert history[0]["changed_by"] is None


def test_resending_the_same_cost_confirms_it(client, owner_token, ingested_product):
    """The "Confirmar costo y guardar" flow: same number in, review stamped.

    The operator opens a product the importer guessed at, looks at the cost,
    and agrees with it. Sending the unchanged value is how they say so - the
    review is about a human vouching, not about the number moving.
    """
    response = client.patch(
        f"/products/{ingested_product}",
        json={"unit_cost": "25000.00"},
        headers=auth(owner_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["unit_cost"] == 25000.0
    assert body["reviewed_at"] is not None, "confirmar un costo sin cambiarlo debe marcarlo"
    assert body["catalogue_status"] == "ok"


def test_confirming_an_unchanged_cost_adds_no_history_row(
    client, owner_token, ingested_product
):
    """Nothing about the cost changed, so the cost history has nothing to add."""
    response = client.get(f"/products/{ingested_product}", headers=auth(owner_token))
    history = response.json()["cost_history"]

    assert len(history) == 1, "confirmar no es un cambio de costo"
    assert history[0]["source"] == "import"


# =============================================================================
# Filters
# =============================================================================


@pytest.fixture(scope="module")
def costless_product(client, owner_token) -> dict:
    return create_product(client, owner_token, name="Audífonos Sin Revisar")


def test_the_status_filter_returns_only_that_status(client, owner_token, costless_product):
    response = client.get("/products?status=sin_costo", headers=auth(owner_token))
    assert response.status_code == 200
    rows = response.json()

    assert rows, "debe haber al menos un producto sin costo"
    assert all(row["catalogue_status"] == "sin_costo" for row in rows)
    assert costless_product["product_id"] in {row["product_id"] for row in rows}


def test_the_status_filter_excludes_reviewed_products(client, owner_token, reviewed_product):
    response = client.get("/products?status=sin_costo", headers=auth(owner_token))
    assert reviewed_product["product_id"] not in {row["product_id"] for row in response.json()}


def test_an_invalid_status_is_rejected_before_touching_sql(client, owner_token):
    response = client.get("/products?status=inventado", headers=auth(owner_token))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_search_matches_name_or_sku_case_insensitively(client, owner_token, reviewed_product):
    by_name = client.get("/products?search=faja+reductora+premium", headers=auth(owner_token))
    assert by_name.status_code == 200
    assert reviewed_product["product_id"] in {row["product_id"] for row in by_name.json()}

    by_sku = client.get("/products?search=faja-prem", headers=auth(owner_token))
    assert reviewed_product["product_id"] in {row["product_id"] for row in by_sku.json()}


# =============================================================================
# Soft delete
# =============================================================================


def test_deleting_a_product_only_deactivates_it(client, owner_token, costless_product):
    product_id = costless_product["product_id"]

    response = client.delete(f"/products/{product_id}", headers=auth(owner_token))
    assert response.status_code == 204

    # Still there: shipments point at it, so the row must survive.
    detail = client.get(f"/products/{product_id}", headers=auth(owner_token))
    assert detail.status_code == 200
    assert detail.json()["product"]["is_active"] is False


def test_the_active_filter_hides_and_finds_the_deactivated_product(
    client, owner_token, costless_product
):
    product_id = costless_product["product_id"]

    active = client.get("/products?active=true", headers=auth(owner_token))
    assert product_id not in {row["product_id"] for row in active.json()}

    inactive = client.get("/products?active=false", headers=auth(owner_token))
    assert product_id in {row["product_id"] for row in inactive.json()}


def test_a_deactivated_product_can_be_brought_back(client, owner_token, costless_product):
    """Soft delete has to be reversible, or it is just a delete with extra steps."""
    product_id = costless_product["product_id"]

    response = client.patch(
        f"/products/{product_id}", json={"is_active": True}, headers=auth(owner_token)
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_active"] is True

    active = client.get("/products?active=true", headers=auth(owner_token))
    assert product_id in {row["product_id"] for row in active.json()}

    # Put it back the way the rest of the suite expects to find it.
    client.delete(f"/products/{product_id}", headers=auth(owner_token))


def test_deleting_an_unknown_product_is_a_clean_404(client, owner_token):
    response = client.delete(
        "/products/00000000-0000-0000-0000-000000000000", headers=auth(owner_token)
    )
    assert response.status_code == 404


# =============================================================================
# Roles
# =============================================================================


def test_a_viewer_can_read_the_catalogue(client, viewer_token):
    response = client.get("/products", headers=auth(viewer_token))
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_a_viewer_cannot_create_a_product(client, viewer_token):
    response = client.post(
        "/products", json={"name": "Producto de un viewer"}, headers=auth(viewer_token)
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_a_viewer_cannot_patch_a_product(client, viewer_token, reviewed_product):
    response = client.patch(
        f"/products/{reviewed_product['product_id']}",
        json={"unit_cost": "1.00"},
        headers=auth(viewer_token),
    )
    assert response.status_code == 403


def test_a_viewer_cannot_delete_a_product(client, viewer_token, reviewed_product):
    response = client.delete(
        f"/products/{reviewed_product['product_id']}", headers=auth(viewer_token)
    )
    assert response.status_code == 403


def test_the_catalogue_rejects_anonymous_callers(client):
    assert client.get("/products").status_code == 401
    assert client.post("/products", json={"name": "x"}).status_code == 401
