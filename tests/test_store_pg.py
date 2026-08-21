"""The ingestion engine against a real PostgreSQL, plus store-parity checks.

The point of this suite is not "does SQL work". It is: does PostgresStore behave
EXACTLY like MemoryStore? The merge policy exists twice - once in Python, once in
a generated ON CONFLICT clause - and the only thing keeping those honest is
running the same fixture through both and diffing the result.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from uuid import UUID

import pytest

from pipeline.ingest import BatchAlreadyExists, IngestEngine, MemoryStore
from pipeline.models import BatchKind
from tests.conftest import COUNTRY, CURRENCY, PLATFORM, TODAY
from tests.pg_helpers import recreate_test_database, resolve_test_dsn, seed_workspace, truncate_data

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.postgres

TENANT_A = UUID("aaaaaaaa-0000-0000-0000-000000000001")
TENANT_B = UUID("bbbbbbbb-0000-0000-0000-000000000002")
CONN_A = UUID("aaaaaaaa-1111-1111-1111-111111111111")
CONN_B = UUID("bbbbbbbb-1111-1111-1111-111111111111")


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    if not resolve_test_dsn():
        pytest.skip("No DATABASE_URL configured; skipping PostgreSQL tests")
    try:
        return recreate_test_database()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")


def _service_connection(dsn: str) -> psycopg.Connection:
    """A connection in the ingestion service context.

    The ingestion queue legitimately writes for any tenant, so it declares
    `norte.service` and row-level security lets it through (migration 007).
    Tests must use the same context the real code path does - otherwise they
    would be testing a path that does not exist.
    """
    conn = psycopg.connect(dsn, autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', false)")
    conn.commit()
    return conn


@pytest.fixture
def pg_conn(pg_dsn):
    conn = _service_connection(pg_dsn)
    seed_workspace(conn, tenant_id=TENANT_A, connection_id=CONN_A, slug="tenant-a")
    seed_workspace(conn, tenant_id=TENANT_B, connection_id=CONN_B, slug="tenant-b")
    truncate_data(conn)
    seed_workspace(conn, tenant_id=TENANT_A, connection_id=CONN_A, slug="tenant-a")
    seed_workspace(conn, tenant_id=TENANT_B, connection_id=CONN_B, slug="tenant-b")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def pg_store(pg_conn):
    from pipeline.store_pg import PostgresStore

    return PostgresStore(pg_conn)


@pytest.fixture
def pg_engine(pg_store, pii_salt):
    return IngestEngine(pg_store, pii_salt=pii_salt, today=TODAY)


def _ingest(engine, payload, name, kind=BatchKind.SHIPMENTS, tenant=TENANT_A, conn_id=CONN_A):
    return engine.ingest(
        payload=payload,
        source_name=name,
        kind=kind,
        tenant_id=tenant,
        connection_id=conn_id,
        country_code=COUNTRY,
        platform_code=PLATFORM,
        default_currency=CURRENCY,
    )


def _rows(conn, query, params=()):
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


# =============================================================================
# Basic persistence
# =============================================================================


def test_first_load_persists_every_row(pg_engine, pg_conn, guias_dia1):
    report = _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()

    assert report.rows_inserted == 10
    assert report.rows_failed == 0

    rows = _rows(pg_conn, "SELECT count(*) AS n FROM core.shipment")
    assert rows[0]["n"] == 10


def test_batch_is_recorded_with_its_report(pg_engine, pg_conn, guias_dia1):
    report = _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()

    batch = _rows(pg_conn, "SELECT * FROM raw.load_batch WHERE id = %s", (report.batch_id,))[0]
    assert batch["status"] == "ok"
    assert batch["rows_inserted"] == 10
    assert batch["content_hash"] == report.content_hash
    assert batch["report"]["rows"]["inserted"] == 10
    assert batch["finished_at"] is not None


def test_dimensions_are_resolved_and_deduplicated(pg_engine, pg_conn, guias_dia1):
    _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()

    carriers = _rows(pg_conn, "SELECT name_norm FROM core.carrier ORDER BY name_norm")
    assert [c["name_norm"] for c in carriers] == ["coordinadora", "envia", "interrapidisimo"]

    # Bogotá / BOGOTA / Bogota must be ONE geo row, not three.
    bogota = _rows(
        pg_conn, "SELECT count(*) AS n FROM core.geo WHERE city_normalized = 'bogota'"
    )
    assert bogota[0]["n"] == 1

    products = _rows(pg_conn, "SELECT count(*) AS n FROM core.product")
    assert products[0]["n"] == 2      # Faja Reductora, Reloj Inteligente


def test_product_alias_redirects_to_the_canonical_product(pg_engine, pg_conn, guias_dia1):
    _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()

    product_id = _rows(
        pg_conn, "SELECT id FROM core.product WHERE name_norm = 'faja reductora'"
    )[0]["id"]

    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO core.product_alias (tenant_id, alias_norm, product_id) VALUES (%s, %s, %s)",
            (TENANT_A, "faja reductora x2", product_id),
        )
    pg_conn.commit()

    payload = (
        b"Guia;Fecha;Estado;Producto;Valor\n"
        b"E-5001;05/07/2026;ENTREGADO;FAJA REDUCTORA X2;89.900\n"
    )
    _ingest(pg_engine, payload, "alias.csv")
    pg_conn.commit()

    shipment = _rows(
        pg_conn, "SELECT product_id FROM core.shipment WHERE tracking_number = 'E-5001'"
    )[0]
    assert shipment["product_id"] == product_id

    total_products = _rows(pg_conn, "SELECT count(*) AS n FROM core.product")[0]["n"]
    assert total_products == 2, "the alias must not create a third product"


def test_customer_phone_never_reaches_the_database(pg_engine, pg_conn, guias_dia1):
    _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()

    hit = _rows(
        pg_conn,
        "SELECT count(*) AS n FROM core.shipment WHERE customer_hash LIKE %s",
        ("%3001234567%",),
    )
    assert hit[0]["n"] == 0

    hashes = _rows(pg_conn, "SELECT customer_hash FROM core.shipment WHERE customer_hash IS NOT NULL")
    assert len(hashes) == 10
    assert all(len(h["customer_hash"]) == 64 for h in hashes)


# =============================================================================
# Idempotence
# =============================================================================


def test_loading_the_same_file_twice_creates_no_duplicates(pg_engine, pg_conn, guias_dia1):
    first = _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()
    second = _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()

    assert first.rows_inserted == 10
    assert second.already_loaded is True
    assert second.rows_inserted == 0

    counts = _rows(
        pg_conn,
        "SELECT count(*) AS shipments, count(DISTINCT tracking_number) AS distinct_tracking "
        "FROM core.shipment",
    )[0]
    assert counts["shipments"] == 10
    assert counts["distinct_tracking"] == 10

    batches = _rows(pg_conn, "SELECT count(*) AS n FROM raw.load_batch")
    assert batches[0]["n"] == 1


def test_reexported_file_with_identical_rows_reports_skipped(pg_engine, pg_conn, guias_dia1):
    _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()

    report = _ingest(pg_engine, guias_dia1 + b"\n", "dia1_reexport.csv")
    pg_conn.commit()

    assert report.already_loaded is False
    assert report.rows_inserted == 0
    assert report.rows_updated == 0
    assert report.rows_skipped == 10


# =============================================================================
# Merge policy parity with MemoryStore
# =============================================================================


def test_status_advances_but_never_regresses(pg_engine, pg_conn, guias_dia1, guias_dia2):
    _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()
    _ingest(pg_engine, guias_dia2, "dia2.csv")
    pg_conn.commit()

    rows = {
        r["tracking_number"]: r
        for r in _rows(pg_conn, "SELECT tracking_number, status_code, delivered_at FROM core.shipment")
    }
    assert rows["E-1002"]["status_code"] == "returned"      # frozen, was terminal
    assert rows["E-1004"]["status_code"] == "delivered"     # advanced
    assert rows["E-1004"]["delivered_at"] is not None
    assert rows["E-1011"]["status_code"] == "delivered"     # new


def test_money_discrepancy_is_persisted(pg_engine, pg_conn, guias_dia1, guias_dia2):
    _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()
    report = _ingest(pg_engine, guias_dia2, "dia2.csv")
    pg_conn.commit()

    stored = _rows(
        pg_conn,
        "SELECT entity, entity_key, field_name, old_value, new_value "
        "FROM raw.load_discrepancy ORDER BY entity_key",
    )
    assert len(stored) == 1
    assert stored[0]["entity_key"] == "E-1003"
    assert stored[0]["field_name"] == "declared_value"
    assert Decimal(stored[0]["old_value"]) == Decimal("149900")
    assert Decimal(stored[0]["new_value"]) == Decimal("159900")

    # And the newest value won.
    value = _rows(
        pg_conn, "SELECT declared_value FROM core.shipment WHERE tracking_number = 'E-1003'"
    )[0]["declared_value"]
    assert value == Decimal("159900.00")

    assert len(report.discrepancies) == 1


def test_static_field_is_filled_but_never_overwritten(pg_engine, pg_conn):
    first = (
        b"Guia;Fecha;Estado;Ciudad;Valor\n"
        b"E-6001;01/07/2026;EN TRANSITO;Cali;50.000\n"
    )
    second = (
        b"Guia;Fecha;Estado;Ciudad;Producto;Valor\n"
        b"E-6001;01/07/2026;EN TRANSITO;Medellin;Reloj;50.000\n"
    )

    _ingest(pg_engine, first, "a.csv")
    pg_conn.commit()
    _ingest(pg_engine, second, "b.csv")
    pg_conn.commit()

    row = _rows(
        pg_conn,
        """
        SELECT g.city_name, p.name AS product_name
        FROM core.shipment s
        LEFT JOIN core.geo g ON g.id = s.geo_id
        LEFT JOIN core.product p ON p.id = s.product_id
        WHERE s.tracking_number = 'E-6001'
        """,
    )[0]
    assert row["city_name"] == "Cali"          # known already: not overwritten
    assert row["product_name"] == "Reloj"      # was missing: filled in


@pytest.mark.parametrize("fixture_name", ["guias_dia1", "guias_dia2"])
def test_both_stores_agree_row_for_row(request, pg_engine, pg_conn, pii_salt, fixture_name):
    """The parity check. Same bytes, both stores, identical outcome."""
    payload = request.getfixturevalue(fixture_name)

    memory_store = MemoryStore()
    memory_engine = IngestEngine(memory_store, pii_salt=pii_salt, today=TODAY)

    pg_report = _ingest(pg_engine, payload, "parity.csv")
    pg_conn.commit()
    memory_report = _ingest(memory_engine, payload, "parity.csv")

    assert pg_report.rows_total == memory_report.rows_total
    assert pg_report.rows_inserted == memory_report.rows_inserted
    assert pg_report.rows_updated == memory_report.rows_updated
    assert pg_report.rows_skipped == memory_report.rows_skipped
    assert pg_report.rows_failed == memory_report.rows_failed

    pg_rows = {
        r["tracking_number"]: r
        for r in _rows(
            pg_conn,
            "SELECT tracking_number, status_code, declared_value, quantity, created_date, "
            "customer_hash FROM core.shipment",
        )
    }
    memory_rows = {k[1]: v for k, v in memory_store.shipments.items()}

    assert pg_rows.keys() == memory_rows.keys()
    for tracking, pg_row in pg_rows.items():
        mem_row = memory_rows[tracking]
        assert pg_row["status_code"] == mem_row.status_code
        assert pg_row["created_date"] == mem_row.created_date
        assert pg_row["quantity"] == mem_row.quantity
        assert pg_row["customer_hash"] == mem_row.customer_hash
        if mem_row.declared_value is None:
            assert pg_row["declared_value"] is None
        else:
            assert Decimal(pg_row["declared_value"]) == Decimal(mem_row.declared_value)


def test_status_ladder_matches_the_python_mirror(pg_conn):
    """core.status_canon and mapping.STATUS_CANON must not drift apart."""
    from pipeline.mapping import STATUS_CANON

    rows = _rows(pg_conn, "SELECT code, sort_order, is_terminal, bucket FROM core.status_canon")
    assert len(rows) == len(STATUS_CANON)
    for row in rows:
        mirror = STATUS_CANON[row["code"]]
        assert row["sort_order"] == mirror.sort_order
        assert row["is_terminal"] == mirror.is_terminal
        assert row["bucket"] == mirror.bucket


def test_status_aliases_match_the_python_mirror(pg_conn):
    from pipeline.mapping import STATUS_ALIASES

    rows = _rows(pg_conn, "SELECT alias_norm, status_code FROM core.status_alias")
    for row in rows:
        mirrored = STATUS_ALIASES.get(row["alias_norm"])
        assert mirrored is not None, f"SQL alias {row['alias_norm']!r} missing from Python mirror"
        assert mirrored == row["status_code"], (
            f"alias {row['alias_norm']!r}: SQL says {row['status_code']}, Python says {mirrored}"
        )


def test_normalize_text_matches_between_python_and_sql(pg_conn):
    from pipeline.normalize import normalize_text

    samples = ["Bogotá", "BOGOTA", "  Valle  del   Cauca ", "Medellín", "Ñuñoa", "Cundinamarca"]
    for sample in samples:
        sql_value = _rows(pg_conn, "SELECT core.normalize_text(%s) AS v", (sample,))[0]["v"]
        assert sql_value == normalize_text(sample), f"mismatch on {sample!r}"


# =============================================================================
# Movements and orphans
# =============================================================================


def test_movements_link_and_orphans_wait(pg_engine, pg_conn, guias_dia1, movimientos):
    _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()
    report = _ingest(pg_engine, movimientos, "mov.csv", kind=BatchKind.MOVEMENTS)
    pg_conn.commit()

    assert report.rows_inserted == 10

    counts = _rows(
        pg_conn,
        "SELECT count(*) FILTER (WHERE shipment_id IS NOT NULL) AS linked, "
        "count(*) FILTER (WHERE shipment_id IS NULL) AS orphans FROM core.movement",
    )[0]
    assert counts["linked"] == 8
    assert counts["orphans"] == 2


def test_orphans_relink_when_the_shipment_arrives(
    pg_engine, pg_conn, guias_dia1, guias_dia2, movimientos
):
    _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()
    _ingest(pg_engine, movimientos, "mov.csv", kind=BatchKind.MOVEMENTS)
    pg_conn.commit()
    _ingest(pg_engine, guias_dia2, "dia2.csv")      # brings E-1011
    pg_conn.commit()

    orphans = _rows(
        pg_conn, "SELECT count(*) AS n FROM core.movement WHERE shipment_id IS NULL"
    )[0]["n"]
    assert orphans == 0


def test_movements_loaded_before_shipments_still_end_up_linked(
    pg_engine, pg_conn, guias_dia1, movimientos
):
    """The realistic order: money lands first, guides arrive with the weekly export."""
    _ingest(pg_engine, movimientos, "mov.csv", kind=BatchKind.MOVEMENTS)
    pg_conn.commit()

    all_orphans = _rows(
        pg_conn, "SELECT count(*) AS n FROM core.movement WHERE shipment_id IS NULL"
    )[0]["n"]
    assert all_orphans == 10

    _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()

    remaining = _rows(
        pg_conn, "SELECT count(*) AS n FROM core.movement WHERE shipment_id IS NULL"
    )[0]["n"]
    assert remaining == 2      # only E-1011, which is not in dia1


# =============================================================================
# Concurrency
# =============================================================================


def test_two_simultaneous_uploads_of_the_same_file_load_once(pg_dsn, guias_dia1, pii_salt):
    """Two users hit Subir at the same moment with the same file.

    Exactly one batch must exist afterwards, with exactly 10 shipments. The loser
    of the race gets `already_loaded`, which is the correct answer - not an error.
    """
    from pipeline.store_pg import PostgresStore

    setup = _service_connection(pg_dsn)
    seed_workspace(setup, tenant_id=TENANT_A, connection_id=CONN_A, slug="tenant-a")
    truncate_data(setup)
    seed_workspace(setup, tenant_id=TENANT_A, connection_id=CONN_A, slug="tenant-a")
    setup.close()

    barrier = threading.Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            conn = _service_connection(pg_dsn)
            try:
                engine = IngestEngine(PostgresStore(conn), pii_salt=pii_salt, today=TODAY)
                barrier.wait(timeout=20)
                report = _ingest(engine, guias_dia1, "simultaneo.csv")
                conn.commit()
                results.append(report)
            finally:
                conn.close()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, f"a concurrent load raised: {errors}"
    assert len(results) == 2

    loaded = [r for r in results if not r.already_loaded]
    skipped = [r for r in results if r.already_loaded]
    assert len(loaded) == 1, "exactly one load must win the race"
    assert len(skipped) == 1
    assert loaded[0].rows_inserted == 10

    verify = _service_connection(pg_dsn)
    try:
        counts = _rows(
            verify,
            "SELECT (SELECT count(*) FROM raw.load_batch) AS batches, "
            "(SELECT count(*) FROM core.shipment) AS shipments",
        )[0]
        assert counts["batches"] == 1
        assert counts["shipments"] == 10
    finally:
        verify.close()


def test_register_batch_raises_when_the_hash_is_already_claimed(pg_store, pg_conn):
    ctx = pg_store.register_batch(
        tenant_id=TENANT_A,
        connection_id=CONN_A,
        country_code=COUNTRY,
        platform_code=PLATFORM,
        source_name="a.csv",
        kind=BatchKind.SHIPMENTS,
        content_hash="a" * 64,
    )
    pg_conn.commit()
    assert ctx.batch_id is not None

    with pytest.raises(BatchAlreadyExists):
        pg_store.register_batch(
            tenant_id=TENANT_A,
            connection_id=CONN_A,
            country_code=COUNTRY,
            platform_code=PLATFORM,
            source_name="a_copia.csv",
            kind=BatchKind.SHIPMENTS,
            content_hash="a" * 64,
        )


def test_a_failing_row_does_not_abort_the_whole_load(pg_engine, pg_conn):
    payload = (
        b"Guia;Fecha;Estado;Valor\n"
        b"E-7001;01/07/2026;ENTREGADO;50.000\n"
        b"E-7002;01/12/2027;ENTREGADO;60.000\n"     # future date: rejected
        b"E-7003;01/07/2026;ENTREGADO;70.000\n"
    )

    report = _ingest(pg_engine, payload, "mixto.csv")
    pg_conn.commit()

    assert report.rows_inserted == 2
    assert report.rows_failed == 1

    stored = _rows(pg_conn, "SELECT tracking_number FROM core.shipment ORDER BY tracking_number")
    assert [s["tracking_number"] for s in stored] == ["E-7001", "E-7003"]


# =============================================================================
# Tenant isolation through the mart views
# =============================================================================


def test_mart_views_are_empty_without_a_tenant_guc(pg_engine, pg_conn, guias_dia1):
    _ingest(pg_engine, guias_dia1, "dia1.csv")
    pg_conn.commit()

    rows = _rows(pg_conn, "SELECT count(*) AS n FROM mart.v_daily_contribution")
    assert rows[0]["n"] == 0, "mart views must fail closed when norte.tenant_id is unset"


def test_mart_views_return_only_the_current_tenant(pg_dsn, guias_dia1, pii_salt):
    from pipeline.store_pg import PostgresStore, set_tenant

    conn = _service_connection(pg_dsn)
    try:
        seed_workspace(conn, tenant_id=TENANT_A, connection_id=CONN_A, slug="tenant-a")
        seed_workspace(conn, tenant_id=TENANT_B, connection_id=CONN_B, slug="tenant-b")
        truncate_data(conn)
        seed_workspace(conn, tenant_id=TENANT_A, connection_id=CONN_A, slug="tenant-a")
        seed_workspace(conn, tenant_id=TENANT_B, connection_id=CONN_B, slug="tenant-b")

        engine = IngestEngine(PostgresStore(conn), pii_salt=pii_salt, today=TODAY)
        _ingest(engine, guias_dia1, "a.csv", tenant=TENANT_A, conn_id=CONN_A)
        conn.commit()

        set_tenant(conn, TENANT_A)
        a_rows = _rows(conn, "SELECT sum(shipments) AS n FROM mart.v_daily_contribution")
        assert a_rows[0]["n"] == 10

        set_tenant(conn, TENANT_B)
        b_rows = _rows(conn, "SELECT coalesce(sum(shipments), 0) AS n FROM mart.v_daily_contribution")
        assert b_rows[0]["n"] == 0, "tenant B must not see tenant A's shipments"
    finally:
        conn.close()


def test_kpis_match_the_hand_counted_fixture(pg_dsn, guias_dia1, pii_salt):
    """v_daily_contribution reconciled by hand against tests/fixtures/effi_guias_dia1.csv."""
    from pipeline.store_pg import PostgresStore, set_tenant

    conn = _service_connection(pg_dsn)
    try:
        seed_workspace(conn, tenant_id=TENANT_A, connection_id=CONN_A, slug="tenant-a")
        truncate_data(conn)
        seed_workspace(conn, tenant_id=TENANT_A, connection_id=CONN_A, slug="tenant-a")

        engine = IngestEngine(PostgresStore(conn), pii_salt=pii_salt, today=TODAY)
        _ingest(engine, guias_dia1, "dia1.csv")
        conn.commit()
        set_tenant(conn, TENANT_A)

        totals = _rows(
            conn,
            """
            SELECT sum(shipments) AS shipments, sum(delivered) AS delivered,
                   sum(returned) AS returned, sum(in_transit) AS in_transit,
                   sum(dead) AS dead, sum(revenue) AS revenue
            FROM mart.v_daily_contribution
            """,
        )[0]

        # Counted by hand from the fixture:
        #   delivered E-1001,1003,1005,1007,1010 = 5
        #   returned  E-1002,1009               = 2
        #   dead      E-1008 (cancelled)        = 1
        #   open      E-1004,1006               = 2
        #   revenue = declared value of delivered = 89.900+149.900+179.800+149.900+89.900
        assert totals["shipments"] == 10
        assert totals["delivered"] == 5
        assert totals["returned"] == 2
        assert totals["dead"] == 1
        assert totals["in_transit"] == 2
        assert Decimal(totals["revenue"]) == Decimal("659400")

        carriers = {
            r["carrier_name"]: r
            for r in _rows(
                conn,
                "SELECT carrier_name, shipments, delivered, delivery_rate_pct "
                "FROM mart.v_carrier_effectiveness",
            )
        }
        # Interrapidisimo: E-1001 (ok), E-1002 (returned), E-1005 (ok), E-1010 (ok)
        assert carriers["Interrapidisimo"]["shipments"] == 4
        assert carriers["Interrapidisimo"]["delivered"] == 3
        assert Decimal(carriers["Interrapidisimo"]["delivery_rate_pct"]) == Decimal("75.00")
    finally:
        conn.close()


def test_layout_blocks_cpa_without_an_ads_connection(pg_dsn, guias_dia1, pii_salt):
    from pipeline.store_pg import PostgresStore, set_tenant

    conn = _service_connection(pg_dsn)
    try:
        seed_workspace(conn, tenant_id=TENANT_A, connection_id=CONN_A, slug="tenant-a")
        truncate_data(conn)
        seed_workspace(conn, tenant_id=TENANT_A, connection_id=CONN_A, slug="tenant-a")

        engine = IngestEngine(PostgresStore(conn), pii_salt=pii_salt, today=TODAY)
        _ingest(engine, guias_dia1, "dia1.csv")
        conn.commit()
        set_tenant(conn, TENANT_A)

        widgets = {
            r["widget_code"]: r
            for r in _rows(
                conn,
                "SELECT widget_code, state, missing_required, state_message "
                "FROM mart.v_country_dashboard_layout",
            )
        }
        assert widgets["cpa_roas"]["state"] == "blocked"
        assert widgets["cpa_roas"]["missing_required"] == ["ads"]
        assert "pauta" in widgets["cpa_roas"]["state_message"].lower()

        assert widgets["carrier_table"]["state"] == "available"
        assert widgets["cs_confirmation"]["state"] == "blocked"
    finally:
        conn.close()

def test_movement_sign_mirror_matches_the_table(pg_conn):
    """The Python mirror of movement_type.sign cannot drift from the table.

    The ingestion loop reads it to decide whether a row's own sign contradicts
    its type. A stale entry there would silently stop reporting reversals - the
    exact failure the check exists to prevent.
    """
    from pipeline.mapping import MOVEMENT_TYPE_SIGNS

    with pg_conn.cursor() as cur:
        cur.execute("SELECT code, sign FROM core.movement_type")
        in_db = dict(cur.fetchall())

    assert MOVEMENT_TYPE_SIGNS == in_db, (
        "pipeline/mapping.py:MOVEMENT_TYPE_SIGNS y core.movement_type.sign "
        "dejaron de coincidir"
    )
