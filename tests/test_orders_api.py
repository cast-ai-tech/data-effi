"""Orders and customers: the two endpoints that hand out a real person's data.

Most of this suite is about one sentence: **a viewer never receives a customer's
name or phone number**. Not blurred, not masked, not hidden by the frontend -
absent from the JSON the server produced. Every other assertion here exists to
make sure that rule cannot be satisfied by accident and then quietly lost:

  * the same request as an owner DOES return the contact data, so a passing
    viewer test cannot be passing because decryption is broken for everyone;
  * `customer_ref` is present for both roles, so a viewer still has a stable
    label to group by and nobody is tempted to "just show the phone";
  * reading contact data leaves a row in `raw.pii_access`, and a viewer's read
    leaves none, because nothing was disclosed;
  * two tenants cannot see each other, at the row level and at the id level.

The data is written straight into `core.shipment` under the service context.
That is deliberate: this suite is about what the API hands out, and going
through ingestion would make a privacy test depend on a CSV parser.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn, seed_workspace

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("cryptography")

pytestmark = pytest.mark.postgres

OWNER_EMAIL = "owner@ordenes.ec"
OWNER_PASSWORD = "una-clave-larga-de-ordenes"
VIEWER_EMAIL = "viewer@ordenes.ec"
VIEWER_PASSWORD = "clave-larga-de-viewer-ordenes"
RIVAL_EMAIL = "owner@rival.ec"
RIVAL_PASSWORD = "clave-larga-del-inquilino-rival"

COUNTRY = "EC"
CURRENCY = "USD"

RIVAL_TENANT_ID = UUID("99999999-9999-9999-9999-999999999999")
RIVAL_CONNECTION_ID = UUID("88888888-8888-8888-8888-888888888888")

TODAY = date(2026, 8, 20)

# The people in the fixture. `phone` is what gets hashed into customer_hash and
# encrypted into customer_phone_enc - the same value, through two completely
# different functions, which is the whole design.
ANA = {"name": "Ana Lucía Paredes", "phone": "0991234567", "doc": "1712345678",
       "address": "Av. Amazonas N34-50", "city": "Quito", "province": "Pichincha"}
BRUNO = {"name": "Bruno Cedeño", "phone": "0987654321", "doc": "0912345678",
         "address": "Km 12 Vía a Daule", "city": "Guayaquil", "province": "Guayas"}


# =============================================================================
# Harness
# =============================================================================


@pytest.fixture(scope="module")
def api_dsn() -> str:
    if not resolve_test_dsn():
        pytest.skip("No DATABASE_URL configured")
    try:
        return recreate_test_database()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")


@pytest.fixture(scope="module")
def pii_key() -> str:
    """A throwaway encryption key, generated per run. Never a real one."""
    from pipeline.crypto import _fernet, generate_key

    key = generate_key()
    os.environ["PII_ENCRYPTION_KEY"] = key
    _fernet.cache_clear()
    return key


@pytest.fixture(scope="module")
def client(api_dsn, pii_key):
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


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def owner_token(client) -> str:
    response = client.post(
        "/auth/register",
        json={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "full_name": "Dueña de Órdenes",
            "tenant_name": "Órdenes Demo",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def viewer_token(client, owner_token) -> str:
    invite = client.post(
        "/auth/invite",
        json={"email": VIEWER_EMAIL, "role": "viewer"},
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


@pytest.fixture(scope="module")
def tenant_id(client, owner_token) -> UUID:
    return UUID(client.get("/auth/me", headers=auth(owner_token)).json()["tenant_id"])


# =============================================================================
# Fixture data
# =============================================================================


def _hash_customer(phone: str) -> str:
    """The same shape `pipeline.ingest.hash_customer` produces: salted SHA-256."""
    salt = os.environ["PII_HASH_SALT"]
    return hashlib.sha256(f"{salt}{phone}".encode()).hexdigest()


def _insert_shipment(cur, *, tenant, connection, tracking, person, status, created,
                     delivered=None, declared=None, freight=None, cogs=None,
                     product_id=None, carrier_tracking=None, encrypt=True,
                     country=COUNTRY, currency=CURRENCY, address=None, document=None,
                     geo_id=None):
    """One guide, with contact data encrypted exactly the way the pipeline does.

    `address` and `document` override the person's defaults so a later guide can
    carry a corrected address, or omit a field the earlier one had.
    """
    from pipeline.crypto import encrypt_pii

    address = person["address"] if address is None else address
    document = person["doc"] if document is None else document

    cur.execute(
        """
        INSERT INTO core.shipment
            (tenant_id, connection_id, country_code, tracking_number,
             carrier_tracking_number, customer_hash, product_id, geo_id, quantity,
             status_code, created_date, delivered_at, currency_code,
             declared_value, cod_collected, freight_cost, product_cost,
             customer_name_enc, customer_phone_enc, customer_document_enc,
             customer_address_enc, customer_city_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            tenant, connection, country, tracking, carrier_tracking,
            _hash_customer(person["phone"]), product_id, geo_id, status, created,
            delivered, currency, declared,
            declared if status == "delivered" else None,
            freight, cogs,
            encrypt_pii(person["name"]) if encrypt else None,
            encrypt_pii(person["phone"]) if encrypt else None,
            encrypt_pii(document) if encrypt else None,
            encrypt_pii(address) if encrypt else None,
            person["city"],
        ),
    )
    return cur.fetchone()[0]


@pytest.fixture(scope="module")
def seeded(client, api_dsn, tenant_id, pii_key) -> dict:
    """Two customers, five guides, one movement - all in EC.

    Ana has three closed guides (two delivered, one returned) so she gets a
    real grade. Bruno has one delivered and one still moving, which is what
    makes `only_open` and `days_open` testable.
    """
    seeded: dict = {}

    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        seed_workspace(
            conn,
            tenant_id=tenant_id,
            connection_id=uuid4(),
            country_code=COUNTRY,
            slug="ordenes",
        )
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute(
            "SELECT id FROM core.connection WHERE tenant_id = %s LIMIT 1", (tenant_id,)
        )
        connection_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO core.product (tenant_id, name, name_norm, unit_cost, currency_code) "
            "VALUES (%s, %s, core.normalize_text(%s), %s, %s) RETURNING id",
            (tenant_id, "Faja Ecuador", "Faja Ecuador", 12, CURRENCY),
        )
        product_id = cur.fetchone()[0]

        common = {"tenant": tenant_id, "connection": connection_id, "product_id": product_id}

        seeded["ana_delivered"] = _insert_shipment(
            cur, tracking="EC-0001", carrier_tracking="SERV-9001", person=ANA,
            status="delivered", created=TODAY - timedelta(days=20),
            delivered=TODAY - timedelta(days=17), declared=60, freight=6, cogs=12, **common,
        )
        seeded["ana_delivered_2"] = _insert_shipment(
            cur, tracking="EC-0002", person=ANA, status="delivered",
            created=TODAY - timedelta(days=12), delivered=TODAY - timedelta(days=9),
            declared=45, freight=6, cogs=12, **common,
        )
        seeded["ana_returned"] = _insert_shipment(
            cur, tracking="EC-0003", person=ANA, status="returned",
            created=TODAY - timedelta(days=8), declared=45, freight=11, cogs=12, **common,
        )
        seeded["bruno_delivered"] = _insert_shipment(
            cur, tracking="EC-0004", person=BRUNO, status="delivered",
            created=TODAY - timedelta(days=15), delivered=TODAY - timedelta(days=13),
            declared=80, freight=7, cogs=12, **common,
        )
        seeded["bruno_open"] = _insert_shipment(
            cur, tracking="EC-0005", person=BRUNO, status="in_transit",
            created=TODAY - timedelta(days=2), declared=80, freight=7, cogs=12, **common,
        )

        # One money event, so the timeline has something to interleave with the
        # status event and the ordering rule is actually exercised.
        cur.execute(
            """
            INSERT INTO core.movement
                (tenant_id, connection_id, country_code, shipment_id, movement_type_code,
                 movement_date, amount, currency_code, external_ref, dedupe_key)
            VALUES (%s, %s, %s, %s, 'cod_collected', %s, %s, %s, %s, %s)
            """,
            (
                tenant_id, connection_id, COUNTRY, seeded["ana_delivered"],
                TODAY - timedelta(days=17), 60, CURRENCY, "REC-001", "d" * 64,
            ),
        )
        conn.commit()

    seeded["ana_hash"] = _hash_customer(ANA["phone"])
    seeded["bruno_hash"] = _hash_customer(BRUNO["phone"])
    return seeded


@pytest.fixture(scope="module")
def rival_token(client, api_dsn, seeded) -> str:
    """A second tenant with its own guide, created straight in SQL.

    `/auth/register` only works once per deployment, on purpose, so the rival
    tenant is built the way a second workspace really appears: rows first, then
    a normal login.
    """
    from api.security import hash_password

    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        seed_workspace(
            conn,
            tenant_id=RIVAL_TENANT_ID,
            connection_id=RIVAL_CONNECTION_ID,
            country_code=COUNTRY,
            slug="rival",
        )
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute(
            "INSERT INTO core.app_user (tenant_id, email, password_hash, full_name, role) "
            "VALUES (%s, %s, %s, %s, 'owner') ON CONFLICT DO NOTHING",
            (RIVAL_TENANT_ID, RIVAL_EMAIL, hash_password(RIVAL_PASSWORD), "Dueño Rival"),
        )
        # Since 032 a person reaches a company through core.membership, not
        # through app_user.tenant_id alone: /auth/login reads the companies from
        # core.user_workspaces, and a user with no membership is refused with
        # "Tu usuario no pertenece a ninguna sociedad". `tenant_id` now only
        # decides WHICH company opens first, so seeding a rival straight in SQL
        # has to grant the membership too.
        cur.execute(
            "INSERT INTO core.membership (user_id, tenant_id, role) "
            "SELECT id, tenant_id, role FROM core.app_user WHERE lower(email) = lower(%s) "
            "ON CONFLICT (user_id, tenant_id) DO NOTHING",
            (RIVAL_EMAIL,),
        )
        _insert_shipment(
            cur, tenant=RIVAL_TENANT_ID, connection=RIVAL_CONNECTION_ID,
            tracking="RIVAL-0001", person=BRUNO, status="delivered",
            created=TODAY - timedelta(days=5), delivered=TODAY - timedelta(days=3),
            declared=99, freight=7, cogs=12,
        )
        conn.commit()

    response = client.post(
        "/auth/login", json={"email": RIVAL_EMAIL, "password": RIVAL_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def orders(client, token: str, **params) -> dict:
    params.setdefault("country", COUNTRY)
    response = client.get("/orders", params=params, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def customers(client, token: str, **params) -> dict:
    params.setdefault("country", COUNTRY)
    response = client.get("/customers", params=params, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def pii_access_rows(api_dsn, tenant: UUID) -> list[tuple]:
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute(
            "SELECT endpoint, record_count, user_id FROM raw.pii_access "
            "WHERE tenant_id = %s ORDER BY id",
            (tenant,),
        )
        return cur.fetchall()


# =============================================================================
# THE RULE: a viewer never receives contact data
# =============================================================================


def test_a_viewer_receives_no_name_and_no_phone(client, viewer_token, seeded):
    """The one test this whole endpoint exists to keep passing.

    The server does not send the fields. There is nothing for the client to
    reveal, nothing in the JSON to grep for, and nothing a screenshot of the
    network tab can leak.
    """
    body = orders(client, viewer_token)

    assert body["rows"], "el viewer sí debe poder ver la tabla de guías"
    assert body["pii_visible"] is False

    for row in body["rows"]:
        assert row["customer_name"] is None, "un viewer no puede recibir el nombre"
        assert row["customer_phone"] is None, "un viewer no puede recibir el teléfono"


def test_the_viewers_response_contains_no_contact_string_anywhere(
    client, viewer_token, seeded
):
    """Not in a field, not in a message, not in a nested object. Anywhere."""
    response = client.get("/orders", params={"country": COUNTRY}, headers=auth(viewer_token))
    payload = response.text

    assert ANA["name"] not in payload
    assert ANA["phone"] not in payload
    assert BRUNO["name"] not in payload
    assert BRUNO["phone"] not in payload


def test_a_viewer_gets_no_address_or_document_on_the_detail_card(
    client, viewer_token, seeded
):
    response = client.get(
        f"/orders/{seeded['ana_delivered']}", headers=auth(viewer_token)
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["pii_visible"] is False
    assert body["order"]["customer_name"] is None
    assert body["order"]["customer_phone"] is None
    assert body["order"]["customer_address"] is None
    assert body["order"]["customer_document"] is None
    assert ANA["address"] not in response.text
    assert ANA["doc"] not in response.text


def test_a_viewer_gets_no_contact_data_in_the_customers_table(client, viewer_token, seeded):
    body = customers(client, viewer_token)

    assert body["rows"], "el viewer sí debe poder ver la tabla de clientes"
    assert body["pii_visible"] is False
    assert all(row["customer_name"] is None for row in body["rows"])
    assert all(row["customer_phone"] is None for row in body["rows"])


def test_a_viewer_gets_no_contact_data_on_a_customer_card(client, viewer_token, seeded):
    response = client.get(
        f"/customers/{seeded['ana_hash']}", headers=auth(viewer_token)
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["pii_visible"] is False
    assert body["customer"]["customer_phone"] is None
    assert body["customer"]["customer_name"] is None
    assert body["customer"]["customer_address"] is None, "un viewer no recibe la dirección"
    assert body["customer"]["customer_document"] is None, "ni el documento"
    assert all(order["customer_phone"] is None for order in body["orders"])

    for secret in (ANA["phone"], ANA["name"], ANA["address"], ANA["doc"]):
        assert secret not in response.text


def test_a_viewer_still_gets_the_stable_customer_label(client, viewer_token, seeded):
    """Without a ref, the pressure to "just show the phone" comes straight back.

    `customer_ref` is derived from the hash, identifies nobody outside this
    workspace, and is what lets a viewer say "these two guides are the same
    person" without ever seeing who that person is.
    """
    body = orders(client, viewer_token)
    refs = {row["customer_ref"] for row in body["rows"]}

    assert None not in refs
    assert all(ref.startswith("#") and len(ref) == 7 for ref in refs)
    # Two customers in the fixture, so exactly two distinct labels.
    assert len(refs) == 2


def test_the_same_request_as_owner_does_return_the_contact_data(
    client, owner_token, seeded
):
    """The control. Without this, the viewer test passes when decryption is broken."""
    body = orders(client, owner_token, search="EC-0001")

    assert body["pii_visible"] is True
    row = body["rows"][0]
    assert row["customer_name"] == ANA["name"]
    assert row["customer_phone"] == ANA["phone"]


def test_the_owner_sees_address_and_document_only_on_the_detail_card(
    client, owner_token, seeded
):
    """The table never needs them, so a page of 200 rows never decrypts them."""
    listed = orders(client, owner_token, search="EC-0001")["rows"][0]
    assert "customer_address" not in listed
    assert "customer_document" not in listed

    detail = client.get(
        f"/orders/{seeded['ana_delivered']}", headers=auth(owner_token)
    ).json()
    assert detail["order"]["customer_address"] == ANA["address"]
    assert detail["order"]["customer_document"] == ANA["doc"]


def test_the_ref_is_the_same_label_in_orders_and_in_customers(
    client, owner_token, seeded
):
    order_row = orders(client, owner_token, search="EC-0001")["rows"][0]
    card = client.get(
        f"/customers/{seeded['ana_hash']}", headers=auth(owner_token)
    ).json()

    assert order_row["customer_ref"] == card["customer"]["customer_ref"]


# =============================================================================
# The access log: the fact, never the value
# =============================================================================


def test_reading_contact_data_leaves_a_row_in_the_access_log(
    client, owner_token, tenant_id, api_dsn, seeded
):
    before = len(pii_access_rows(api_dsn, tenant_id))
    orders(client, owner_token)
    after = pii_access_rows(api_dsn, tenant_id)

    assert len(after) == before + 1
    endpoint, record_count, user_id = after[-1]
    assert endpoint == "GET /orders"
    assert record_count == 5, "cinco guías con contacto descifrado, cinco registros"
    assert user_id is not None, "la bitácora tiene que decir QUIÉN"


def test_a_viewers_read_leaves_no_row_because_nothing_was_disclosed(
    client, viewer_token, tenant_id, api_dsn, seeded
):
    before = len(pii_access_rows(api_dsn, tenant_id))
    orders(client, viewer_token)
    customers(client, viewer_token)
    client.get(f"/orders/{seeded['ana_delivered']}", headers=auth(viewer_token))

    assert len(pii_access_rows(api_dsn, tenant_id)) == before


def test_the_access_log_never_stores_a_decrypted_value(api_dsn, tenant_id, seeded):
    """An audit table that accumulates the data it audits is a second leak."""
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute("SELECT * FROM raw.pii_access WHERE tenant_id = %s", (tenant_id,))
        columns = [description.name for description in cur.description]
        rows = cur.fetchall()

    assert set(columns) == {
        "id", "tenant_id", "user_id", "endpoint", "record_count", "ip", "created_at"
    }
    flat = " ".join(str(value) for row in rows for value in row)
    for secret in (ANA["name"], ANA["phone"], ANA["doc"], BRUNO["name"], BRUNO["phone"]):
        assert secret not in flat


def test_the_logged_endpoint_is_a_template_not_a_rendered_url(
    client, owner_token, tenant_id, api_dsn, seeded
):
    """A query string carries the search term, and the search term is a phone."""
    client.get(f"/orders/{seeded['ana_delivered']}", headers=auth(owner_token))
    endpoint = pii_access_rows(api_dsn, tenant_id)[-1][0]

    assert endpoint == "GET /orders/{shipment_id}"
    assert str(seeded["ana_delivered"]) not in endpoint


def test_the_log_counts_records_that_carried_data_not_rows_returned(
    client, owner_token, tenant_id, api_dsn, seeded
):
    orders(client, owner_token, search="EC-0001")
    endpoint, record_count, _ = pii_access_rows(api_dsn, tenant_id)[-1]

    assert endpoint == "GET /orders"
    assert record_count == 1


# =============================================================================
# Two tenants
# =============================================================================


def test_a_tenant_only_sees_its_own_guides(client, rival_token, seeded):
    body = orders(client, rival_token)
    trackings = {row["tracking_number"] for row in body["rows"]}

    assert trackings == {"RIVAL-0001"}
    assert body["total"] == 1


def test_a_tenant_cannot_open_another_tenants_guide_by_id(
    client, rival_token, seeded
):
    """Knowing the uuid is not authorisation. It has to read as "does not exist"."""
    response = client.get(f"/orders/{seeded['ana_delivered']}", headers=auth(rival_token))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_tenant_cannot_open_another_tenants_customer(client, owner_token, rival_token):
    """The hash is deterministic, so the SAME phone produces the SAME id in both
    workspaces. That is exactly why this test exists: a shared identifier must
    not become a shared row."""
    rival_customers = customers(client, rival_token)["rows"]
    assert rival_customers, "el inquilino rival tiene su propio cliente"

    shared_hash = rival_customers[0]["customer_hash"]
    rival_view = client.get(f"/customers/{shared_hash}", headers=auth(rival_token)).json()
    own_view = client.get(f"/customers/{shared_hash}", headers=auth(owner_token)).json()

    # Same person, same id, two completely separate histories.
    assert rival_view["customer"]["orders"] == 1
    assert own_view["customer"]["orders"] == 2
    assert {o["tracking_number"] for o in rival_view["orders"]} == {"RIVAL-0001"}
    assert "RIVAL-0001" not in {o["tracking_number"] for o in own_view["orders"]}


def test_a_tenant_does_not_see_another_tenants_customers_in_the_table(
    client, owner_token, seeded
):
    body = customers(client, owner_token)
    assert body["total"] == 2, "sólo Ana y Bruno; el cliente del rival no cuenta"


def test_the_access_log_is_scoped_to_the_tenant_that_read(
    client, rival_token, api_dsn, tenant_id, seeded
):
    before = len(pii_access_rows(api_dsn, tenant_id))
    orders(client, rival_token)

    assert len(pii_access_rows(api_dsn, tenant_id)) == before, (
        "la lectura del rival no puede aparecer en la bitácora de otro inquilino"
    )
    assert pii_access_rows(api_dsn, RIVAL_TENANT_ID), "pero sí en la suya"


# =============================================================================
# Pagination: the ceiling is the point
# =============================================================================


def test_a_page_size_above_the_ceiling_is_refused(client, owner_token):
    """200 is a table. 50.000 is an export of the customer list."""
    for path in ("/orders", "/customers"):
        response = client.get(
            path, params={"country": COUNTRY, "page_size": 5000}, headers=auth(owner_token)
        )
        assert response.status_code == 422, path
        assert response.json()["error"]["code"] == "validation_error"


def test_the_ceiling_itself_is_allowed(client, owner_token, seeded):
    body = orders(client, owner_token, page_size=200)
    assert body["page_size"] == 200


def test_the_total_counts_every_match_not_just_this_page(client, owner_token, seeded):
    body = orders(client, owner_token, page_size=2, page=1)

    assert len(body["rows"]) == 2
    assert body["total"] == 5
    assert body["page"] == 1


def test_paging_forward_does_not_repeat_or_skip_a_guide(client, owner_token, seeded):
    """Ties in the sort key are why every ORDER BY ends in the tracking number."""
    seen: list[str] = []
    for page in (1, 2, 3):
        seen += [row["tracking_number"] for row in orders(
            client, owner_token, page=page, page_size=2, sort="days_open"
        )["rows"]]

    assert len(seen) == len(set(seen)) == 5


def test_a_page_past_the_end_is_empty_not_an_error(client, owner_token, seeded):
    body = orders(client, owner_token, page=99)
    assert body["rows"] == []
    assert body["total"] == 5


# =============================================================================
# Reading the table the way an operator does
# =============================================================================


def test_search_finds_a_guide_by_either_tracking_number(client, owner_token, seeded):
    """The customer quotes whichever number they were given - ours or the courier's."""
    own = orders(client, owner_token, search="EC-0001")["rows"]
    assert [row["tracking_number"] for row in own] == ["EC-0001"]

    courier = orders(client, owner_token, search="SERV-9001")["rows"]
    assert [row["tracking_number"] for row in courier] == ["EC-0001"]


def test_only_open_returns_the_guides_still_in_the_street(client, owner_token, seeded):
    body = orders(client, owner_token, only_open=True)

    assert [row["tracking_number"] for row in body["rows"]] == ["EC-0005"]
    assert body["rows"][0]["is_terminal"] is False
    assert body["rows"][0]["days_open"] is not None


def test_a_closed_guide_reports_no_days_open(client, owner_token, seeded):
    """A delivered guide is not open any number of days, and 0 would read as
    "delivered same day"."""
    row = orders(client, owner_token, search="EC-0001")["rows"][0]
    assert row["is_terminal"] is True
    assert row["days_open"] is None


def test_the_status_filter_returns_only_that_status(client, owner_token, seeded):
    body = orders(client, owner_token, status="delivered")

    assert body["total"] == 3
    assert all(row["status_code"] == "delivered" for row in body["rows"])
    assert all(row["status_label"] == "Entregada" for row in body["rows"])


def test_the_group_filter_uses_the_five_words(client, owner_token, seeded):
    """`group=entregada` is what the screen's "Estado" picker sends (migration 045):
    one of five words, not a canonical code and not a bucket."""
    body = orders(client, owner_token, group="entregada")
    assert body["total"] == 3
    assert all(row["status_group"] == "entregada" for row in body["rows"])

    moving = orders(client, owner_token, group="en_transito")
    assert [row["tracking_number"] for row in moving["rows"]] == ["EC-0005"]
    assert moving["rows"][0]["status_group"] == "en_transito"

    returned = orders(client, owner_token, group="devolucion")
    assert [row["tracking_number"] for row in returned["rows"]] == ["EC-0003"]

    # Every row carries its group, whatever the filter.
    for row in orders(client, owner_token)["rows"]:
        assert row["status_group"] in {
            "entregada", "devolucion", "en_transito", "novedad", "indemnizacion"
        }


def test_an_unknown_group_is_refused(client, owner_token, seeded):
    response = client.get(
        "/orders", params={"country": "EC", "group": "en_calle"}, headers=auth(owner_token)
    )
    assert response.status_code == 422


def test_the_date_range_filters_on_when_the_guide_was_created(client, owner_token, seeded):
    body = orders(
        client,
        owner_token,
        from_date=str(TODAY - timedelta(days=9)),
        to_date=str(TODAY),
    )
    assert {row["tracking_number"] for row in body["rows"]} == {"EC-0003", "EC-0005"}


def test_the_grade_filter_reaches_across_to_the_customer(client, owner_token, seeded):
    """Grade belongs to the person, not the parcel, so it is looked up where it
    is computed - and must not duplicate a guide while doing so."""
    body = orders(client, owner_token, grade="regular")

    assert body["rows"], "Ana entrega 2 de 3 cerradas: 'regular'"
    assert {row["tracking_number"] for row in body["rows"]} == {"EC-0001", "EC-0002", "EC-0003"}
    assert body["total"] == 3


def test_an_invented_grade_is_refused_before_touching_sql(client, owner_token):
    response = client.get(
        "/orders", params={"country": COUNTRY, "grade": "buenisimo"}, headers=auth(owner_token)
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_an_invented_sort_is_refused(client, owner_token):
    response = client.get(
        "/orders", params={"country": COUNTRY, "sort": "revenue"}, headers=auth(owner_token)
    )
    assert response.status_code == 422


def test_sorting_by_contribution_puts_the_best_guide_first(client, owner_token, seeded):
    rows = orders(client, owner_token, sort="contribution")["rows"]
    values = [row["contribution"] for row in rows if row["contribution"] is not None]

    assert values == sorted(values, reverse=True)
    assert rows[0]["tracking_number"] == "EC-0004", "80 - 7 - 12 es la mejor"


def test_money_crosses_the_wire_as_numbers_not_strings(client, owner_token, seeded):
    row = orders(client, owner_token, search="EC-0004")["rows"][0]

    for field in ("revenue_amount", "freight_amount", "cogs_amount", "contribution"):
        assert isinstance(row[field], int | float), field
    assert row["contribution"] == 61.0


def test_the_country_is_required(client, owner_token):
    for path in ("/orders", "/customers"):
        assert client.get(path, headers=auth(owner_token)).status_code == 422, path


# =============================================================================
# The detail card
# =============================================================================


def test_the_detail_card_carries_the_whole_guide(client, owner_token, seeded):
    body = client.get(f"/orders/{seeded['ana_delivered']}", headers=auth(owner_token)).json()
    order = body["order"]

    assert order["tracking_number"] == "EC-0001"
    assert order["carrier_tracking_number"] == "SERV-9001"
    assert order["product_name"] == "Faja Ecuador"
    assert order["city_name"] == ANA["city"]
    assert order["status_label"] == "Entregada"
    assert order["currency_code"] == CURRENCY


def test_the_timeline_interleaves_status_and_money_in_date_order(
    client, owner_token, seeded
):
    """A payout and the delivery that caused it usually share a date. Reading
    the payout first makes the card say the money arrived before the parcel."""
    body = client.get(f"/orders/{seeded['ana_delivered']}", headers=auth(owner_token)).json()
    timeline = body["timeline"]

    assert len(timeline) == 2
    assert [event["event_kind"] for event in timeline] == ["estado", "dinero"]
    assert [event["event_date"] for event in timeline] == sorted(
        event["event_date"] for event in timeline
    )
    assert timeline[1]["amount"] == 60.0
    assert timeline[1]["reference"] == "REC-001"


def test_a_guide_with_no_movements_still_has_a_timeline(client, owner_token, seeded):
    body = client.get(f"/orders/{seeded['bruno_open']}", headers=auth(owner_token)).json()
    assert [event["event_kind"] for event in body["timeline"]] == ["estado"]


def test_an_unknown_guide_is_a_clean_404(client, owner_token):
    response = client.get(
        "/orders/00000000-0000-0000-0000-000000000000", headers=auth(owner_token)
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# =============================================================================
# Customers
# =============================================================================


def test_the_same_phone_number_is_one_customer_not_three_guides(
    client, owner_token, seeded
):
    body = customers(client, owner_token)
    ana = next(row for row in body["rows"] if row["customer_hash"] == seeded["ana_hash"])

    assert body["total"] == 2, "cinco guías, dos personas"
    assert ana["orders"] == 3
    assert ana["delivered"] == 2
    assert ana["returned"] == 1
    assert ana["customer_name"] == ANA["name"]


def test_the_delivery_rate_ignores_orders_still_in_transit(client, owner_token, seeded):
    """Bruno has one delivered and one moving. 100%, not 50%: an open order is
    not a failed one."""
    body = customers(client, owner_token)
    bruno = next(row for row in body["rows"] if row["customer_hash"] == seeded["bruno_hash"])

    assert bruno["open_orders"] == 1
    assert bruno["delivery_rate_pct"] == 100.0


def test_a_customer_with_too_little_history_is_graded_nuevo_not_scored(
    client, owner_token, seeded
):
    """With one closed order there is no statistical basis for a number."""
    body = customers(client, owner_token)
    bruno = next(row for row in body["rows"] if row["customer_hash"] == seeded["bruno_hash"])

    assert bruno["customer_grade"] == "nuevo"


def test_a_customer_who_returns_one_in_three_is_graded_regular(
    client, owner_token, seeded
):
    """Two of three delivered is 67%: 'regular', not 'bueno'. The bands are
    deliberately coarse - a grade the operator can act on, not a score."""
    body = customers(client, owner_token)
    ana = next(row for row in body["rows"] if row["customer_hash"] == seeded["ana_hash"])

    assert ana["delivery_rate_pct"] == 66.7
    assert ana["customer_grade"] == "regular"


def test_the_grade_filter_returns_only_that_grade(client, owner_token, seeded):
    body = customers(client, owner_token, grade="regular")

    assert [row["customer_hash"] for row in body["rows"]] == [seeded["ana_hash"]]
    assert body["total"] == 1


def test_min_orders_drops_the_one_off_buyers(client, owner_token, seeded):
    body = customers(client, owner_token, min_orders=3)
    assert [row["customer_hash"] for row in body["rows"]] == [seeded["ana_hash"]]


def test_sorting_customers_by_orders_puts_the_most_frequent_first(
    client, owner_token, seeded
):
    rows = customers(client, owner_token, sort="orders")["rows"]
    assert [row["orders"] for row in rows] == sorted(
        (row["orders"] for row in rows), reverse=True
    )


def test_a_customer_card_carries_their_own_guides_newest_first(
    client, owner_token, seeded
):
    body = client.get(
        f"/customers/{seeded['ana_hash']}", headers=auth(owner_token)
    ).json()

    assert body["customer"]["orders"] == 3
    assert [order["tracking_number"] for order in body["orders"]] == [
        "EC-0003", "EC-0002", "EC-0001"
    ]
    assert all(order["customer_phone"] == ANA["phone"] for order in body["orders"])


def test_an_unknown_customer_is_a_clean_404(client, owner_token):
    response = client.get(f"/customers/{'a' * 64}", headers=auth(owner_token))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_something_that_is_not_a_hash_never_reaches_sql(client, owner_token):
    """The path is a char(64) of hex or it is a 422. Not a database round trip."""
    for bad in ("abc", "'; DROP TABLE core.shipment; --", "z" * 64):
        response = client.get(f"/customers/{bad}", headers=auth(owner_token))
        assert response.status_code == 422, bad


# =============================================================================
# Contribution, split
# =============================================================================


def test_the_split_separates_what_closed_from_what_is_still_moving(
    client, owner_token, seeded
):
    """Four closed guides and one in the street. Summing both makes a young
    cohort read as a loss; the split says which is which."""
    response = client.get(
        "/kpis/contribution-split", params={"country": COUNTRY}, headers=auth(owner_token)
    )
    assert response.status_code == 200, response.text
    # Every KPI answers inside the same envelope: {rows, date_basis, date_from, date_to}.
    row = response.json()["rows"][0]

    assert row["shipments"] == 5
    assert row["closed_shipments"] == 4
    assert row["open_shipments"] == 1
    # EC-0005: freight 7 + product 12, already spent, nothing collected yet.
    assert row["capital_in_street"] == 19.0
    # 42 + 27 + 61 de las entregadas, menos 23 de la devuelta que sólo costó.
    assert row["realised_contribution"] == 107.0
    assert row["maturity_pct"] == 80.0
    assert row["currency_code"] == CURRENCY


def test_the_split_is_a_number_not_a_string(client, owner_token, seeded):
    response = client.get(
        "/kpis/contribution-split", params={"country": COUNTRY}, headers=auth(owner_token)
    )
    row = response.json()["rows"][0]
    assert isinstance(row["net_contribution"], int | float)


def test_a_viewer_can_read_the_split(client, viewer_token, seeded):
    """It is an aggregate. No person is in it, so no role gate belongs on it."""
    response = client.get(
        "/kpis/contribution-split", params={"country": COUNTRY}, headers=auth(viewer_token)
    )
    assert response.status_code == 200
    assert response.json()["rows"], "un agregado no lleva a nadie dentro"


# =============================================================================
# Degrading without a key
# =============================================================================


def test_without_an_encryption_key_the_page_still_renders(client, owner_token, seeded):
    """A deployment that never configured the key must not 500 the orders page.

    Contact reads as absent and `pii_visible` says why, which is a page the
    operator can act on. A stack trace is not.
    """
    from pipeline.crypto import _fernet

    original = os.environ.pop("PII_ENCRYPTION_KEY")
    _fernet.cache_clear()
    try:
        body = orders(client, owner_token)

        assert body["pii_visible"] is False
        assert body["rows"], "las guías siguen ahí; sólo el contacto no se puede leer"
        assert all(row["customer_name"] is None for row in body["rows"])
        assert all(row["customer_ref"] is not None for row in body["rows"])
    finally:
        os.environ["PII_ENCRYPTION_KEY"] = original
        _fernet.cache_clear()


# =============================================================================
# Anonymous callers
# =============================================================================


def test_every_endpoint_refuses_an_anonymous_caller(client, seeded):
    assert client.get("/orders", params={"country": COUNTRY}).status_code == 401
    assert client.get(f"/orders/{seeded['ana_delivered']}").status_code == 401
    assert client.get("/customers", params={"country": COUNTRY}).status_code == 401
    assert client.get(f"/customers/{seeded['ana_hash']}").status_code == 401


# =============================================================================
# The customer detail card: the address is what the parcel needs
#
# Everything below lives in PE and CO on purpose. The EC fixture is what every
# count in this file is asserted against, so a new guide there would make an
# unrelated test fail for a reason that has nothing to do with what it tests.
# =============================================================================

PERU = {"name": "Dora Quispe", "phone": "0912223344", "doc": "45678912",
        "address": "Jr. de la Unión 820", "city": "Lima", "province": "Lima"}
NOMAD = {"name": "Carla Rojas", "phone": "0955667788", "doc": "80081357",
         "address": "Calle 72 #10-34", "city": "Bogotá", "province": "Cundinamarca"}

CORRECTED_ADDRESS = "Av. Arequipa 4550, Miraflores"


def _insert_geo(cur, *, tenant, country, level1, city):
    """A geo row, because `province_name` comes from core.geo, not from the guide."""
    cur.execute(
        """
        INSERT INTO core.geo
            (tenant_id, country_code, level1_name, level1_norm, city_name, city_normalized)
        VALUES (%s, %s, %s, core.normalize_text(%s), %s, core.normalize_text(%s))
        ON CONFLICT (tenant_id, country_code, level1_norm, city_normalized) DO NOTHING
        RETURNING id
        """,
        (tenant, country, level1, level1, city, city),
    )
    row = cur.fetchone()
    if row is not None:
        return row[0]

    cur.execute(
        "SELECT id FROM core.geo WHERE tenant_id = %s AND country_code = %s "
        "AND city_normalized = core.normalize_text(%s)",
        (tenant, country, city),
    )
    return cur.fetchone()[0]


@pytest.fixture(scope="module")
def pe_customer(client, api_dsn, tenant_id, seeded) -> dict:
    """A customer who moved, and whose newest guide has no document.

    Two guides, deliberately asymmetric:

        older  -> original address, document present
        newer  -> corrected address, document MISSING

    That shape is what proves each field resolves on its own. Taking "the
    newest guide" as a whole row would show the new address and no document,
    even though the document is perfectly well known from the older one.
    """
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute(
            "INSERT INTO core.workspace_country (tenant_id, country_code) VALUES (%s, 'PE') "
            "ON CONFLICT DO NOTHING",
            (tenant_id,),
        )
        cur.execute(
            "SELECT id FROM core.connection WHERE tenant_id = %s LIMIT 1", (tenant_id,)
        )
        connection_id = cur.fetchone()[0]
        geo_id = _insert_geo(
            cur, tenant=tenant_id, country="PE",
            level1=PERU["province"], city=PERU["city"],
        )

        _insert_shipment(
            cur, tenant=tenant_id, connection=connection_id, tracking="PE-0001",
            person=PERU, status="delivered", created=TODAY - timedelta(days=30),
            delivered=TODAY - timedelta(days=27), declared=90, freight=8, cogs=15,
            country="PE", currency="PEN", geo_id=geo_id,
        )
        _insert_shipment(
            cur, tenant=tenant_id, connection=connection_id, tracking="PE-0002",
            person=PERU, status="delivered", created=TODAY - timedelta(days=4),
            delivered=TODAY - timedelta(days=1), declared=90, freight=8, cogs=15,
            country="PE", currency="PEN", geo_id=geo_id,
            address=CORRECTED_ADDRESS, document="",
        )
        conn.commit()

    return {"hash": _hash_customer(PERU["phone"])}


def customer_card(client, token: str, customer_hash: str, **params) -> dict:
    response = client.get(
        f"/customers/{customer_hash}", params=params, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_card_carries_the_address_the_parcel_is_going_to(
    client, owner_token, pe_customer
):
    card = customer_card(client, owner_token, pe_customer["hash"])["customer"]

    assert card["customer_name"] == PERU["name"]
    assert card["customer_phone"] == PERU["phone"]
    assert card["customer_address"] == CORRECTED_ADDRESS
    assert card["customer_city"] == PERU["city"]
    assert card["customer_province"] == PERU["province"]


def test_a_corrected_address_is_the_one_shown(client, owner_token, pe_customer):
    """A customer who told you they moved must be seen moved.

    The old address is still on the older guide and still readable there; what
    must not happen is the card offering it as where to send the next parcel.
    """
    card = customer_card(client, owner_token, pe_customer["hash"])["customer"]

    assert card["customer_address"] == CORRECTED_ADDRESS
    assert card["customer_address"] != PERU["address"]


def test_each_contact_field_resolves_on_its_own(client, owner_token, pe_customer):
    """The newest guide has no document. The document is still known.

    Resolving the whole contact block from one "latest" row would blank a field
    the operation has on file, and the operator would go looking for it in a
    spreadsheet.
    """
    card = customer_card(client, owner_token, pe_customer["hash"])["customer"]

    assert card["customer_address"] == CORRECTED_ADDRESS, "la dirección viene de la guía nueva"
    assert card["customer_document"] == PERU["doc"], "el documento viene de la que sí lo tenía"


def test_the_customers_table_cannot_return_an_address_at_all(
    client, owner_token, pe_customer
):
    """Not null - absent. A page of 200 rows must not be one flag away from
    decrypting 200 addresses."""
    rows = customers(client, owner_token, country="PE")["rows"]
    assert rows

    for row in rows:
        assert "customer_address" not in row
        assert "customer_document" not in row
    assert CORRECTED_ADDRESS not in client.get(
        "/customers", params={"country": "PE"}, headers=auth(owner_token)
    ).text


def test_a_viewer_gets_the_city_but_never_the_street(client, viewer_token, pe_customer):
    """The line the two sides of this are drawn on.

    A city is not an identity - `main_city` has been visible to every role since
    the customers table existed, and a viewer needs it to read the table at all.
    A street address and a national ID number identify a household, so they stay
    behind the role gate with the name and the phone.
    """
    response = client.get(
        f"/customers/{pe_customer['hash']}", headers=auth(viewer_token)
    )
    assert response.status_code == 200
    card = response.json()["customer"]

    assert card["customer_city"] == PERU["city"]
    assert card["customer_province"] == PERU["province"]
    assert card["main_city"] is not None

    assert card["customer_address"] is None
    assert card["customer_document"] is None
    assert CORRECTED_ADDRESS not in response.text
    assert PERU["doc"] not in response.text


def test_the_card_logs_the_customer_plus_every_guide_it_decrypted(
    client, owner_token, tenant_id, api_dsn, pe_customer
):
    """`record_count` has to grow when the card decrypts more.

    It counts RECORDS, not people: one customer row plus each guide of theirs
    that carried contact data. It is always one person here - the endpoint takes
    a single hash - so the count answers "how much left", which is the question
    an incident asks.
    """
    before = len(pii_access_rows(api_dsn, tenant_id))
    customer_card(client, owner_token, pe_customer["hash"])
    rows = pii_access_rows(api_dsn, tenant_id)

    assert len(rows) == before + 1
    endpoint, record_count, _ = rows[-1]
    assert endpoint == "GET /customers/{customer_hash}"
    assert record_count == 3, "un cliente + sus dos guías"


def test_a_viewers_card_still_leaves_no_row(
    client, viewer_token, tenant_id, api_dsn, pe_customer
):
    before = len(pii_access_rows(api_dsn, tenant_id))
    client.get(f"/customers/{pe_customer['hash']}", headers=auth(viewer_token))
    assert len(pii_access_rows(api_dsn, tenant_id)) == before


def test_the_access_log_still_never_stores_the_address(api_dsn, tenant_id, pe_customer):
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute("SELECT * FROM raw.pii_access WHERE tenant_id = %s", (tenant_id,))
        rows = cur.fetchall()

    flat = " ".join(str(value) for row in rows for value in row)
    for secret in (CORRECTED_ADDRESS, PERU["address"], PERU["doc"], PERU["phone"]):
        assert secret not in flat


# =============================================================================
# One customer, two countries, two currencies
# =============================================================================


@pytest.fixture(scope="module")
def two_country_customer(client, api_dsn, tenant_id, pe_customer) -> dict:
    """The same phone number buying in Colombia and in Peru.

    The hash is deterministic and has no country in it, so this is genuinely
    ONE person. The metrics view still keeps them as two rows, because the two
    halves of their history are denominated in different money.
    """
    with psycopg.connect(api_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        cur.execute(
            "INSERT INTO core.workspace_country (tenant_id, country_code) VALUES (%s, 'CO') "
            "ON CONFLICT DO NOTHING",
            (tenant_id,),
        )
        cur.execute(
            "SELECT id FROM core.connection WHERE tenant_id = %s LIMIT 1", (tenant_id,)
        )
        connection_id = cur.fetchone()[0]

        # Colombia: two guides, in pesos.
        for n, created in ((1, 25), (2, 18)):
            _insert_shipment(
                cur, tenant=tenant_id, connection=connection_id, tracking=f"CO-000{n}",
                person=NOMAD, status="delivered", created=TODAY - timedelta(days=created),
                delivered=TODAY - timedelta(days=created - 3),
                declared=200000, freight=15000, cogs=40000,
                country="CO", currency="COP",
            )
        # Peru: one guide, in soles.
        _insert_shipment(
            cur, tenant=tenant_id, connection=connection_id, tracking="PE-0003",
            person=NOMAD, status="delivered", created=TODAY - timedelta(days=6),
            delivered=TODAY - timedelta(days=3), declared=120, freight=9, cogs=20,
            country="PE", currency="PEN",
        )
        conn.commit()

    return {"hash": _hash_customer(NOMAD["phone"])}


def test_asking_for_a_country_returns_that_countrys_metrics(
    client, owner_token, two_country_customer
):
    colombia = customer_card(
        client, owner_token, two_country_customer["hash"], country="CO"
    )["customer"]
    peru = customer_card(
        client, owner_token, two_country_customer["hash"], country="PE"
    )["customer"]

    assert colombia["orders"] == 2
    assert colombia["currency_code"] == "COP"
    assert colombia["revenue"] == 400000.0

    assert peru["orders"] == 1
    assert peru["currency_code"] == "PEN"
    assert peru["revenue"] == 120.0


def test_a_customer_in_two_countries_never_sums_two_currencies(
    client, owner_token, two_country_customer
):
    """400.000 COP and 120 PEN do not add up to 400.120 of anything.

    This is why `mart.v_customer_metrics` is one row per customer PER country
    and why the card never merges them. A single mixed row would not be a number
    that is slightly wrong - it would be a number that means nothing, printed
    next to a currency symbol that makes it look like it does.
    """
    for country, currency, revenue, orders in (
        ("CO", "COP", 400000.0, 2),
        ("PE", "PEN", 120.0, 1),
    ):
        card = customer_card(
            client, owner_token, two_country_customer["hash"], country=country
        )["customer"]

        assert card["currency_code"] == currency
        assert card["revenue"] == revenue
        assert card["orders"] == orders
        assert card["revenue"] != 400120.0, "jamás sumar monedas distintas"

    # And the total across both is never reported by anything.
    assert sum(
        customer_card(client, owner_token, two_country_customer["hash"], country=c)[
            "customer"
        ]["orders"]
        for c in ("CO", "PE")
    ) == 3, "tres guías en total, pero repartidas en dos filas, nunca en una"


def test_the_guides_listed_are_only_that_countrys(
    client, owner_token, two_country_customer
):
    """The orders under the card have to match the metrics above them.

    Showing Peruvian guides under Colombian totals is how an operator concludes
    the numbers are broken - and they would be right.
    """
    colombia = customer_card(
        client, owner_token, two_country_customer["hash"], country="CO"
    )
    peru = customer_card(
        client, owner_token, two_country_customer["hash"], country="PE"
    )

    assert {o["tracking_number"] for o in colombia["orders"]} == {"CO-0001", "CO-0002"}
    assert {o["tracking_number"] for o in peru["orders"]} == {"PE-0003"}
    assert all(o["currency_code"] == "COP" for o in colombia["orders"])
    assert all(o["currency_code"] == "PEN" for o in peru["orders"])

    # The count on the card and the number of guides under it agree.
    assert colombia["customer"]["orders"] == len(colombia["orders"])
    assert peru["customer"]["orders"] == len(peru["orders"])


def test_without_a_country_the_busiest_one_wins(client, owner_token, two_country_customer):
    """The fallback, for a caller that has only a hash. Still one country."""
    card = customer_card(client, owner_token, two_country_customer["hash"])["customer"]

    assert card["currency_code"] == "COP", "2 guías en CO contra 1 en PE"
    assert card["orders"] == 2


def test_the_same_person_keeps_the_same_label_in_both_countries(
    client, owner_token, two_country_customer
):
    """`customer_ref` comes from the hash, and the hash has no country in it."""
    colombia = customer_card(
        client, owner_token, two_country_customer["hash"], country="CO"
    )["customer"]
    peru = customer_card(
        client, owner_token, two_country_customer["hash"], country="PE"
    )["customer"]

    assert colombia["customer_ref"] == peru["customer_ref"]
    assert colombia["customer_hash"] == peru["customer_hash"]


def test_the_customers_table_never_mixes_countries_either(
    client, owner_token, two_country_customer
):
    colombian = customers(client, owner_token, country="CO")["rows"]
    assert all(row["currency_code"] == "COP" for row in colombian)
    assert all(row["revenue"] != 400120.0 for row in colombian)
