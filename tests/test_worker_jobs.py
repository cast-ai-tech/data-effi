"""Regresiones de la auditoría del worker (rama audit/pipeline-worker).

Sin base de datos: una conexión falsa que se comporta como PostgreSQL en lo
único que importa aquí - tras un error, la transacción queda abortada y todo
lo que se ejecute falla hasta el rollback.
"""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from worker import jobs


class FakeConn:
    def __init__(self, rows=None):
        self.aborted = False
        self.executed: list[str] = []
        self.rows = rows or []
        self.rollbacks = 0

    def cursor(self, **_kwargs):
        return FakeCursor(self)

    def commit(self):
        if self.aborted:
            raise psycopg.errors.InFailedSqlTransaction("aborted")

    def rollback(self):
        self.aborted = False
        self.rollbacks += 1


class FakeCursor:
    def __init__(self, conn: FakeConn):
        self.conn = conn
        self._last = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        if self.conn.aborted:
            raise psycopg.errors.InFailedSqlTransaction(
                "current transaction is aborted"
            )
        self._last = query
        self.conn.executed.append(query)

    def fetchone(self):
        return (True,)

    def fetchall(self):
        return self.conn.rows


@pytest.fixture(autouse=True)
def _no_broadcast(monkeypatch):
    monkeypatch.setattr(jobs, "broadcast", lambda *a, **k: 0)


def test_a_job_that_breaks_the_transaction_is_still_recorded_as_failed():
    conn = FakeConn()

    def body(c):
        c.aborted = True
        raise psycopg.errors.UndefinedTable("relation does not exist")

    result = jobs.run_job(conn, "relink_orphans", body)

    assert result["status"] == "failed"
    assert "UndefinedTable" in result["error"]
    assert any("INSERT INTO raw.job_run" in q for q in conn.executed)


def test_one_broken_sheet_does_not_stop_the_others(monkeypatch):
    first, second = uuid4(), uuid4()
    conn = FakeConn(rows=[
        {"id": first, "name": "A", "tenant_id": uuid4()},
        {"id": second, "name": "B", "tenant_id": uuid4()},
    ])

    def fake_sync(c, row, *, pii_salt):
        if row["id"] == first:
            c.aborted = True
            raise psycopg.errors.NumericValueOutOfRange("overflow")
        return {"inserted": 3}

    monkeypatch.setattr(jobs, "_sync_one_sheet", fake_sync)

    result = jobs.job_sync_sheets(conn, pii_salt="s")

    statuses = [entry["status"] for entry in result["connections"]]
    assert statuses == ["error", "ok"]
    # The error is recorded without pulling the connection out of the schedule.
    assert not any("status = 'error'" in q for q in conn.executed)
    assert any("SET last_error" in q for q in conn.executed)
