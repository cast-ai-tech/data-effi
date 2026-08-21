"""Shared test fixtures.

The ingestion tests are deterministic on purpose: a fixed `today`, fixed tenant
and connection UUIDs, and fixture files whose numbers can be added up by hand.
When a KPI test fails you should be able to open the CSV and see why.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Fixed identities so assertions can reference them directly.
TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
CONNECTION_ID = UUID("22222222-2222-2222-2222-222222222222")
CONNECTION_ID_ALT = UUID("33333333-3333-3333-3333-333333333333")

# Fixture data lives in July 2026; every check runs against this date.
TODAY = date(2026, 8, 1)

COUNTRY = "CO"
CURRENCY = "COP"
PLATFORM = "effi"

# Test-only salt. Never a real one - real salts live in the environment.
TEST_PII_SALT = "test-salt-not-a-real-secret"


@pytest.fixture(scope="session")
def pii_salt() -> str:
    return TEST_PII_SALT


@pytest.fixture
def guias_dia1() -> bytes:
    return (FIXTURES / "effi_guias_dia1.csv").read_bytes()


@pytest.fixture
def guias_dia2() -> bytes:
    return (FIXTURES / "effi_guias_dia2.csv").read_bytes()


@pytest.fixture
def movimientos() -> bytes:
    return (FIXTURES / "effi_movimientos.csv").read_bytes()


@pytest.fixture
def memory_store():
    from pipeline.ingest import MemoryStore

    return MemoryStore()


@pytest.fixture
def engine(memory_store, pii_salt):
    from pipeline.ingest import IngestEngine

    return IngestEngine(memory_store, pii_salt=pii_salt, today=TODAY)


@pytest.fixture(scope="session")
def database_url() -> str | None:
    """DSN for the Postgres-backed tests. Absent means those tests skip."""
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session", autouse=True)
def _test_database_lifecycle():
    """Sweep dead databases on the way in, drop ours on the way out.

    Each pytest process owns a database named after its pid (see
    `pg_helpers.TEST_DB_NAME`), so concurrent runs stop dropping each other's.
    This fixture is what keeps that from leaking one database per run.
    """
    from tests.pg_helpers import drop_test_database, sweep_abandoned_test_databases

    sweep_abandoned_test_databases()
    yield
    drop_test_database()
