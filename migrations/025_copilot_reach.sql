-- =============================================================================
-- Data Effi - 025 - The copilot could read one customer at a time.
--
-- Migration 015 states that `v_orders` and `v_customer_metrics` are
-- "deliberately NOT granted to norte_readonly". The comment was sincere and the
-- grant happened anyway: 007 runs
--
--     ALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT SELECT ON TABLES TO norte_readonly;
--
-- so every view created in `mart` after that point is handed to the copilot's
-- role automatically, at creation, without anyone writing a GRANT. Migration
-- 011 found the same hole and closed it for two views by hand; each new
-- row-level view since has quietly reopened it.
--
-- WHAT WAS REACHABLE
-- v_orders             one row per guide: customer name and phone ciphertext,
--                      address ciphertext, tracking number, city.
-- v_customer_metrics   one row per person, with their contact ciphertext.
-- v_order_timeline     every money and status event of a named guide.
-- v_source_row_archive every original column of every uploaded row - the one
--                      migration 010 called out by name as never grantable.
--
-- The NL->SQL validator refuses all four (they are not in ALLOWED_VIEWS), so
-- nothing leaked. But the grant is the SECOND wall, and the whole reason it
-- exists is that the first one is a text filter in front of a language model.
-- Two walls where one is decorative is one wall.
--
-- THE FIX IS THE DEFAULT, NOT THE FOUR VIEWS
-- Revoking these four and moving on would leave the next PII view to leak in
-- exactly the same silent way. So the blanket default is withdrawn: from here
-- the copilot is granted each business aggregate EXPLICITLY. A new view is
-- unreachable until somebody decides otherwise, which is the safe direction to
-- fail for a role that answers generated SQL.
--
-- Depends on: 024. Idempotent.
-- =============================================================================

-- 1. Stop handing future mart views to the copilot automatically.
ALTER DEFAULT PRIVILEGES IN SCHEMA mart REVOKE SELECT ON TABLES FROM norte_readonly;

-- 2. Take back everything, then re-grant exactly `ai.nl2sql.ALLOWED_VIEWS`.
--    The two layers must agree: a view the validator accepts but the role
--    cannot read answers a legitimate question with a privilege error, and a
--    view the role can read but the validator refuses is a second wall that
--    only looks like one.
REVOKE SELECT ON ALL TABLES IN SCHEMA mart FROM norte_readonly;

GRANT SELECT ON
    mart.v_aging,
    mart.v_alert_signals,
    mart.v_available_platforms,
    mart.v_batch_history,
    mart.v_carrier_effectiveness,
    mart.v_cash_cycle,
    mart.v_cohort_maturation,
    mart.v_connection_health,
    mart.v_country_dashboard_layout,
    mart.v_cpa_roas,
    mart.v_cs_confirmation,
    mart.v_daily_contribution,
    mart.v_dropshipping_margin,
    mart.v_freight_analysis,
    mart.v_fulfillment_sla,
    mart.v_geo_performance,
    mart.v_global_summary,
    mart.v_office_rescue,
    mart.v_problem_rate,
    mart.v_product_catalogue,
    mart.v_product_performance
TO norte_readonly;

-- Not granted, and each for a reason worth writing down:
--   v_orders, v_customer_metrics, v_order_timeline - row-level, one real person
--       per row. The operator asked the copilot to reach "any data in the
--       platform"; they meant every business aggregate, not every customer.
--   v_source_row_archive - every original column of every uploaded file.
--   v_ai_memory - what the copilot remembers, partly written by users. A
--       generated query that can read it is one that can be steered by it.
--   v_available_platforms, v_connection_health, v_platform_catalogue,
--   v_batch_history, v_country_dashboard_layout - configuration and plumbing,
--       not questions about the business.
--   v_alert_signals - feeds the alert engine directly; the copilot reads the
--       alerts, not the raw signals.
