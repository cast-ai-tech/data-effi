"""The tier-3 sync loop: what each failure does to the connection.

The worker only ever selects `status = 'active'` connections, so writing
`status = 'error'` is a decision to stop syncing until a human acts. These tests
pin down WHICH failures earn that, and that one merchant's failure never stops
the others. No database: a fake connection records the SQL it is sent.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from connectors.effi.session_fetcher import (
    FetchError,
    PermissionDeniedError,
    SessionExpiredError,
    TransientFetchError,
)
from pipeline.models import BatchKind
from worker import jobs


class _Cursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.sql.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._conn.rows


class _Conn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.sql: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, **_kwargs):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def statements(self, needle: str) -> list[str]:
        return [s for s, _ in self.sql if needle in s]


def _row(name="Tienda"):
    return {
        "id": uuid4(), "tenant_id": uuid4(), "country_code": "CO",
        "platform_code": "effi", "secret_ref": None,
        "consent_granted_at": datetime.now(UTC), "name": name,
        "credential_status": "ok", "currency_code": "COP",
    }


def _run(conn):
    return jobs.job_sync_tier3(conn, pii_salt="sal", enabled=True)


# -- which failures park the connection --------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        TransientFetchError("Effi respondió HTTP 502"),
        RuntimeError("un archivo raro"),
    ],
)
def test_outages_and_bad_files_do_not_take_the_connection_off_the_schedule(monkeypatch, exc):
    conn = _Conn([_row()])

    def boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(jobs, "_sync_one_tier3", boom)

    result = _run(conn)

    assert not conn.statements("status = 'error'"), (
        "Una caída de Effi dejó la conexión en 'error' y el worker ya no la vuelve a tomar."
    )
    assert conn.statements("SET last_error = %s")
    assert result["connections"][0]["status"] in ("retry_later", "error")


def test_an_expired_session_needs_a_human_and_says_so(monkeypatch):
    conn = _Conn([_row()])
    monkeypatch.setattr(
        jobs, "_sync_one_tier3",
        lambda *a, **k: (_ for _ in ()).throw(SessionExpiredError("vencida")),
    )

    result = _run(conn)

    assert conn.statements("status = 'error'")
    expired = conn.statements("SET credential_status = %s")
    assert expired and "NOT IN ('invalid', 'locked')" in expired[0], (
        "Marcar 'expired' no puede pisar 'invalid'/'locked', que son los que frenan el login."
    )
    assert result["connections"][0]["status"] == "needs_reauthorization"


def test_a_missing_permission_is_not_reported_as_an_expired_session(monkeypatch):
    conn = _Conn([_row()])
    monkeypatch.setattr(
        jobs, "_sync_one_tier3",
        lambda *a, **k: (_ for _ in ()).throw(PermissionDeniedError("falta permiso")),
    )

    result = _run(conn)

    params = [p for s, p in conn.sql if "SET credential_status = %s" in s]
    assert params and params[0][0] == "insufficient_permissions"
    assert result["connections"][0]["status"] == "needs_permissions"


def test_one_merchant_failing_does_not_stop_the_next_one(monkeypatch):
    first, second = _row("A"), _row("B")
    conn = _Conn([first, second])
    synced = []

    def sync(_conn, row, **_k):
        if row is first:
            raise ValueError("columna inesperada")
        synced.append(row["name"])
        return []

    monkeypatch.setattr(jobs, "_sync_one_tier3", sync)

    result = _run(conn)

    assert synced == ["B"]
    assert [c["status"] for c in result["connections"]] == ["error", "ok"]


def test_a_clean_run_clears_the_previous_error(monkeypatch):
    conn = _Conn([_row()])
    monkeypatch.setattr(jobs, "_sync_one_tier3", lambda *a, **k: [])

    _run(conn)

    assert conn.statements("last_sync_at = now(), last_error = NULL")


# -- a reused session that dies early gets exactly one fresh login -----------


class _Fetcher:
    def __init__(self, fail_first: bool):
        self.fail_first = fail_first
        self.calls = 0

    def fetch_report(self, kind, *, date_from, date_to):
        self.calls += 1
        if self.fail_first:
            self.fail_first = False
            raise SessionExpiredError("rebotó al login")
        return SimpleNamespace(payload=b"x", filename=f"{kind.value}.csv")


@pytest.fixture
def no_ingest(monkeypatch):
    report = SimpleNamespace(already_loaded=False, rows_inserted=1, rows_updated=0)
    monkeypatch.setattr(jobs, "PostgresStore", lambda conn: None)
    monkeypatch.setattr(
        jobs, "IngestEngine",
        lambda store, pii_salt: SimpleNamespace(ingest=lambda **k: report),
    )
    cleared = []
    from api import credentials

    monkeypatch.setattr(credentials, "clear_credential", lambda conn, **k: cleared.append(k))
    return cleared


def _sync(conn, row):
    return jobs._sync_one_tier3(
        conn, row, pii_salt="sal", date_from=date(2026, 9, 1), date_to=date(2026, 9, 14)
    )


def test_a_reused_session_rejected_early_is_renewed_once(monkeypatch, no_ingest):
    stale, fresh = _Fetcher(fail_first=True), _Fetcher(fail_first=False)
    built = iter([(stale, True), (fresh, False)])
    monkeypatch.setattr(jobs, "_build_tier3_fetcher", lambda conn, row: next(built))

    ingested = _sync(_Conn(), _row())

    assert [i["kind"] for i in ingested] == [BatchKind.SHIPMENTS.value, BatchKind.MOVEMENTS.value]
    assert len(no_ingest) == 1, "la sesión rechazada debía olvidarse antes de volver a entrar"


def test_a_session_from_a_fresh_login_is_not_retried(monkeypatch, no_ingest):
    """Rejected right after logging in is a real problem, not a stale session."""
    monkeypatch.setattr(
        jobs, "_build_tier3_fetcher", lambda conn, row: (_Fetcher(fail_first=True), False)
    )

    with pytest.raises(SessionExpiredError):
        _sync(_Conn(), _row())

    assert no_ingest == []


def test_the_renewal_happens_at_most_once(monkeypatch, no_ingest):
    built = iter([(_Fetcher(fail_first=True), True), (_Fetcher(fail_first=True), False)])
    monkeypatch.setattr(jobs, "_build_tier3_fetcher", lambda conn, row: next(built))

    with pytest.raises(SessionExpiredError):
        _sync(_Conn(), _row())


def test_login_not_possible_yet_is_transient_not_a_broken_connection(monkeypatch):
    """LOGIN_CONTRACT_VERIFIED is False (and Effi's form has a captcha).

    That used to raise a plain FetchError, so every vault connection with the
    worker switched on landed in `error` on its first run and never left it.
    """
    from api import credentials

    monkeypatch.setattr(
        credentials, "load_session",
        lambda conn, **k: SimpleNamespace(token=None, expires_at=None),
    )

    class _Cred:
        def __enter__(self):
            from pipeline.vault import Credential

            return Credential(username="u@t.co", password="p")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(credentials, "use_credential", lambda conn, **k: _Cred())

    with pytest.raises(TransientFetchError):
        jobs._build_tier3_fetcher(_Conn(), _row())

    assert issubclass(TransientFetchError, FetchError)
