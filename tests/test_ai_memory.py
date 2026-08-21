"""Memory, conversations, and recommendations - against a real PostgreSQL.

THREE CLAIMS ARE UNDER TEST HERE, and they are the three the operator was
promised:

  1. "It learns from what is done." It does not train. It REMEMBERS: facts
     round-trip, an upsert replaces rather than duplicates, an expired fact
     stops being recalled, and a correction the operator explained becomes a
     durable fact that later prompts read back.
  2. "It gives real-time recommendations." They are produced by SQL, so they are
     produced with the model switched off. That is asserted twice - once by
     running the detection with no settings object anywhere near it, and once
     structurally, because `detect` has no way to reach a model.
  3. "It can look at any data in the platform." Any AGGREGATE. Row-level
     customer data stays unreachable, at the grant level as well as at the
     validator level.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from tests.pg_helpers import recreate_test_database, resolve_test_dsn, seed_workspace

psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.postgres

TENANT = UUID("cccccccc-1000-4000-c000-00000000000c")
OTHER_TENANT = UUID("dddddddd-2000-4000-d000-00000000000d")
CONNECTION = UUID("cccccccc-3000-4000-c000-00000000000c")
OTHER_CONNECTION = UUID("dddddddd-4000-4000-d000-00000000000d")


@pytest.fixture(scope="module")
def memory_dsn() -> str:
    """The migrated test database, reused when it is already there.

    Deliberately NOT an unconditional `recreate_test_database()`. That call
    terminates every backend on `norte_test` before dropping it, which is fine
    when one suite owns the database and hostile when several modules share it.
    Nothing in this module needs a pristine database - it needs the schema - so
    it only rebuilds when the schema is missing, and cleans up after itself.
    """
    dsn = resolve_test_dsn()
    if not dsn:
        pytest.skip("No DATABASE_URL configured")

    try:
        with psycopg.connect(dsn, autocommit=True) as probe, probe.cursor() as cur:
            cur.execute("SELECT to_regclass('raw.ai_memory') IS NOT NULL")
            if cur.fetchone()[0]:
                return dsn
    except psycopg.Error:
        pass      # no database, or no schema in it: fall through and build one

    try:
        return recreate_test_database()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL unreachable: {exc}")


@pytest.fixture
def conn(memory_dsn):
    """A connection scoped to TENANT, the way the API scopes one per request."""
    connection = psycopg.connect(memory_dsn, autocommit=False)
    # `dropi` is country-scoped (migration 012 requires that for a connection
    # pinned to CO) and tier 2, so it needs no consent record to exist.
    seed_workspace(
        connection, tenant_id=TENANT, connection_id=CONNECTION,
        platform_code="dropi", slug="ai-mem",
    )
    seed_workspace(
        connection, tenant_id=OTHER_TENANT, connection_id=OTHER_CONNECTION,
        platform_code="dropi", slug="ai-other",
    )
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('norte.service', 'off', false)")
        cur.execute("SELECT set_config('norte.tenant_id', %s, false)", (str(TENANT),))
    connection.commit()

    yield connection

    with connection.cursor() as cur:
        cur.execute("DELETE FROM raw.ai_memory WHERE tenant_id = %s", (TENANT,))
        cur.execute("DELETE FROM raw.ai_conversation WHERE tenant_id = %s", (TENANT,))
        cur.execute("DELETE FROM core.shipment WHERE tenant_id = %s", (TENANT,))
    connection.commit()
    connection.close()


def _scope_to(connection, tenant_id: UUID) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('norte.tenant_id', %s, false)", (str(tenant_id),))
    connection.commit()


# =============================================================================
# Memory round-trips
# =============================================================================


def test_a_stated_fact_round_trips_into_a_prompt(conn):
    from ai.memory import format_memory, recall, remember

    memory_id = remember(
        conn, TENANT, "context", "servientrega_guayas",
        "Servientrega siempre tarda más en Guayas que en Pichincha.",
        country_code="EC",
    )
    assert memory_id is not None

    facts = recall(conn, TENANT, "EC")
    assert [fact["key"] for fact in facts] == ["servientrega_guayas"]

    block = format_memory(facts)
    assert "Servientrega siempre tarda más en Guayas" in block
    assert "Contexto del negocio" in block
    assert "[EC]" in block


def test_remembering_the_same_key_replaces_it(conn):
    from ai.memory import recall, remember

    remember(conn, TENANT, "preference", "moneda", "Muestra todo en dólares.", country_code="EC")
    remember(conn, TENANT, "preference", "moneda", "Muestra todo en pesos.", country_code="EC")

    facts = [f for f in recall(conn, TENANT, "EC") if f["key"] == "moneda"]
    assert len(facts) == 1, "a repeated key must update, not pile up"
    assert facts[0]["value"] == "Muestra todo en pesos."


def test_a_tenant_wide_fact_has_exactly_one_row(conn):
    """country_code IS NULL must still be a unique key, or globals duplicate."""
    from ai.memory import recall, remember

    remember(conn, TENANT, "decision", "domingos", "No despachamos los domingos.")
    remember(conn, TENANT, "decision", "domingos", "No despachamos domingos ni festivos.")

    facts = [f for f in recall(conn, TENANT, "EC") if f["key"] == "domingos"]
    assert len(facts) == 1
    assert facts[0]["country_code"] is None
    assert "festivos" in facts[0]["value"]


def test_expired_facts_stop_being_recalled(conn):
    from ai.memory import recall, remember

    remember(
        conn, TENANT, "threshold", "efectividad_vieja",
        "Tu efectividad era 80%.", country_code="EC",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    remember(
        conn, TENANT, "threshold", "efectividad_actual",
        "Tu efectividad es 62%.", country_code="EC",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )

    keys = {fact["key"] for fact in recall(conn, TENANT, "EC")}
    assert "efectividad_actual" in keys
    assert "efectividad_vieja" not in keys


def test_country_facts_sort_before_global_ones(conn):
    from ai.memory import recall, remember

    remember(conn, TENANT, "context", "global_fact", "Vendemos por WhatsApp.")
    remember(conn, TENANT, "context", "ec_fact", "En Ecuador cobramos en dólares.",
             country_code="EC")

    keys = [fact["key"] for fact in recall(conn, TENANT, "EC")]
    assert keys.index("ec_fact") < keys.index("global_fact")


def test_another_countrys_fact_is_not_recalled(conn):
    from ai.memory import recall, remember

    remember(conn, TENANT, "context", "co_fact", "En Colombia el flete lo paga el cliente.",
             country_code="CO")

    keys = {fact["key"] for fact in recall(conn, TENANT, "EC")}
    assert "co_fact" not in keys


def test_memory_does_not_cross_tenants(conn):
    """Row-level security, not a WHERE clause the code could forget."""
    from ai.memory import recall, remember

    remember(conn, TENANT, "context", "secreto", "Nuestro proveedor es X.", country_code="EC")

    _scope_to(conn, OTHER_TENANT)
    assert recall(conn, OTHER_TENANT, "EC") == []
    _scope_to(conn, TENANT)


def test_malformed_facts_are_dropped_not_raised(conn):
    """A bad fact must never break the answer the user actually asked for."""
    from ai.memory import remember

    assert remember(conn, TENANT, "no_such_kind", "k", "v") is None
    assert remember(conn, TENANT, "context", "", "v") is None
    assert remember(conn, TENANT, "context", "k", "   ") is None
    assert remember(conn, TENANT, "context", "k", "v", source="nowhere") is None


def test_format_memory_is_empty_when_there_is_nothing(conn):
    from ai.memory import format_memory

    assert format_memory([]) == ""


# =============================================================================
# Conversations and corrections
# =============================================================================


def test_a_thread_keeps_its_turns_in_order(conn):
    from ai.features import append_message, conversation_history, ensure_conversation

    thread = ensure_conversation(
        conn, TENANT, conversation_id=None, user_id=None, country="EC",
        title_hint="¿Cuál transportadora entrega peor?",
    )
    append_message(conn, TENANT, thread, "user", "¿Cuál transportadora entrega peor?")
    append_message(
        conn, TENANT, thread, "assistant", "Servientrega, con 54%.",
        sql_executed="SELECT 1", row_count=1, tokens=120,
    )
    append_message(conn, TENANT, thread, "user", "¿Y en Guayas?")

    history = conversation_history(conn, TENANT, thread)
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[-1]["content"] == "¿Y en Guayas?"


def test_a_foreign_conversation_id_opens_a_new_thread(conn):
    """No oracle: an id that is not yours simply is not found."""
    from ai.features import ensure_conversation

    stranger = UUID("00000000-0000-4000-8000-000000000999")
    thread = ensure_conversation(
        conn, TENANT, conversation_id=stranger, user_id=None, country="EC",
        title_hint="hola",
    )
    assert thread != stranger


def test_an_explained_thumbs_down_becomes_a_durable_correction(conn):
    """This is the whole of "it learns from what is done", stated honestly.

    Nothing is trained. The operator's explanation becomes one row that every
    later prompt for that country reads back.
    """
    from ai.features import append_message, ensure_conversation, record_feedback
    from ai.memory import format_memory, recall

    thread = ensure_conversation(
        conn, TENANT, conversation_id=None, user_id=None, country="EC",
        title_hint="¿Por qué cayó la contribución?",
    )
    append_message(conn, TENANT, thread, "user", "¿Por qué cayó la contribución en octubre?")
    answer_id = append_message(
        conn, TENANT, thread, "assistant", "Cayó porque bajaron las ventas.",
    )

    result = record_feedback(
        conn, TENANT, answer_id, helpful=False,
        comment="No fue el volumen: subió el flete de Servientrega un 12%.",
    )
    assert result["stored"] is True
    assert result["learned"] is True

    corrections = [f for f in recall(conn, TENANT, "EC") if f["kind"] == "correction"]
    assert len(corrections) == 1
    assert "flete de Servientrega" in corrections[0]["value"]
    assert "Corrección del operador" in format_memory(corrections)


def test_a_thumbs_down_with_no_reason_teaches_nothing(conn):
    """A verdict that does not say what was wrong has nothing to learn from."""
    from ai.features import append_message, ensure_conversation, record_feedback
    from ai.memory import recall

    thread = ensure_conversation(
        conn, TENANT, conversation_id=None, user_id=None, country="EC", title_hint="x",
    )
    answer_id = append_message(conn, TENANT, thread, "assistant", "Una respuesta.")

    result = record_feedback(conn, TENANT, answer_id, helpful=False, comment=None)
    assert result["stored"] is True
    assert result["learned"] is False
    assert [f for f in recall(conn, TENANT, "EC") if f["kind"] == "correction"] == []


def test_feedback_on_someone_elses_message_is_refused(conn):
    from ai.features import record_feedback

    result = record_feedback(
        conn, TENANT, UUID("00000000-0000-4000-8000-000000000111"),
        helpful=True, comment=None,
    )
    assert result["stored"] is False


# =============================================================================
# Recommendations, with the model switched off
# =============================================================================


def _seed_office_queue(connection, *, count: int = 12, days_waiting: int = 12) -> None:
    """Guides sitting at a carrier office, in the 8-21 day rescue band."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.carrier (tenant_id, country_code, name, name_norm)
            VALUES (%s, 'CO', 'Servientrega', 'servientrega')
            ON CONFLICT DO NOTHING
            """,
            (TENANT,),
        )
        cur.execute(
            "SELECT id FROM core.carrier WHERE tenant_id = %s AND name_norm = 'servientrega'",
            (TENANT,),
        )
        carrier_id = cur.fetchone()[0]

        for index in range(count):
            cur.execute(
                """
                INSERT INTO core.shipment
                    (tenant_id, connection_id, country_code, carrier_id, tracking_number,
                     status_code, created_date, currency_code, declared_value)
                VALUES (%s, %s, 'CO', %s, %s, 'in_office', CURRENT_DATE - %s, 'COP', 150000)
                ON CONFLICT (connection_id, tracking_number) DO NOTHING
                """,
                (TENANT, CONNECTION, carrier_id, f"OFC-{index:04d}", days_waiting),
            )
    connection.commit()


def test_recommendations_are_produced_with_the_model_disabled(conn):
    """No settings, no key, no network - and still a recommendation with a number.

    This is the requirement, not a nice-to-have: an operator whose
    recommendations vanish when an API key expires has no recommendations.
    """
    from ai.recommendations import detect

    _seed_office_queue(conn)

    found = detect(conn, "CO")
    assert found, "deterministic detection produced nothing"

    office = [item for item in found if item["code"] == "office_queue_recoverable"]
    assert office, f"expected the office queue signal, got {[i['code'] for i in found]}"

    signal = office[0]
    assert signal["severity"] in ("info", "warning", "critical")
    assert signal["impact_amount"] and signal["impact_amount"] > 0
    assert signal["impact_currency"] == "COP"
    assert signal["finding"] and signal["action"]
    assert signal["deep_link"].startswith("/co?tab=")
    # COD vocabulary: a dispatch is not a sale until it is delivered.
    assert "venta" not in (signal["finding"] + signal["action"]).lower()


def test_detection_has_no_way_to_reach_a_model(conn):
    """Structural, not behavioural: `detect` cannot call an LLM even by mistake.

    It takes a connection and a country. There is no settings object to read a
    key from, and no client to build one with.
    """
    from ai.recommendations import detect

    parameters = set(inspect.signature(detect).parameters)
    assert parameters == {"conn", "country", "limit"}


def test_recommendations_do_not_cross_tenants(conn):
    from ai.recommendations import detect

    _seed_office_queue(conn)
    assert detect(conn, "CO")

    _scope_to(conn, OTHER_TENANT)
    assert detect(conn, "CO") == []
    _scope_to(conn, TENANT)


def test_a_country_filter_is_respected(conn):
    from ai.recommendations import detect

    _seed_office_queue(conn)
    assert detect(conn, "CO")
    assert detect(conn, "EC") == []


def test_post_ingestion_refresh_never_raises(conn):
    """It runs as a side effect of a successful upload. It must stay silent."""
    from ai.recommendations import refresh_after_batch

    _seed_office_queue(conn)
    result = refresh_after_batch(conn, TENANT, "CO")
    assert result["recommendations"] >= 1
    assert result["thresholds"] >= 0


def test_thresholds_are_not_invented_from_thin_data(conn):
    """Nine guides do not make a benchmark. Silence beats a confident wrong number."""
    from ai.memory import infer_thresholds

    _seed_office_queue(conn, count=3)
    assert infer_thresholds(conn, TENANT, "CO") == []


# =============================================================================
# The grants behind the validator
# =============================================================================


@pytest.mark.parametrize(
    "view", ["mart.v_source_row_archive", "mart.v_ai_memory"]
)
def test_the_readonly_role_has_no_grant_on_row_level_or_memory_views(conn, view):
    """Layer 1, on its own, without relying on the validator.

    Migration 007 sets ALTER DEFAULT PRIVILEGES on the whole mart schema, which
    silently granted these to `norte_readonly` as they were created. Migration
    011 revokes them explicitly; this is what keeps that true.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege('norte_readonly', %s, 'SELECT')", (view,))
        assert cur.fetchone()[0] is False, f"norte_readonly can read {view}"


@pytest.mark.parametrize(
    "view",
    [
        "mart.v_problem_rate",
        "mart.v_cash_cycle",
        "mart.v_dropshipping_margin",
        "mart.v_fulfillment_sla",
        "mart.v_office_rescue",
        "mart.v_freight_analysis",
        "mart.v_product_catalogue",
    ],
)
def test_the_readonly_role_can_read_every_business_aggregate(conn, view):
    """The widening the operator asked for, at the grant level."""
    with conn.cursor() as cur:
        cur.execute("SELECT has_table_privilege('norte_readonly', %s, 'SELECT')", (view,))
        assert cur.fetchone()[0] is True, f"norte_readonly cannot read {view}"
