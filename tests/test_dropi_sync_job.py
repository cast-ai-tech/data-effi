"""The `sync_dropi` job, with the database and Dropi both faked.

What matters here is the behaviour around the fetch, not SQL: which failures
stop a connection (a rejected token), which only get written down (Dropi being
down), that one account failing never stops the next, and that the cursor
moves only after a window was actually read.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import ClassVar
from uuid import UUID

import pytest

from connectors.dropi.client import DropiAuthError, DropiTransientError, FetchSummary
from pipeline.ingest import MemoryStore
from pipeline.vault import Credential
from worker import jobs

TENANT = UUID("11111111-1111-1111-1111-111111111111")
CONN_A = UUID("55555555-5555-5555-5555-555555555555")
CONN_B = UUID("66666666-6666-6666-6666-666666666666")
TODAY = date(2026, 9, 28)

ORDER = {
    "id": 77,
    "status": "EN RUTA",
    "shipping_guide": "G-77",
    "total_order": 50000,
    "created_at": "2026-09-20 10:00:00",
}


def _row(connection_id, name, synced_through=None):
    return {
        "id": connection_id,
        "tenant_id": TENANT,
        "country_code": "CO",
        "platform_code": "dropi",
        "name": name,
        "currency_code": "COP",
        "synced_through": synced_through,
    }


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.conn.connections

    def fetchone(self):
        return None


class FakeConn:
    def __init__(self, connections):
        self.connections = connections
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, **_kwargs):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def sql_matching(self, fragment):
        return [(sql, params) for sql, params in self.statements if fragment in sql]


class FakeClient:
    """Stands in for DropiClient; behaviour chosen per token."""

    calls: ClassVar[list[tuple[str, date, date]]] = []

    def __init__(self, *, token, country_code):
        self.token = token

    def fetch_orders(self, *, date_from, date_to):
        FakeClient.calls.append((self.token, date_from, date_to))
        if self.token == "rejected":
            raise DropiAuthError("Dropi rechazó el token de integración (HTTP 401).")
        if self.token == "down":
            raise DropiTransientError("Dropi respondió HTTP 503 después de 4 intentos.")
        orders = [ORDER] if date_from <= date(2026, 9, 20) <= date_to else []
        return FetchSummary(orders=list(orders), pages=1)


@pytest.fixture
def harness(monkeypatch):
    tokens: dict[UUID, str] = {}
    events: dict[str, list] = {"ok": [], "failed": [], "findings": []}
    store = MemoryStore()

    @contextmanager
    def fake_use_credential(conn, *, connection_id, tenant_id):
        if connection_id not in tokens:
            raise LookupError("Esta conexión no tiene una cuenta conectada.")
        yield Credential(username="token de integración", password=tokens[connection_id])

    from ai import alerts
    from api import credentials

    monkeypatch.setattr(credentials, "use_credential", fake_use_credential)
    monkeypatch.setattr(
        credentials, "record_login_ok",
        lambda conn, **kw: events["ok"].append(kw["connection_id"]),
    )
    monkeypatch.setattr(
        credentials, "record_login_failure",
        lambda conn, **kw: events["failed"].append((kw["connection_id"], kw["credential_status"])),
    )
    monkeypatch.setattr(
        alerts, "persist_findings",
        lambda conn, tenant, country, items, **kw: events["findings"].extend(items),
    )
    monkeypatch.setattr(jobs, "PostgresStore", lambda conn: store)
    FakeClient.calls = []
    return tokens, events, store


def _run(conn):
    return jobs.job_sync_dropi(
        conn, pii_salt="test-salt", today=TODAY, client_factory=FakeClient
    )


def test_a_connected_account_is_read_ingested_and_its_cursor_moves(harness):
    tokens, events, store = harness
    tokens[CONN_A] = "good"
    conn = FakeConn([_row(CONN_A, "Dropi CO")])

    result = _run(conn)

    entry = result["connections"][0]
    assert entry["status"] == "ok"
    assert entry["orders"] == 1
    assert (CONN_A, "G-77") in store.shipments
    assert events["ok"] == [CONN_A]

    cursor_writes = conn.sql_matching("INSERT INTO core.connection_sync_state")
    assert len(cursor_writes) == 1
    params = cursor_writes[0][1]
    assert params[2] == TODAY            # synced_through
    assert conn.sql_matching("SET last_sync_at = now()")


def test_the_first_run_backfills_in_slices(harness):
    tokens, _, _ = harness
    tokens[CONN_A] = "good"
    _run(FakeConn([_row(CONN_A, "Dropi CO")]))

    windows = [(f, t) for _, f, t in FakeClient.calls]
    assert windows[-1][1] == TODAY
    assert len(windows) > 1
    assert (windows[-1][1] - windows[0][0]).days == 89


def test_a_rejected_token_is_marked_invalid_and_announced(harness):
    tokens, events, _ = harness
    tokens[CONN_A] = "rejected"
    conn = FakeConn([_row(CONN_A, "Dropi CO")])

    entry = _run(conn)["connections"][0]

    assert entry["status"] == "needs_reauthorization"
    assert events["failed"] == [(CONN_A, "invalid")]
    assert events["findings"][0]["severity"] == "critical"
    assert "token" in events["findings"][0]["title"]
    # The cursor does not move on a failed run.
    assert not conn.sql_matching("INSERT INTO core.connection_sync_state")


def test_dropi_being_down_is_written_down_without_disabling_the_connection(harness):
    tokens, events, _ = harness
    tokens[CONN_A] = "down"
    conn = FakeConn([_row(CONN_A, "Dropi CO")])

    entry = _run(conn)["connections"][0]

    assert entry["status"] == "error"
    assert conn.sql_matching("SET last_error = %s WHERE id = %s")
    # Never `status = 'error'`: the next run is what fixes a transient failure.
    assert not conn.sql_matching("SET status = 'error'")
    assert events["failed"] == []


def test_a_connection_without_a_token_says_so(harness):
    conn = FakeConn([_row(CONN_A, "Dropi CO")])
    entry = _run(conn)["connections"][0]
    assert entry["status"] == "error"
    assert "no tiene una cuenta conectada" in entry["error"]


def test_one_account_failing_never_stops_the_next(harness):
    tokens, events, store = harness
    tokens[CONN_A] = "rejected"
    tokens[CONN_B] = "good"
    conn = FakeConn([_row(CONN_A, "Dropi A"), _row(CONN_B, "Dropi B")])

    result = _run(conn)

    statuses = {e["connection_id"]: e["status"] for e in result["connections"]}
    assert statuses == {CONN_A: "needs_reauthorization", CONN_B: "ok"}
    assert (CONN_B, "G-77") in store.shipments


def test_the_selection_only_takes_active_api_connections_with_a_usable_token(harness):
    conn = FakeConn([])
    _run(conn)
    select = conn.statements[0][0]
    assert "c.source_mode = 'api'" in select
    assert "c.status = 'active'" in select
    assert "c.credential_status <> 'invalid'" in select
    assert conn.statements[0][1] == ("dropi",)


def test_the_job_is_reachable_by_name():
    from api.routers.worker import ALLOWED_JOBS

    assert "sync_dropi" in jobs.JOB_NAMES
    assert "sync_dropi" in ALLOWED_JOBS
