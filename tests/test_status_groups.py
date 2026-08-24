"""The five screen groups, and Dropi's vocabulary (migration 040).

Two copies exist on purpose - `pipeline/mapping.py` for the engine that must
work without a database, `core.status_canon.display_group` for the views - and
this file is what keeps them from drifting apart.
"""

from __future__ import annotations

import pytest

from pipeline.mapping import (
    DISPLAY_GROUP_LABELS,
    DISPLAY_GROUPS,
    STATUS_ALIASES,
    STATUS_CANON,
    display_group,
    resolve_status,
)

GROUPS = {"entregada", "devolucion", "en_camino", "novedad", "muerta"}


def test_every_canonical_status_has_a_screen_group():
    assert set(DISPLAY_GROUPS) == set(STATUS_CANON)
    assert set(DISPLAY_GROUPS.values()) <= GROUPS
    assert set(DISPLAY_GROUP_LABELS) == GROUPS


def test_the_groups_respect_the_flags_of_the_canonical_ladder():
    """A group is a coarser reading of the flags, never a contradiction of them."""
    for code, status in STATUS_CANON.items():
        group = display_group(code)
        if status.is_delivered:
            assert group == "entregada", code
        elif status.is_returned:
            assert group == "devolucion", code
        elif status.is_terminal:
            assert group == "muerta", code
        else:
            assert group in {"en_camino", "novedad"}, code


def test_office_and_issue_count_as_novedad():
    """Both are a parcel that stopped moving and needs a call. The report has
    one column for that, and "en oficina" must not disappear into "en camino"."""
    assert display_group("in_office") == "novedad"
    assert display_group("delivery_issue") == "novedad"


def test_an_unknown_code_reads_as_still_moving():
    assert display_group("algo-raro") == "en_camino"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Incidencia en ruta", "delivery_issue"),
        ("INCIDENCIA EN RUTA", "delivery_issue"),
        ("Incidencia", "delivery_issue"),
        ("Entregado", "delivered"),
        ("En ruta", "in_transit"),
        ("Devolución", "returning"),
        ("Devolución a origen", "returning"),
        ("Entregada a destino", "delivered"),
    ],
)
def test_dropi_and_effi_words_land_on_the_same_ladder(raw, expected):
    code, recognized = resolve_status(raw)
    assert recognized, raw
    assert code == expected


def test_the_two_platforms_agree_on_the_screen_group():
    """"Entregada a destino" (Effi) and "Entregado" (Dropi) are one column."""
    effi, _ = resolve_status("Entregada a destino")
    dropi, _ = resolve_status("Entregado")
    assert display_group(effi) == display_group(dropi) == "entregada"

    effi_issue, _ = resolve_status("Novedad")
    dropi_issue, _ = resolve_status("Incidencia en ruta")
    assert display_group(effi_issue) == display_group(dropi_issue) == "novedad"


def test_every_alias_points_at_a_canonical_code():
    assert set(STATUS_ALIASES.values()) <= set(STATUS_CANON)


# -----------------------------------------------------------------------------
# Against the database, when there is one
# -----------------------------------------------------------------------------


@pytest.mark.postgres
def test_the_sql_seed_mirrors_the_python_copy():
    psycopg = pytest.importorskip("psycopg")
    from tests.pg_helpers import admin_test_dsn, recreate_test_database, resolve_test_dsn

    if not resolve_test_dsn():
        pytest.skip("No DATABASE_URL configured")
    try:
        recreate_test_database()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")

    with psycopg.connect(admin_test_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT code, display_group FROM core.status_canon")
        assert dict(cur.fetchall()) == DISPLAY_GROUPS

        cur.execute(
            "SELECT status_code FROM core.status_alias "
            "WHERE platform_code = 'dropi' AND alias_norm = 'incidencia en ruta'"
        )
        row = cur.fetchone()
        assert row is not None, "040 no registró 'incidencia en ruta' para Dropi"
        assert row[0] == "delivery_issue"
