"""Row-Level Security: proving tenant A cannot read tenant B.

These tests bypass the API entirely and talk to PostgreSQL as the role the API
connects with. That is the point: if the application layer is compromised or
simply buggy, these policies are what still stands between two customers' data.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn, seed_workspace

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.postgres

TENANT_A = UUID("aaaaaaaa-1000-4000-a000-00000000000a")
TENANT_B = UUID("bbbbbbbb-2000-4000-b000-00000000000b")
CONN_A = UUID("aaaaaaaa-3000-4000-a000-00000000000a")
CONN_B = UUID("bbbbbbbb-4000-4000-b000-00000000000b")

# Tables that must refuse cross-tenant reads.
PROTECTED_TABLES = [
    "core.shipment",
    "core.movement",
    "core.ad_spend",
    "core.cs_interaction",
    "core.carrier",
    "core.geo",
    "core.product",
    "core.supplier",
    "core.store",
    "core.connection",
    "core.workspace_country",
    "raw.load_batch",
    "raw.upload_job",
]


@pytest.fixture(scope="module")
def rls_dsn() -> str:
    if not resolve_test_dsn():
        pytest.skip("No DATABASE_URL configured")
    try:
        return recreate_test_database()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")


@pytest.fixture(scope="module")
def two_tenants(rls_dsn):
    """Two tenants, each with one shipment, seeded in the service context."""
    conn = psycopg.connect(rls_dsn, autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', false)")
    conn.commit()

    seed_workspace(conn, tenant_id=TENANT_A, connection_id=CONN_A, slug="rls-a")
    seed_workspace(conn, tenant_id=TENANT_B, connection_id=CONN_B, slug="rls-b")

    with conn.cursor() as cur:
        for tenant, connection, tracking in (
            (TENANT_A, CONN_A, "A-0001"),
            (TENANT_B, CONN_B, "B-0001"),
        ):
            cur.execute(
                """
                INSERT INTO core.shipment
                    (tenant_id, connection_id, country_code, tracking_number, status_code,
                     created_date, currency_code, declared_value)
                VALUES (%s, %s, 'CO', %s, 'delivered', CURRENT_DATE - 5, 'COP', 100000)
                ON CONFLICT (connection_id, tracking_number) DO NOTHING
                """,
                (tenant, connection, tracking),
            )
    conn.commit()
    conn.close()

    yield rls_dsn


def session(dsn: str, tenant: UUID | None = None, service: bool = False):
    conn = psycopg.connect(dsn, autocommit=True)
    with conn.cursor() as cur:
        if tenant is not None:
            cur.execute("SELECT set_config('norte.tenant_id', %s, false)", (str(tenant),))
        if service:
            cur.execute("SELECT set_config('norte.service', 'on', false)")
    return conn


def count(conn, table: str, where: str = "", params: tuple = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} {where}", params)  # noqa: S608
        return int(cur.fetchone()[0])


# =============================================================================
# The headline test
# =============================================================================


def test_tenant_a_cannot_read_tenant_b(two_tenants):
    conn = session(two_tenants, tenant=TENANT_A)
    try:
        mine = count(conn, "core.shipment", "WHERE tracking_number = %s", ("A-0001",))
        theirs = count(conn, "core.shipment", "WHERE tracking_number = %s", ("B-0001",))

        assert mine == 1, "tenant A must see its own shipment"
        assert theirs == 0, "tenant A must NOT see tenant B's shipment"
    finally:
        conn.close()


def test_tenant_b_cannot_read_tenant_a(two_tenants):
    conn = session(two_tenants, tenant=TENANT_B)
    try:
        assert count(conn, "core.shipment", "WHERE tracking_number = %s", ("B-0001",)) == 1
        assert count(conn, "core.shipment", "WHERE tracking_number = %s", ("A-0001",)) == 0
    finally:
        conn.close()


def test_an_explicit_cross_tenant_filter_still_returns_nothing(two_tenants):
    """Even asking for it by primary key. The policy is not a WHERE clause you
    can talk your way around."""
    conn = session(two_tenants, tenant=TENANT_A)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tracking_number FROM core.shipment WHERE tenant_id = %s", (TENANT_B,)
            )
            assert cur.fetchall() == []
    finally:
        conn.close()


def test_no_tenant_context_sees_nothing(two_tenants):
    """Fail closed: a connection that forgets to set the tenant reads zero rows,
    not everything."""
    conn = session(two_tenants)
    try:
        assert count(conn, "core.shipment") == 0
        assert count(conn, "core.connection") == 0
        assert count(conn, "raw.load_batch") == 0
    finally:
        conn.close()


@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_every_protected_table_is_empty_without_a_tenant(two_tenants, table):
    conn = session(two_tenants)
    try:
        assert count(conn, table) == 0, f"{table} leaked rows with no tenant context"
    finally:
        conn.close()


@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_every_protected_table_has_forced_rls(two_tenants, table):
    """ENABLE without FORCE would let the table owner - which is the role the API
    connects as - bypass every policy silently."""
    schema, name = table.split(".")
    conn = session(two_tenants, service=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s
                """,
                (schema, name),
            )
            enabled, forced = cur.fetchone()
        assert enabled, f"{table} does not have RLS enabled"
        assert forced, f"{table} does not have RLS FORCED"
    finally:
        conn.close()


def test_tenant_cannot_insert_rows_for_another_tenant(two_tenants):
    """WITH CHECK: you cannot write into someone else's tenant either."""
    conn = session(two_tenants, tenant=TENANT_A)
    try:
        with conn.cursor() as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                """
                INSERT INTO core.shipment
                    (tenant_id, connection_id, country_code, tracking_number, status_code,
                     created_date, currency_code)
                VALUES (%s, %s, 'CO', 'SMUGGLED-1', 'delivered', CURRENT_DATE, 'COP')
                """,
                (TENANT_B, CONN_B),
            )
    finally:
        conn.close()


def test_service_context_sees_every_tenant(two_tenants):
    """The worker legitimately processes all tenants; that path must still work."""
    conn = session(two_tenants, service=True)
    try:
        assert count(conn, "core.shipment") >= 2
    finally:
        conn.close()


# =============================================================================
# The read-only role used by NL->SQL
# =============================================================================


def test_readonly_role_exists_with_its_guard_rails(two_tenants):
    conn = session(two_tenants, service=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rolconfig FROM pg_roles WHERE rolname = 'norte_readonly'"
            )
            row = cur.fetchone()
        assert row is not None, "norte_readonly was not created"

        config = " ".join(row[0] or [])
        assert "search_path=mart" in config.replace(" ", "")
        assert "statement_timeout=5s" in config.replace(" ", "")
        assert "default_transaction_read_only=on" in config.replace(" ", "")
    finally:
        conn.close()


def test_readonly_role_has_no_grants_on_core_or_raw(two_tenants):
    conn = session(two_tenants, service=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                FROM information_schema.role_table_grants
                WHERE grantee = 'norte_readonly' AND table_schema IN ('core', 'raw', 'stg')
                """
            )
            assert cur.fetchone()[0] == 0, "norte_readonly must not hold grants outside mart"

            cur.execute(
                """
                SELECT count(*)
                FROM information_schema.role_table_grants
                WHERE grantee = 'norte_readonly' AND table_schema = 'mart'
                  AND privilege_type <> 'SELECT'
                """
            )
            assert cur.fetchone()[0] == 0, "norte_readonly must hold SELECT and nothing else"
    finally:
        conn.close()
