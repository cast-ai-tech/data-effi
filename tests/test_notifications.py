"""Notifications, the event feed, the daily digest, and the decisions.

Two groups. The first needs no database and pins the contracts that must hold
before a row is ever written: a fingerprint is stable, a long-poll returns
within its `wait`, the capability filter is in the SQL. The second runs
against PostgreSQL and checks what the operator was promised: the same
finding is not repeated inside its window, the digest is written once per
day however many times the cron fires, events and notifications never cross
tenants, and the NL->SQL role cannot read any of it.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn, seed_workspace

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

TENANT = UUID("eeeeeeee-1000-4000-e000-00000000000e")
OTHER_TENANT = UUID("ffffffff-2000-4000-f000-00000000000f")
CONNECTION = UUID("eeeeeeee-3000-4000-e000-00000000000e")
OTHER_CONNECTION = UUID("ffffffff-4000-4000-f000-00000000000f")
USER = UUID("eeeeeeee-5000-4000-e000-00000000000e")
OTHER_USER = UUID("eeeeeeee-6000-4000-e000-00000000000e")

# Settings stand-in for the digest job: a zero budget makes the brief degrade
# deterministically without a key, a network, or a Settings object.
NO_AI = SimpleNamespace(ai_enabled=False, ai_daily_token_budget=0)


def _finding(code: str = "product_below_breakeven", title: str = "Faja X", **extra) -> dict:
    return {
        "code": code,
        "severity": "critical",
        "country_code": "CO",
        "title": title,
        "finding": "Entrega 40% y necesita 55%.",
        "action": "Exige confirmación telefónica.",
        "impact_amount": 1_200_000.0,
        "impact_currency": "COP",
        "deep_link": "/co?tab=efectividad",
        "detected_at": datetime.now(UTC),
        **extra,
    }


# =============================================================================
# No database
# =============================================================================


def test_fingerprint_is_stable_and_country_insensitive_to_case():
    from ai.alerts import fingerprint

    first = fingerprint("product_below_breakeven", "co", "Faja X")
    assert first == fingerprint("product_below_breakeven", "CO", "Faja X ")
    assert len(first) == 64
    assert first != fingerprint("product_below_breakeven", "EC", "Faja X")
    assert first != fingerprint("product_below_breakeven", "CO", "Faja Y")


def test_a_digest_fingerprint_is_one_per_country_per_day():
    from ai.alerts import fingerprint

    assert fingerprint("digest", "CO", "2026-08-23") == fingerprint("digest", "CO", "2026-08-23")
    assert fingerprint("digest", "CO", "2026-08-23") != fingerprint("digest", "CO", "2026-08-24")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Tu efectividad de entrega mediana en CO es 62.3% (últimos 90 días, 1,240 guías).", 62.3),
        ("Tu ciclo de caja normal en CO es 12 días desde el despacho", 12.0),
        ("Tu flete típico en CO es 5,000 COP por kilo, y pesa 8.1% del recaudo", 5000.0),
        ("60", 60.0),
        ("14,5", 14.5),
        ("sin número", None),
        ("", None),
        (None, None),
    ],
)
def test_threshold_sentences_yield_their_number(text, expected):
    from ai.decisions import threshold_number

    assert threshold_number(text) == expected


def _user(role: str, countries: tuple[str, ...] | None = None):
    from api.deps import CurrentUser

    return CurrentUser(
        id=USER, tenant_id=TENANT, email="t@example.com", role=role, countries=countries
    )


def test_an_uploader_only_hears_about_loads():
    from api.routers.events import _query

    sql, params = _query(_user("uploader"), TENANT, 0, [])
    assert "type LIKE 'upload_job.%%'" in sql
    assert params == [TENANT, 0]

    sql, params = _query(_user("owner"), TENANT, 0, ["batch.finished"])
    assert "upload_job" not in sql
    assert "type = ANY(%s)" in sql
    assert params == [TENANT, 0, ["batch.finished"]]


def test_a_limited_membership_only_hears_its_countries():
    from api.routers.events import _query

    sql, params = _query(_user("viewer", countries=("GT",)), TENANT, 7, [])
    assert "country_code IS NULL OR upper(country_code) = ANY(%s)" in sql
    assert params[-1] == ["GT"]


def test_unknown_event_types_in_the_filter_are_dropped_not_queried():
    from api.routers.events import _parse_types

    assert _parse_types("batch.finished, nope, fx.refreshed") == ["batch.finished", "fx.refreshed"]
    assert _parse_types(None) == []


def test_emit_refuses_a_type_nobody_subscribes_to():
    from api.events import emit

    with pytest.raises(ValueError):
        emit(None, TENANT, "typo.event")


@pytest.fixture
def events_client(monkeypatch):
    """The events router alone, with the pool replaced by a stub.

    Same guard as `api/main.py` mounts, and the same `current_user` dependency
    the real app resolves - overridden here because there is no token and no
    database, not because the guard is skipped.
    """
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from api.deps import current_user, require_any_cap
    from api.errors import register_error_handlers
    from api.routers import events

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(events.router, dependencies=[Depends(require_any_cap("read", "ingest"))])
    app.dependency_overrides[current_user] = lambda: _user("owner")

    monkeypatch.setattr(events, "_current_cursor", lambda tenant_id: 41)
    monkeypatch.setattr(events, "_fetch", lambda user, tenant_id, since, wanted: [])
    return TestClient(app), events


def test_without_since_only_the_cursor_comes_back(events_client):
    client, _ = events_client
    response = client.get("/events")
    assert response.status_code == 200
    assert response.json() == {"cursor": 41, "events": []}


def test_the_long_poll_returns_within_its_wait(events_client):
    client, _ = events_client
    started = time.monotonic()
    response = client.get("/events?since=41&wait=2")
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert response.json() == {"cursor": 41, "events": []}
    assert 1.5 <= elapsed <= 4.0, f"waited {elapsed:.2f}s for wait=2"


def test_a_wait_of_zero_answers_at_once(events_client):
    client, _ = events_client
    started = time.monotonic()
    response = client.get("/events?since=41&wait=0")
    assert response.status_code == 200
    assert time.monotonic() - started < 1.0


def test_the_wait_ceiling_is_enforced(events_client):
    client, _ = events_client
    assert client.get("/events?since=41&wait=9").status_code == 422
    assert client.get("/events?since=41&wait=-1").status_code == 422


def test_rows_end_the_wait_early_and_move_the_cursor(events_client, monkeypatch):
    client, events = events_client
    rows = [
        {
            "id": 42, "type": "batch.finished", "country_code": "CO",
            "payload": {"batch_id": "b"}, "created_at": datetime.now(UTC),
        },
        {
            "id": 43, "type": "fx.refreshed", "country_code": None,
            "payload": {}, "created_at": datetime.now(UTC),
        },
    ]
    monkeypatch.setattr(events, "_fetch", lambda user, tenant_id, since, wanted: rows)

    started = time.monotonic()
    response = client.get("/events?since=41&wait=6")
    assert time.monotonic() - started < 1.0
    body = response.json()
    assert body["cursor"] == 43
    assert [event["type"] for event in body["events"]] == ["batch.finished", "fx.refreshed"]


def test_an_uploader_may_read_the_feed_but_a_role_with_neither_cap_may_not(monkeypatch):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from api.deps import current_user, require_any_cap
    from api.errors import register_error_handlers
    from api.routers import events

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(events.router, dependencies=[Depends(require_any_cap("read", "ingest"))])
    monkeypatch.setattr(events, "_current_cursor", lambda tenant_id: 0)

    app.dependency_overrides[current_user] = lambda: _user("uploader")
    assert TestClient(app).get("/events").status_code == 200

    app.dependency_overrides[current_user] = lambda: _user("nobody")
    assert TestClient(app).get("/events").status_code == 403


# =============================================================================
# PostgreSQL
# =============================================================================


@pytest.fixture(scope="module")
def notifications_dsn() -> str:
    """The migrated test database, reused when it is already there (see test_ai_memory)."""
    dsn = resolve_test_dsn()
    if not dsn:
        pytest.skip("No DATABASE_URL configured")

    try:
        with psycopg.connect(dsn, autocommit=True) as probe, probe.cursor() as cur:
            cur.execute("SELECT to_regclass('raw.notification') IS NOT NULL")
            if cur.fetchone()[0]:
                return dsn
    except psycopg.Error:
        pass

    try:
        return recreate_test_database()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")


@pytest.fixture
def conn(notifications_dsn):
    """A connection scoped to TENANT, the way the API scopes one per request."""
    connection = psycopg.connect(notifications_dsn, autocommit=False)
    seed_workspace(
        connection, tenant_id=TENANT, connection_id=CONNECTION,
        platform_code="dropi", slug="notif",
    )
    seed_workspace(
        connection, tenant_id=OTHER_TENANT, connection_id=OTHER_CONNECTION,
        platform_code="dropi", slug="notif-other",
    )
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        for user_id, email in ((USER, "notif@example.com"), (OTHER_USER, "notif2@example.com")):
            cur.execute(
                """
                INSERT INTO core.app_user (id, tenant_id, email, password_hash, full_name, role)
                VALUES (%s, %s, %s, 'x', 'Prueba', 'owner')
                ON CONFLICT (id) DO NOTHING
                """,
                (user_id, TENANT, email),
            )
    connection.commit()
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'off', false)")
        cur.execute("SELECT set_config('norte.tenant_id', %s, false)", (str(TENANT),))
    connection.commit()

    yield connection

    connection.rollback()
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'on', true)")
        for tenant in (TENANT, OTHER_TENANT):
            cur.execute("DELETE FROM raw.notification WHERE tenant_id = %s", (tenant,))
            cur.execute("DELETE FROM raw.event WHERE tenant_id = %s", (tenant,))
            cur.execute("DELETE FROM raw.ai_memory WHERE tenant_id = %s", (tenant,))
            cur.execute("DELETE FROM core.shipment WHERE tenant_id = %s", (tenant,))
    connection.commit()
    connection.close()


def _scope_to(connection, tenant_id: UUID) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('norte.tenant_id', %s, false)", (str(tenant_id),))
    connection.commit()


def _count(connection, sql: str, *params) -> int:
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()[0])


@pytest.mark.postgres
def test_the_same_finding_is_not_repeated_inside_the_window(conn):
    from ai.alerts import persist_findings

    first = persist_findings(conn, TENANT, "CO", [_finding()])
    assert len(first) == 1

    again = persist_findings(conn, TENANT, "CO", [_finding()])
    assert again == [], "a repeated finding inside three days must not insert"

    other = persist_findings(conn, TENANT, "CO", [_finding(title="Faja Y")])
    assert len(other) == 1

    assert _count(conn, "SELECT count(*) FROM raw.notification WHERE tenant_id = %s", TENANT) == 2


@pytest.mark.postgres
def test_persisting_a_finding_emits_an_event_the_screen_can_hear(conn):
    from ai.alerts import persist_findings

    (notification_id,) = persist_findings(conn, TENANT, "CO", [_finding()])

    with conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM raw.event WHERE tenant_id = %s AND type = 'notification.created'",
            (TENANT,),
        )
        payloads = [row[0] for row in cur.fetchall()]
    assert len(payloads) == 1
    assert payloads[0] == {
        "notification_id": notification_id, "severity": "critical", "kind": "urgent",
    }


@pytest.mark.postgres
def test_the_digest_is_written_once_per_country_per_day(conn):
    from ai.alerts import persist_digest

    day = datetime.now(UTC).date()
    first = persist_digest(
        conn, TENANT, "CO", brief=None, recommendations=[_finding()], alerts=[], local_date=day,
    )
    assert first is not None
    second = persist_digest(
        conn, TENANT, "CO", brief="otro texto", recommendations=[], alerts=[], local_date=day,
    )
    assert second is None

    with conn.cursor() as cur:
        cur.execute("SELECT kind, severity, finding FROM raw.notification WHERE id = %s", (first,))
        kind, severity, finding = cur.fetchone()
    assert kind == "digest"
    assert severity == "warning", "a digest is never critical on its own"
    assert "Faja X" in finding, "without a brief the findings are listed"


@pytest.mark.postgres
def test_daily_digest_job_is_idempotent_and_respects_the_local_hour(notifications_dsn, conn):
    from worker.jobs import job_daily_digest

    # The job runs the way the worker does: its own connection, service on.
    service = psycopg.connect(notifications_dsn, autocommit=False)
    try:
        with service.cursor() as cur:
            cur.execute("SELECT set_config('norte.service', 'on', false)")
        service.commit()

        # 15:00 UTC is 10:00 in Bogotá: past seven everywhere the platform runs.
        late = datetime(2026, 8, 23, 15, 0, tzinfo=UTC)
        first = job_daily_digest(service, settings=NO_AI, now=late)
        assert first["errors"] == 0, first
        second = job_daily_digest(service, settings=NO_AI, now=late)
        assert second["errors"] == 0, second
        assert second["count"] == 0, "the second pass must write nothing"
        assert second["already_written"] >= 1
    finally:
        service.close()

    assert _count(
        conn,
        "SELECT count(*) FROM raw.notification WHERE tenant_id = %s AND kind = 'digest'",
        TENANT,
    ) == 1

    # 08:00 UTC is 03:00 in Bogotá: nobody's digest yet.
    service = psycopg.connect(notifications_dsn, autocommit=False)
    try:
        with service.cursor() as cur:
            cur.execute("SELECT set_config('norte.service', 'on', false)")
        service.commit()
        early = job_daily_digest(
            service, settings=NO_AI, now=datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
        )
        assert early["count"] == 0
        assert early["before_local_hour"] >= 1
    finally:
        service.close()


@pytest.mark.postgres
def test_the_event_cursor_returns_only_newer_rows_and_filters_by_capability(conn):
    from api.events import emit
    from api.routers.events import _query

    older = emit(conn, TENANT, "fx.refreshed")
    upload = emit(conn, TENANT, "upload_job.updated", payload={"job_id": "j", "status": "done"})
    batch = emit(conn, TENANT, "batch.finished", country_code="CO", payload={"batch_id": "b"})
    conn.commit()

    def read(user, since):
        sql, params = _query(user, TENANT, since, [])
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [row[0] for row in cur.fetchall()]

    assert read(_user("owner"), older) == [upload, batch]
    assert read(_user("owner"), batch) == []
    assert read(_user("uploader"), 0) == [upload], "an uploader hears about loads only"


@pytest.mark.postgres
def test_events_and_notifications_do_not_cross_tenants(conn):
    from ai.alerts import persist_findings
    from api.events import emit

    persist_findings(conn, TENANT, "CO", [_finding()])
    emit(conn, TENANT, "fx.refreshed")
    conn.commit()

    _scope_to(conn, OTHER_TENANT)
    assert _count(conn, "SELECT count(*) FROM raw.notification") == 0
    assert _count(conn, "SELECT count(*) FROM raw.event") == 0
    _scope_to(conn, TENANT)
    assert _count(conn, "SELECT count(*) FROM raw.notification") == 1
    assert _count(conn, "SELECT count(*) FROM raw.event") == 2


@pytest.mark.postgres
def test_read_state_belongs_to_one_person(conn):
    from ai.alerts import persist_findings

    (notification_id,) = persist_findings(conn, TENANT, "CO", [_finding()])
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.notification_state (notification_id, user_id, tenant_id, read_at)
            VALUES (%s, %s, %s, now())
            """,
            (notification_id, USER, TENANT),
        )
    conn.commit()

    def unread_for(user_id: UUID) -> int:
        return _count(
            conn,
            """
            SELECT count(*) FROM raw.notification n
            LEFT JOIN raw.notification_state s
                   ON s.notification_id = n.id AND s.user_id = %s
            WHERE n.tenant_id = %s AND s.read_at IS NULL
            """,
            user_id, TENANT,
        )

    assert unread_for(USER) == 0
    assert unread_for(OTHER_USER) == 1


@pytest.mark.postgres
@pytest.mark.parametrize(
    "table", ["raw.notification", "raw.notification_state", "raw.event"]
)
def test_the_readonly_role_cannot_read_notifications(conn, table):
    with conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege('norte_readonly', %s, 'SELECT')", (table,))
        assert cur.fetchone()[0] is False, f"norte_readonly can read {table}"


@pytest.mark.postgres
def test_the_readonly_role_can_read_the_carrier_zone_view(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege('norte_readonly', 'mart.v_carrier_by_zone', 'SELECT')"
        )
        assert cur.fetchone()[0] is True


def _seed_products(connection) -> None:
    """Two products with 30 guides each: one delivers 90%, one delivers 20%.

    Costs are set so the break-even sits near 50%: the first is clearly above
    it, the second clearly below and already losing money.
    """
    with connection.cursor() as cur:
        for name, delivered in (("Ganador", 27), ("Perdedor", 6)):
            cur.execute(
                """
                INSERT INTO core.product (tenant_id, name, name_norm, unit_cost)
                VALUES (%s, %s, %s, 20000)
                ON CONFLICT DO NOTHING
                """,
                (TENANT, name, name.lower()),
            )
            cur.execute(
                "SELECT id FROM core.product WHERE tenant_id = %s AND name_norm = %s",
                (TENANT, name.lower()),
            )
            product_id = cur.fetchone()[0]
            for index in range(30):
                is_delivered = index < delivered
                cur.execute(
                    """
                    INSERT INTO core.shipment
                        (tenant_id, connection_id, country_code, product_id, tracking_number,
                         status_code, created_date, delivered_at, currency_code,
                         declared_value, product_cost, freight_cost, quantity)
                    VALUES (%s, %s, 'CO', %s, %s, %s, CURRENT_DATE - 20,
                            CASE WHEN %s THEN now() - interval '10 days' END,
                            'COP', 100000, 20000, 15000, 1)
                    ON CONFLICT (connection_id, tracking_number) DO NOTHING
                    """,
                    (
                        TENANT, CONNECTION, product_id, f"{name[:3].upper()}-{index:04d}",
                        "delivered" if is_delivered else "returned", is_delivered,
                    ),
                )
    connection.commit()


@pytest.mark.postgres
def test_product_verdicts_keep_the_winner_and_cut_the_loser(conn):
    from ai.decisions import build_decisions

    _seed_products(conn)
    result = build_decisions(conn, TENANT, "CO", "products")
    verdicts = {item["label"]: item for item in result["items"]}

    assert verdicts["Ganador"]["verdict"] == "keep", verdicts["Ganador"]
    assert verdicts["Perdedor"]["verdict"] == "cut", verdicts["Perdedor"]
    assert verdicts["Perdedor"]["impact_amount"] and verdicts["Perdedor"]["impact_amount"] > 0
    assert verdicts["Perdedor"]["numbers"]["product_id"]
    # cut sorts before keep so the strip shows the decision that costs money first
    assert result["items"][0]["verdict"] == "cut"
