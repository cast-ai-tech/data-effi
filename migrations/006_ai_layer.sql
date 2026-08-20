-- =============================================================================
-- Norte - 006 - AI layer: response cache, token budget, and the read-only role
--                the NL->SQL feature runs as.
--
-- Depends on: 001-003. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Cache. Key is a SHA-256 of (feature, tenant, country, context payload), so the
-- same question against the same numbers costs one LLM call, not one per view.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.ai_cache (
    cache_key    char(64) PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    feature      text NOT NULL CHECK (feature IN ('brief', 'alerts', 'ask')),
    country_code char(2),
    payload      jsonb NOT NULL,
    model        text NOT NULL,
    input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_cache_tenant ON raw.ai_cache (tenant_id, feature, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_cache_expiry ON raw.ai_cache (expires_at);

-- -----------------------------------------------------------------------------
-- Token spend per tenant per day. When the budget runs out the AI layer degrades
-- with a clear message; it never takes the dashboard down with it.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.ai_usage (
    tenant_id     uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    usage_date    date NOT NULL,
    feature       text NOT NULL,
    calls         integer NOT NULL DEFAULT 0,
    input_tokens  bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, usage_date, feature)
);

CREATE OR REPLACE FUNCTION raw.record_ai_usage(
    p_tenant_id uuid,
    p_feature text,
    p_input_tokens integer,
    p_output_tokens integer
)
RETURNS bigint
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_total bigint;
BEGIN
    INSERT INTO raw.ai_usage (tenant_id, usage_date, feature, calls, input_tokens, output_tokens)
    VALUES (p_tenant_id, CURRENT_DATE, p_feature, 1, p_input_tokens, p_output_tokens)
    ON CONFLICT (tenant_id, usage_date, feature) DO UPDATE SET
        calls         = raw.ai_usage.calls + 1,
        input_tokens  = raw.ai_usage.input_tokens + EXCLUDED.input_tokens,
        output_tokens = raw.ai_usage.output_tokens + EXCLUDED.output_tokens;

    SELECT sum(input_tokens + output_tokens) INTO v_total
    FROM raw.ai_usage WHERE tenant_id = p_tenant_id AND usage_date = CURRENT_DATE;

    RETURN COALESCE(v_total, 0);
END;
$fn$;

CREATE OR REPLACE FUNCTION raw.ai_tokens_used_today(p_tenant_id uuid)
RETURNS bigint
LANGUAGE sql
STABLE
AS $fn$
    SELECT COALESCE(sum(input_tokens + output_tokens), 0)
    FROM raw.ai_usage WHERE tenant_id = p_tenant_id AND usage_date = CURRENT_DATE;
$fn$;

-- -----------------------------------------------------------------------------
-- Deterministic alert signals.
--
-- The detections are SQL. The language model only writes the sentence and
-- estimates impact FROM THESE NUMBERS - it never decides what counts as a
-- problem, because a model that invents alerts is worse than no alerts.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_alert_signals AS

-- 1. A carrier whose delivery rate dropped hard against its own 30-day baseline.
WITH carrier_recent AS (
    SELECT
        e.tenant_id, e.country_code, e.carrier_id,
        count(*) FILTER (WHERE e.is_terminal)                   AS terminal_7d,
        count(*) FILTER (WHERE e.is_delivered)::numeric
            / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0)  AS rate_7d,
        sum(e.revenue_amount)                                   AS revenue_7d,
        avg(e.declared_value)                                   AS avg_value
    FROM stg.v_shipment_economics e
    WHERE e.tenant_id = core.current_tenant_id()
      AND e.created_date >= CURRENT_DATE - 7
    GROUP BY e.tenant_id, e.country_code, e.carrier_id
),
carrier_baseline AS (
    SELECT
        e.tenant_id, e.country_code, e.carrier_id,
        count(*) FILTER (WHERE e.is_delivered)::numeric
            / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0)  AS rate_30d,
        count(*) FILTER (WHERE e.is_terminal)                   AS terminal_30d
    FROM stg.v_shipment_economics e
    WHERE e.tenant_id = core.current_tenant_id()
      AND e.created_date BETWEEN CURRENT_DATE - 37 AND CURRENT_DATE - 8
    GROUP BY e.tenant_id, e.country_code, e.carrier_id
),
carrier_drop AS (
    SELECT
        r.tenant_id,
        r.country_code,
        'carrier_delivery_drop'                                 AS code,
        'critical'                                              AS severity,
        COALESCE(c.name, 'Sin transportadora')                  AS subject,
        round(r.rate_7d * 100, 1)                               AS current_value,
        round(b.rate_30d * 100, 1)                              AS baseline_value,
        round((b.rate_30d - r.rate_7d) * 100, 1)                AS delta_points,
        r.terminal_7d                                           AS volume,
        -- Money lost: guides that would have been delivered at the old rate.
        round((b.rate_30d - r.rate_7d) * r.terminal_7d * COALESCE(r.avg_value, 0), 2)
                                                                AS impact_amount,
        '/'|| lower(r.country_code) || '?tab=logistica'          AS deep_link
    FROM carrier_recent r
    JOIN carrier_baseline b
      ON b.tenant_id = r.tenant_id AND b.country_code = r.country_code
     AND b.carrier_id IS NOT DISTINCT FROM r.carrier_id
    LEFT JOIN core.carrier c ON c.id = r.carrier_id
    WHERE r.terminal_7d >= 20 AND b.terminal_30d >= 40
      AND (b.rate_30d - r.rate_7d) >= 0.08          -- 8 percentage points
),

-- 2. A product whose contribution per shipment went negative.
product_bleeding AS (
    SELECT
        p.tenant_id,
        p.country_code,
        'product_negative_contribution'                         AS code,
        'critical'                                              AS severity,
        p.product_name                                          AS subject,
        p.contribution_per_shipment                             AS current_value,
        0::numeric                                              AS baseline_value,
        p.contribution_per_shipment                             AS delta_points,
        p.shipments                                             AS volume,
        p.contribution                                          AS impact_amount,
        '/' || lower(p.country_code) || '?tab=efectividad'      AS deep_link
    FROM mart.v_product_performance p
    WHERE p.shipments >= 20 AND p.contribution_per_shipment < 0
),

-- 3. A province in red with enough volume to be worth acting on.
geo_red AS (
    SELECT
        g.tenant_id,
        g.country_code,
        'geo_red_zone'                                          AS code,
        'warning'                                               AS severity,
        g.city_name || ' (' || g.level1_name || ')'             AS subject,
        g.delivery_rate_pct                                     AS current_value,
        65::numeric                                             AS baseline_value,
        round(65 - g.delivery_rate_pct, 1)                      AS delta_points,
        g.shipments                                             AS volume,
        -- Freight burned on the guides that came back.
        round(g.returned * COALESCE(g.revenue / NULLIF(g.delivered, 0), 0) * 0.15, 2)
                                                                AS impact_amount,
        '/' || lower(g.country_code) || '?tab=efectividad'      AS deep_link
    FROM mart.v_geo_performance g
    WHERE g.traffic_light = 'rojo' AND g.shipments >= 25
),

-- 4. The 13+ aging band growing: money about to be lost, still recoverable.
aging_growth AS (
    SELECT
        a.tenant_id,
        a.country_code,
        'aging_13_plus'                                         AS code,
        'warning'                                               AS severity,
        'Guías de 13+ días'                                     AS subject,
        a.shipments::numeric                                    AS current_value,
        0::numeric                                              AS baseline_value,
        a.avg_days_open                                         AS delta_points,
        a.shipments                                             AS volume,
        a.value_at_risk                                         AS impact_amount,
        '/' || lower(a.country_code) || '?tab=logistica'        AS deep_link
    FROM mart.v_aging a
    WHERE a.aging_bucket IN ('13-20', '21+') AND a.shipments >= 10
),

-- 5. A connection that has not synced in 48 hours.
stale_connection AS (
    SELECT
        h.tenant_id,
        h.country_code,
        'connection_stale'                                      AS code,
        CASE WHEN h.health = 'error' THEN 'critical' ELSE 'warning' END AS severity,
        h.connection_name || ' (' || h.platform_name || ')'     AS subject,
        COALESCE(h.hours_since_sync, 999)                       AS current_value,
        48::numeric                                             AS baseline_value,
        round(COALESCE(h.hours_since_sync, 999) - 48, 1)        AS delta_points,
        0                                                       AS volume,
        NULL::numeric                                           AS impact_amount,
        '/settings'                                             AS deep_link
    FROM mart.v_connection_health h
    WHERE h.health IN ('stale', 'error', 'never_synced')
)

SELECT * FROM carrier_drop
UNION ALL SELECT * FROM product_bleeding
UNION ALL SELECT * FROM geo_red
UNION ALL SELECT * FROM aging_growth
UNION ALL SELECT * FROM stale_connection;

COMMENT ON VIEW mart.v_alert_signals IS
    'Deterministic alert detection. The LLM only phrases these rows; it never decides them.';
