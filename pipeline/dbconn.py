"""Una sola forma de abrir una conexión, para que el pooler no vuelva a morder.

WHY THIS EXISTS
psycopg promotes a query to a named prepared statement after five executions.
Against plain PostgreSQL that is free speed. Behind a transaction-mode pooler -
Supabase's Supavisor on port 6543, PgBouncer generally - it is a crash: the
pooler hands each transaction whichever backend is free, so the sixth execution
meets a backend that already holds `_pg3_0` from another client and raises

    DuplicatePreparedStatement: prepared statement "_pg3_0" already exists

`api/db.py` learned this the hard way and set `prepare_threshold=None` on its
pools. Everything ELSE that opens a connection - the worker and every script -
kept calling `psycopg.connect` directly and kept the bug, which only shows up
after a handful of repeated queries and therefore never in a quick test.

The failure is slow and looks unrelated to the pooler, so the defence has to be
structural: import `connect` from here instead of calling psycopg directly.
"""

from __future__ import annotations

from typing import Any

import psycopg


def connect(dsn: str, **kwargs: Any) -> psycopg.Connection:
    """`psycopg.connect` with prepared statements off, which is never wrong.

    Turning them off costs a plan re-parse per query - microseconds - and buys
    immunity from the pooler. Any caller that genuinely wants them can still pass
    `prepare_threshold` explicitly and override this.
    """
    kwargs.setdefault("prepare_threshold", None)
    return psycopg.connect(dsn, **kwargs)
