"""Adversarial battery for the NL->SQL validator.

Every payload below must be REJECTED. These are not hypothetical: they are what
you get when a prompt injection lands in the question field, or when the model
simply misunderstands what it was asked for.

The validator is one of four layers (see ai/nl2sql.py). It is tested on its own
because the other three are infrastructure, and infrastructure gets
misconfigured.
"""

from __future__ import annotations

import pytest

from ai.nl2sql import ALLOWED_VIEWS, MAX_ROWS, validate_sql

# =============================================================================
# Must be rejected
# =============================================================================

WRITE_OPERATIONS = [
    "INSERT INTO core.shipment (tracking_number) VALUES ('X')",
    "UPDATE core.shipment SET declared_value = 0",
    "DELETE FROM core.shipment",
    "DROP TABLE core.shipment",
    "DROP VIEW mart.v_daily_contribution",
    "TRUNCATE core.shipment",
    "ALTER TABLE core.shipment ADD COLUMN evil text",
    "CREATE TABLE evil (id int)",
    "CREATE VIEW evil AS SELECT * FROM core.app_user",
    "GRANT SELECT ON core.app_user TO norte_readonly",
    "MERGE INTO core.shipment USING core.movement ON true WHEN MATCHED THEN DELETE",
]

STACKED_STATEMENTS = [
    "SELECT 1 FROM mart.v_aging; DROP TABLE core.shipment",
    "SELECT * FROM mart.v_aging; DELETE FROM core.movement;",
    "SELECT * FROM mart.v_aging;SELECT * FROM mart.v_aging",
    "SELECT * FROM mart.v_aging; INSERT INTO core.tenant (slug, name) VALUES ('x','y')",
]

FORBIDDEN_SCHEMAS = [
    "SELECT * FROM core.app_user",
    "SELECT email, password_hash FROM core.app_user",
    "SELECT * FROM core.shipment",
    "SELECT * FROM raw.load_batch",
    "SELECT * FROM stg.v_shipment_economics",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM information_schema.columns",
    "SELECT * FROM core.connection",
    "SELECT customer_hash FROM core.shipment LIMIT 10",
]

NESTED_ESCAPES = [
    "SELECT * FROM mart.v_aging WHERE shipments IN (SELECT count(*) FROM core.app_user)",
    "SELECT (SELECT password_hash FROM core.app_user LIMIT 1) AS x FROM mart.v_aging",
    "WITH leak AS (SELECT * FROM core.app_user) SELECT * FROM leak",
    "SELECT * FROM mart.v_aging JOIN core.app_user ON true",
    "SELECT * FROM mart.v_aging UNION ALL SELECT * FROM core.app_user",
    "SELECT * FROM (SELECT * FROM raw.auth_event) t",
]

SYSTEM_FUNCTIONS = [
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_sleep(30) FROM mart.v_aging",
    "SELECT * FROM mart.v_aging WHERE shipments > 0 AND pg_sleep(10) IS NULL",
    "SELECT current_setting('norte.tenant_id') FROM mart.v_aging",
    "SELECT set_config('norte.tenant_id', 'other', false) FROM mart.v_aging",
    "SELECT version() FROM mart.v_aging",
    "SELECT current_user FROM mart.v_aging",
    "SELECT dblink('host=evil', 'SELECT 1') FROM mart.v_aging",
    "SELECT lo_import('/etc/passwd') FROM mart.v_aging",
    "SELECT pg_terminate_backend(1) FROM mart.v_aging",
]

MALFORMED = [
    "",
    "   ",
    "not sql at all",
    "SELECT",
    "SELECT * FROM",
    "-- SELECT * FROM mart.v_aging",
]

UNKNOWN_VIEWS = [
    "SELECT * FROM mart.v_does_not_exist",
    "SELECT * FROM v_secret_view",
    "SELECT * FROM mart.pg_stat_activity",
]

ALL_ATTACKS = (
    WRITE_OPERATIONS
    + STACKED_STATEMENTS
    + FORBIDDEN_SCHEMAS
    + NESTED_ESCAPES
    + SYSTEM_FUNCTIONS
    + MALFORMED
    + UNKNOWN_VIEWS
)


@pytest.mark.parametrize("payload", ALL_ATTACKS)
def test_every_adversarial_payload_is_rejected(payload):
    result = validate_sql(payload)
    assert result.rejected, f"NOT REJECTED: {payload!r}"
    assert result.sql is None
    assert result.reason, "a rejection must explain itself"


def test_the_whole_battery_is_rejected_without_exception():
    """One assertion for the definition-of-done checklist: 100% rejected."""
    survivors = [p for p in ALL_ATTACKS if validate_sql(p).ok]
    assert survivors == [], f"{len(survivors)} adversarial payloads survived: {survivors}"


def test_battery_covers_every_attack_class():
    """Guards against someone quietly shrinking the battery."""
    assert len(WRITE_OPERATIONS) >= 10
    assert len(STACKED_STATEMENTS) >= 4
    assert len(FORBIDDEN_SCHEMAS) >= 8
    assert len(NESTED_ESCAPES) >= 6
    assert len(SYSTEM_FUNCTIONS) >= 10
    assert len(ALL_ATTACKS) >= 45


# =============================================================================
# Must be accepted
# =============================================================================

LEGITIMATE = [
    "SELECT * FROM mart.v_daily_contribution",
    "SELECT day, contribution FROM mart.v_daily_contribution WHERE country_code = 'CO'",
    "SELECT carrier_name, delivery_rate_pct FROM mart.v_carrier_effectiveness "
    "ORDER BY delivery_rate_pct ASC",
    "SELECT city_name FROM mart.v_geo_performance WHERE traffic_light = 'rojo'",
    "SELECT product_name, contribution FROM mart.v_product_performance "
    "WHERE contribution < 0 ORDER BY contribution",
    "SELECT sum(shipments) FROM mart.v_aging WHERE aging_bucket IN ('13-20', '21+')",
    "WITH ranked AS (SELECT carrier_name, delivery_rate_pct FROM mart.v_carrier_effectiveness) "
    "SELECT * FROM ranked ORDER BY delivery_rate_pct",
    "SELECT g.city_name, p.product_name FROM mart.v_geo_performance g, "
    "mart.v_product_performance p WHERE g.country_code = p.country_code LIMIT 10",
]


@pytest.mark.parametrize("payload", LEGITIMATE)
def test_legitimate_queries_pass(payload):
    result = validate_sql(payload)
    assert result.ok, f"wrongly rejected: {payload!r} -> {result.reason}"
    assert result.sql
    assert "LIMIT" in result.sql.upper()


def test_missing_limit_is_added():
    result = validate_sql("SELECT * FROM mart.v_aging")
    assert result.ok
    assert f"LIMIT {MAX_ROWS}" in result.sql.upper().replace("\n", " ")


def test_oversized_limit_is_capped():
    result = validate_sql("SELECT * FROM mart.v_aging LIMIT 99999")
    assert result.ok
    assert "99999" not in result.sql
    assert str(MAX_ROWS) in result.sql


def test_small_limit_is_preserved():
    result = validate_sql("SELECT * FROM mart.v_aging LIMIT 5")
    assert result.ok
    assert "LIMIT 5" in result.sql.upper().replace("\n", " ")


def test_markdown_fence_is_stripped():
    result = validate_sql("```sql\nSELECT day FROM mart.v_daily_contribution\n```")
    assert result.ok
    assert "```" not in result.sql


def test_trailing_semicolon_is_tolerated():
    result = validate_sql("SELECT day FROM mart.v_daily_contribution;")
    assert result.ok


def test_referenced_tables_are_reported():
    result = validate_sql(
        "SELECT * FROM mart.v_geo_performance g JOIN mart.v_product_performance p "
        "ON g.country_code = p.country_code"
    )
    assert result.ok
    assert result.tables == ["mart.v_geo_performance", "mart.v_product_performance"]


def test_allowlist_holds_only_business_views():
    """No view outside mart, and nothing that could expose raw rows or PII."""
    assert all(name.startswith("v_") for name in ALLOWED_VIEWS)
    forbidden_fragments = ("shipment_economics", "app_user", "source_row", "auth_event")
    for name in ALLOWED_VIEWS:
        assert not any(fragment in name for fragment in forbidden_fragments)
