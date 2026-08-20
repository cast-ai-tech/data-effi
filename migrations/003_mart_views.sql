-- =============================================================================
-- Norte - 003 - Staging helpers and mart.* KPI views.
--
-- CONTRACT: the API never computes a KPI. It reads these views and filters by
-- country / store / date range. The AI layer may only ever see `mart`.
--
-- TENANT ISOLATION: every mart view filters by core.current_tenant_id(), which
-- reads the `norte.tenant_id` GUC. The caller MUST run, inside its transaction:
--     SET LOCAL norte.tenant_id = '<uuid>';
-- If the GUC is unset the views return ZERO rows (fail closed) - this is what
-- keeps a generated NL->SQL query from ever reaching another tenant's data.
--
-- Depends on: 001, 002. Idempotent.
-- =============================================================================

CREATE OR REPLACE FUNCTION core.current_tenant_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $fn$
    SELECT nullif(current_setting('norte.tenant_id', true), '')::uuid;
$fn$;

COMMENT ON FUNCTION core.current_tenant_id IS
    'Tenant of the current transaction, from the norte.tenant_id GUC. NULL = no access.';

-- =============================================================================
-- STAGING HELPERS (never exposed to the AI layer or the API)
-- =============================================================================

-- Money booked against each shipment, by category. `amount` is always stored as
-- a positive magnitude; direction comes from movement_type.sign.
CREATE OR REPLACE VIEW stg.v_movement_by_shipment AS
SELECT
    m.shipment_id,
    m.tenant_id,
    sum(m.amount) FILTER (WHERE mt.category = 'revenue')    AS revenue_amount,
    sum(m.amount) FILTER (WHERE mt.category = 'freight')    AS freight_amount,
    sum(m.amount) FILTER (WHERE mt.category = 'cogs')       AS cogs_amount,
    sum(m.amount) FILTER (WHERE mt.category = 'fee')        AS fee_amount,
    sum(m.amount * mt.sign) FILTER (WHERE mt.category = 'adjustment') AS adjustment_amount,
    count(*)                                                 AS movement_count
FROM core.movement m
JOIN core.movement_type mt ON mt.code = m.movement_type_code
WHERE m.shipment_id IS NOT NULL
GROUP BY m.shipment_id, m.tenant_id;

-- One row per shipment with its economics resolved.
-- Precedence: booked movements > values on the guide > product catalog cost.
CREATE OR REPLACE VIEW stg.v_shipment_economics AS
SELECT
    s.id                AS shipment_id,
    s.tenant_id,
    s.connection_id,
    s.country_code,
    s.store_id,
    s.tracking_number,
    s.created_date,
    s.delivered_at,
    s.returned_at,
    s.dispatched_at,
    s.carrier_id,
    s.geo_id,
    s.product_id,
    s.quantity,
    s.currency_code,
    s.status_code,
    sc.bucket,
    sc.is_terminal,
    sc.is_delivered,
    sc.is_returned,
    sc.sort_order       AS status_sort_order,
    s.declared_value,

    -- Days from guide creation to delivery (NULL while undelivered).
    CASE WHEN s.delivered_at IS NOT NULL
         THEN (s.delivered_at::date - s.created_date) END       AS days_to_deliver,
    -- Days a still-open guide has been alive.
    CASE WHEN NOT sc.is_terminal
         THEN (CURRENT_DATE - s.created_date) END               AS days_open,

    COALESCE(
        mv.revenue_amount,
        CASE WHEN sc.is_delivered THEN COALESCE(s.cod_collected, s.declared_value) END,
        0
    )::numeric(14, 2)                                           AS revenue_amount,

    COALESCE(
        mv.freight_amount,
        COALESCE(s.freight_cost, 0) + COALESCE(s.return_freight_cost, 0),
        0
    )::numeric(14, 2)                                           AS freight_amount,

    COALESCE(
        mv.cogs_amount,
        s.product_cost,
        CASE WHEN sc.is_delivered THEN p.unit_cost * s.quantity END,
        0
    )::numeric(14, 2)                                           AS cogs_amount,

    COALESCE(mv.fee_amount, s.platform_fee, 0)::numeric(14, 2)  AS fee_amount,
    COALESCE(mv.adjustment_amount, 0)::numeric(14, 2)           AS adjustment_amount,
    COALESCE(mv.movement_count, 0)                              AS movement_count
FROM core.shipment s
JOIN core.status_canon sc ON sc.code = s.status_code
LEFT JOIN stg.v_movement_by_shipment mv ON mv.shipment_id = s.id
LEFT JOIN core.product p ON p.id = s.product_id;

-- Which data domains a tenant has per country, and whether data actually landed.
CREATE OR REPLACE VIEW stg.v_country_domain_status AS
WITH declared AS (
    SELECT DISTINCT
        c.tenant_id,
        c.country_code,
        d.domain
    FROM core.connection c
    JOIN core.platform p ON p.code = c.platform_code
    CROSS JOIN LATERAL unnest(p.data_domains) AS d(domain)
    WHERE c.status = 'active'
),
domains AS (
    SELECT wc.tenant_id, wc.country_code, d.domain
    FROM core.workspace_country wc
    CROSS JOIN (VALUES ('shipments'), ('movements'), ('ads'), ('cs'), ('catalog')) AS d(domain)
    WHERE wc.is_active
)
SELECT
    dm.tenant_id,
    dm.country_code,
    dm.domain,
    (dc.domain IS NOT NULL) AS has_connection,
    CASE dm.domain
        WHEN 'shipments' THEN EXISTS (SELECT 1 FROM core.shipment x
                                      WHERE x.tenant_id = dm.tenant_id AND x.country_code = dm.country_code)
        WHEN 'movements' THEN EXISTS (SELECT 1 FROM core.movement x
                                      WHERE x.tenant_id = dm.tenant_id AND x.country_code = dm.country_code)
        WHEN 'ads'       THEN EXISTS (SELECT 1 FROM core.ad_spend x
                                      WHERE x.tenant_id = dm.tenant_id AND x.country_code = dm.country_code)
        WHEN 'cs'        THEN EXISTS (SELECT 1 FROM core.cs_interaction x
                                      WHERE x.tenant_id = dm.tenant_id AND x.country_code = dm.country_code)
        WHEN 'catalog'   THEN EXISTS (SELECT 1 FROM core.product x WHERE x.tenant_id = dm.tenant_id)
        ELSE false
    END AS has_data
FROM domains dm
LEFT JOIN declared dc
       ON dc.tenant_id = dm.tenant_id
      AND dc.country_code = dm.country_code
      AND dc.domain = dm.domain;

-- =============================================================================
-- MART: KPI views (the API and the AI layer read ONLY these)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Daily contribution, by cohort date (the day the guide was created).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_daily_contribution AS
WITH ship AS (
    SELECT
        e.tenant_id,
        e.country_code,
        e.store_id,
        e.created_date                                            AS day,
        count(*)                                                  AS shipments,
        count(*) FILTER (WHERE e.is_delivered)                    AS delivered,
        count(*) FILTER (WHERE e.is_returned)                     AS returned,
        count(*) FILTER (WHERE NOT e.is_terminal)                 AS in_transit,
        count(*) FILTER (WHERE e.is_terminal AND e.bucket = 'dead') AS dead,
        sum(e.declared_value)                                     AS declared_value,
        sum(e.revenue_amount)                                     AS revenue,
        sum(e.freight_amount)                                     AS freight,
        sum(e.cogs_amount)                                        AS cogs,
        sum(e.fee_amount)                                         AS fees,
        sum(e.adjustment_amount)                                  AS adjustments,
        min(e.currency_code)                                      AS currency_code
    FROM stg.v_shipment_economics e
    WHERE e.tenant_id = core.current_tenant_id()
    GROUP BY e.tenant_id, e.country_code, e.store_id, e.created_date
),
ads AS (
    SELECT tenant_id, country_code, store_id, spend_date AS day, sum(spend) AS ad_spend
    FROM core.ad_spend
    WHERE tenant_id = core.current_tenant_id()
    GROUP BY tenant_id, country_code, store_id, spend_date
)
SELECT
    s.tenant_id,
    s.country_code,
    s.store_id,
    st.name                                     AS store_name,
    s.day,
    s.shipments,
    s.delivered,
    s.returned,
    s.in_transit,
    s.dead,
    s.declared_value,
    s.revenue,
    s.freight,
    s.cogs,
    s.fees,
    s.adjustments,
    a.ad_spend,
    (s.revenue - s.freight - s.cogs - s.fees + s.adjustments
        - COALESCE(a.ad_spend, 0))::numeric(14, 2)              AS contribution,
    round(
        (s.revenue - s.freight - s.cogs - s.fees + s.adjustments - COALESCE(a.ad_spend, 0))
        / NULLIF(s.revenue, 0) * 100, 2)                        AS contribution_margin_pct,
    round(s.delivered::numeric
        / NULLIF(s.delivered + s.returned + s.dead, 0) * 100, 2) AS delivery_rate_terminal_pct,
    round(s.delivered::numeric / NULLIF(s.shipments, 0) * 100, 2) AS delivery_rate_dispatched_pct,
    s.currency_code,
    -- Honest flag: ad spend absent means contribution is gross of media cost.
    (a.ad_spend IS NULL)                                        AS ad_spend_missing
FROM ship s
LEFT JOIN ads a
       ON a.tenant_id = s.tenant_id
      AND a.country_code = s.country_code
      AND a.day = s.day
      AND a.store_id IS NOT DISTINCT FROM s.store_id
LEFT JOIN core.store st ON st.id = s.store_id;

-- -----------------------------------------------------------------------------
-- Carrier effectiveness
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_carrier_effectiveness AS
SELECT
    e.tenant_id,
    e.country_code,
    e.carrier_id,
    COALESCE(c.name, 'Sin transportadora')                      AS carrier_name,
    min(e.created_date)                                         AS first_shipment_date,
    max(e.created_date)                                         AS last_shipment_date,
    count(*)                                                    AS shipments,
    count(*) FILTER (WHERE e.is_delivered)                      AS delivered,
    count(*) FILTER (WHERE e.is_returned)                       AS returned,
    count(*) FILTER (WHERE NOT e.is_terminal)                   AS in_transit,
    round(count(*) FILTER (WHERE e.is_delivered)::numeric
        / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) * 100, 2) AS delivery_rate_pct,
    round(count(*) FILTER (WHERE e.is_returned)::numeric
        / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) * 100, 2) AS return_rate_pct,
    round(avg(e.days_to_deliver)::numeric, 1)                   AS avg_days_to_deliver,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY e.days_to_deliver)::numeric(6, 1)
                                                                AS p90_days_to_deliver,
    sum(e.freight_amount)::numeric(14, 2)                       AS freight_total,
    round(sum(e.freight_amount) / NULLIF(count(*), 0), 2)       AS avg_freight_per_shipment,
    sum(e.revenue_amount)::numeric(14, 2)                       AS revenue,
    (sum(e.revenue_amount) - sum(e.freight_amount) - sum(e.cogs_amount)
        - sum(e.fee_amount))::numeric(14, 2)                    AS contribution,
    min(e.currency_code)                                        AS currency_code
FROM stg.v_shipment_economics e
LEFT JOIN core.carrier c ON c.id = e.carrier_id
WHERE e.tenant_id = core.current_tenant_id()
GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;

-- -----------------------------------------------------------------------------
-- Geography: traffic light by city
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_geo_performance AS
SELECT
    e.tenant_id,
    e.country_code,
    e.geo_id,
    COALESCE(g.level1_name, 'Sin dato')                         AS level1_name,
    COALESCE(g.city_name, 'Sin dato')                           AS city_name,
    g.city_normalized,
    count(*)                                                    AS shipments,
    count(*) FILTER (WHERE e.is_delivered)                      AS delivered,
    count(*) FILTER (WHERE e.is_returned)                       AS returned,
    count(*) FILTER (WHERE NOT e.is_terminal)                   AS in_transit,
    round(count(*) FILTER (WHERE e.is_delivered)::numeric
        / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) * 100, 2) AS delivery_rate_pct,
    sum(e.revenue_amount)::numeric(14, 2)                       AS revenue,
    (sum(e.revenue_amount) - sum(e.freight_amount) - sum(e.cogs_amount)
        - sum(e.fee_amount))::numeric(14, 2)                    AS contribution,
    round(avg(e.days_to_deliver)::numeric, 1)                   AS avg_days_to_deliver,
    CASE
        WHEN count(*) FILTER (WHERE e.is_terminal) < 10 THEN 'sin_datos'
        WHEN count(*) FILTER (WHERE e.is_delivered)::numeric
             / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) >= 0.80 THEN 'verde'
        WHEN count(*) FILTER (WHERE e.is_delivered)::numeric
             / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) >= 0.65 THEN 'amarillo'
        ELSE 'rojo'
    END                                                         AS traffic_light,
    min(e.currency_code)                                        AS currency_code
FROM stg.v_shipment_economics e
LEFT JOIN core.geo g ON g.id = e.geo_id
WHERE e.tenant_id = core.current_tenant_id()
GROUP BY e.tenant_id, e.country_code, e.geo_id, g.level1_name, g.city_name, g.city_normalized;

-- -----------------------------------------------------------------------------
-- Products: margin x delivery
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_product_performance AS
SELECT
    e.tenant_id,
    e.country_code,
    e.product_id,
    COALESCE(p.name, 'Sin producto')                            AS product_name,
    p.sku,
    sup.name                                                    AS supplier_name,
    count(*)                                                    AS shipments,
    sum(e.quantity)                                             AS units,
    count(*) FILTER (WHERE e.is_delivered)                      AS delivered,
    count(*) FILTER (WHERE e.is_returned)                       AS returned,
    round(count(*) FILTER (WHERE e.is_delivered)::numeric
        / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) * 100, 2) AS delivery_rate_pct,
    sum(e.revenue_amount)::numeric(14, 2)                       AS revenue,
    sum(e.cogs_amount)::numeric(14, 2)                          AS cogs,
    sum(e.freight_amount)::numeric(14, 2)                       AS freight,
    (sum(e.revenue_amount) - sum(e.freight_amount) - sum(e.cogs_amount)
        - sum(e.fee_amount))::numeric(14, 2)                    AS contribution,
    round((sum(e.revenue_amount) - sum(e.freight_amount) - sum(e.cogs_amount)
        - sum(e.fee_amount)) / NULLIF(count(*), 0), 2)          AS contribution_per_shipment,
    round((sum(e.revenue_amount) - sum(e.freight_amount) - sum(e.cogs_amount)
        - sum(e.fee_amount)) / NULLIF(sum(e.revenue_amount), 0) * 100, 2) AS margin_pct,
    min(e.currency_code)                                        AS currency_code
FROM stg.v_shipment_economics e
LEFT JOIN core.product p  ON p.id = e.product_id
LEFT JOIN core.supplier sup ON sup.id = p.supplier_id
WHERE e.tenant_id = core.current_tenant_id()
GROUP BY e.tenant_id, e.country_code, e.product_id, p.name, p.sku, sup.name;

-- -----------------------------------------------------------------------------
-- Cohort maturation: how each cohort's delivery rate climbs day by day.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_cohort_maturation AS
WITH cohorts AS (
    SELECT
        e.tenant_id,
        e.country_code,
        e.created_date       AS cohort_date,
        count(*)             AS cohort_size,
        min(e.currency_code) AS currency_code
    FROM stg.v_shipment_economics e
    WHERE e.tenant_id = core.current_tenant_id()
    GROUP BY e.tenant_id, e.country_code, e.created_date
),
days AS (
    SELECT generate_series(0, 30) AS days_since
)
SELECT
    c.tenant_id,
    c.country_code,
    c.cohort_date,
    c.cohort_size,
    d.days_since,
    count(e.shipment_id) FILTER (WHERE e.days_to_deliver <= d.days_since) AS delivered_by_day,
    round(count(e.shipment_id) FILTER (WHERE e.days_to_deliver <= d.days_since)::numeric
        / NULLIF(c.cohort_size, 0) * 100, 2)                    AS delivery_rate_pct,
    -- A cohort younger than days_since has not had the chance yet.
    (c.cohort_date + d.days_since <= CURRENT_DATE)              AS is_observable,
    (c.cohort_date + COALESCE(wc.maturation_days, 21) <= CURRENT_DATE) AS is_mature,
    COALESCE(wc.maturation_days, 21)                            AS maturation_days,
    c.currency_code
FROM cohorts c
CROSS JOIN days d
LEFT JOIN stg.v_shipment_economics e
       ON e.tenant_id = c.tenant_id
      AND e.country_code = c.country_code
      AND e.created_date = c.cohort_date
LEFT JOIN core.workspace_country wc
       ON wc.tenant_id = c.tenant_id AND wc.country_code = c.country_code
GROUP BY c.tenant_id, c.country_code, c.cohort_date, c.cohort_size, d.days_since,
         wc.maturation_days, c.currency_code;

-- -----------------------------------------------------------------------------
-- Aging: open guides by age bucket. 13+ is where money goes to die.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_aging AS
SELECT
    e.tenant_id,
    e.country_code,
    CASE
        WHEN e.days_open <= 3  THEN '0-3'
        WHEN e.days_open <= 7  THEN '4-7'
        WHEN e.days_open <= 12 THEN '8-12'
        WHEN e.days_open <= 20 THEN '13-20'
        ELSE '21+'
    END                                                         AS aging_bucket,
    CASE
        WHEN e.days_open <= 3  THEN 1
        WHEN e.days_open <= 7  THEN 2
        WHEN e.days_open <= 12 THEN 3
        WHEN e.days_open <= 20 THEN 4
        ELSE 5
    END                                                         AS bucket_order,
    count(*)                                                    AS shipments,
    sum(e.declared_value)::numeric(14, 2)                       AS value_at_risk,
    round(avg(e.days_open)::numeric, 1)                         AS avg_days_open,
    min(e.currency_code)                                        AS currency_code
FROM stg.v_shipment_economics e
WHERE e.tenant_id = core.current_tenant_id()
  AND NOT e.is_terminal
  AND e.days_open IS NOT NULL
GROUP BY e.tenant_id, e.country_code, 3, 4;

-- -----------------------------------------------------------------------------
-- Customer service confirmation
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_cs_confirmation AS
SELECT
    ci.tenant_id,
    ci.country_code,
    ci.interaction_date                                         AS day,
    count(*)                                                    AS interactions,
    count(*) FILTER (WHERE ci.outcome = 'confirmed')            AS confirmed,
    count(*) FILTER (WHERE ci.outcome = 'rejected')             AS rejected,
    count(*) FILTER (WHERE ci.outcome = 'no_answer')            AS no_answer,
    count(*) FILTER (WHERE ci.outcome = 'pending')              AS pending,
    round(count(*) FILTER (WHERE ci.outcome = 'confirmed')::numeric
        / NULLIF(count(*), 0) * 100, 2)                         AS confirmation_rate_pct,
    round(avg(ci.attempts)::numeric, 2)                         AS avg_attempts
FROM core.cs_interaction ci
WHERE ci.tenant_id = core.current_tenant_id()
GROUP BY ci.tenant_id, ci.country_code, ci.interaction_date;

-- -----------------------------------------------------------------------------
-- CPA / ROAS. Rows only exist where ad spend exists - the widget stays blocked
-- until an ads connection lands data.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_cpa_roas AS
WITH ads AS (
    SELECT tenant_id, country_code, spend_date AS day,
           sum(spend) AS spend, sum(clicks) AS clicks,
           sum(impressions) AS impressions, min(currency_code) AS currency_code
    FROM core.ad_spend
    WHERE tenant_id = core.current_tenant_id()
    GROUP BY tenant_id, country_code, spend_date
),
ship AS (
    SELECT tenant_id, country_code, created_date AS day,
           count(*) AS shipments,
           count(*) FILTER (WHERE is_delivered) AS delivered,
           sum(revenue_amount) AS revenue
    FROM stg.v_shipment_economics
    WHERE tenant_id = core.current_tenant_id()
    GROUP BY tenant_id, country_code, created_date
)
SELECT
    a.tenant_id,
    a.country_code,
    a.day,
    a.spend::numeric(14, 2)                                     AS ad_spend,
    a.impressions,
    a.clicks,
    COALESCE(s.shipments, 0)                                    AS shipments,
    COALESCE(s.delivered, 0)                                    AS delivered,
    COALESCE(s.revenue, 0)::numeric(14, 2)                      AS revenue,
    round(a.spend / NULLIF(s.shipments, 0), 2)                  AS cpa_dispatched,
    round(a.spend / NULLIF(s.delivered, 0), 2)                  AS cpa_delivered,
    round(COALESCE(s.revenue, 0) / NULLIF(a.spend, 0), 2)       AS roas,
    a.currency_code
FROM ads a
LEFT JOIN ship s
       ON s.tenant_id = a.tenant_id
      AND s.country_code = a.country_code
      AND s.day = a.day;

-- -----------------------------------------------------------------------------
-- Global multi-country summary, converted with the latest known FX rate.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_global_summary AS
WITH per_country AS (
    SELECT
        e.tenant_id,
        e.country_code,
        min(e.currency_code)                                    AS currency_code,
        count(*)                                                AS shipments,
        count(*) FILTER (WHERE e.is_delivered)                  AS delivered,
        count(*) FILTER (WHERE e.is_returned)                   AS returned,
        count(*) FILTER (WHERE NOT e.is_terminal)               AS in_transit,
        sum(e.revenue_amount)                                   AS revenue,
        sum(e.revenue_amount) - sum(e.freight_amount)
            - sum(e.cogs_amount) - sum(e.fee_amount)            AS contribution,
        max(e.created_date)                                     AS last_shipment_date
    FROM stg.v_shipment_economics e
    WHERE e.tenant_id = core.current_tenant_id()
    GROUP BY e.tenant_id, e.country_code
),
ads AS (
    SELECT tenant_id, country_code, sum(spend) AS ad_spend
    FROM core.ad_spend
    WHERE tenant_id = core.current_tenant_id()
    GROUP BY tenant_id, country_code
),
latest_fx AS (
    SELECT DISTINCT ON (base_currency, quote_currency)
        base_currency, quote_currency, rate, rate_date
    FROM core.fx_rate
    WHERE quote_currency = 'USD'
    ORDER BY base_currency, quote_currency, rate_date DESC
)
SELECT
    pc.tenant_id,
    pc.country_code,
    co.name                                                     AS country_name,
    pc.currency_code,
    pc.shipments,
    pc.delivered,
    pc.returned,
    pc.in_transit,
    round(pc.delivered::numeric / NULLIF(pc.delivered + pc.returned, 0) * 100, 2)
                                                                AS delivery_rate_pct,
    pc.revenue::numeric(14, 2)                                  AS revenue,
    COALESCE(a.ad_spend, 0)::numeric(14, 2)                     AS ad_spend,
    (pc.contribution - COALESCE(a.ad_spend, 0))::numeric(14, 2) AS contribution,
    fx.rate                                                     AS fx_rate_to_usd,
    fx.rate_date                                                AS fx_rate_date,
    CASE WHEN fx.rate IS NOT NULL
         THEN round((pc.contribution - COALESCE(a.ad_spend, 0)) * fx.rate, 2) END
                                                                AS contribution_usd,
    (fx.rate IS NULL)                                           AS fx_missing,
    pc.last_shipment_date
FROM per_country pc
JOIN core.country co ON co.code = pc.country_code
LEFT JOIN ads a ON a.tenant_id = pc.tenant_id AND a.country_code = pc.country_code
LEFT JOIN latest_fx fx ON fx.base_currency = pc.currency_code;

-- -----------------------------------------------------------------------------
-- Onboarding support: which platforms exist for each country the tenant runs,
-- and whether they are already connected.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_available_platforms AS
SELECT
    wc.tenant_id,
    wc.country_code,
    p.code                                                      AS platform_code,
    p.name                                                      AS platform_name,
    p.tier,
    p.data_domains,
    p.requires_consent,
    p.docs_url,
    count(c.id)                                                 AS connection_count,
    count(c.id) FILTER (WHERE c.status = 'active')              AS active_connection_count,
    (count(c.id) FILTER (WHERE c.status = 'active') > 0)        AS is_connected
FROM core.workspace_country wc
JOIN core.platform_country pc ON pc.country_code = wc.country_code
JOIN core.platform p ON p.code = pc.platform_code AND p.is_active
LEFT JOIN core.connection c
       ON c.tenant_id = wc.tenant_id
      AND c.country_code = wc.country_code
      AND c.platform_code = p.code
WHERE wc.tenant_id = core.current_tenant_id()
  AND wc.is_active
GROUP BY wc.tenant_id, wc.country_code, p.code, p.name, p.tier,
         p.data_domains, p.requires_consent, p.docs_url;

-- -----------------------------------------------------------------------------
-- Connection health. Drives the settings screen and the "stale connection" alert.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_connection_health AS
SELECT
    c.tenant_id,
    c.id                                                        AS connection_id,
    c.name                                                      AS connection_name,
    c.country_code,
    c.platform_code,
    p.name                                                      AS platform_name,
    p.tier,
    c.status,
    c.consent_granted_at,
    c.last_sync_at,
    c.last_error,
    round(EXTRACT(EPOCH FROM (now() - c.last_sync_at)) / 3600.0, 1) AS hours_since_sync,
    b.last_batch_at,
    b.batches_7d,
    b.failed_batches_7d,
    CASE
        WHEN c.status = 'error'                                     THEN 'error'
        WHEN c.status = 'disabled'                                  THEN 'disabled'
        WHEN c.last_sync_at IS NULL                                 THEN 'never_synced'
        WHEN c.last_sync_at < now() - interval '48 hours'           THEN 'stale'
        ELSE 'ok'
    END                                                         AS health
FROM core.connection c
JOIN core.platform p ON p.code = c.platform_code
LEFT JOIN LATERAL (
    SELECT
        max(lb.started_at)                              AS last_batch_at,
        count(*) FILTER (WHERE lb.started_at > now() - interval '7 days') AS batches_7d,
        count(*) FILTER (WHERE lb.started_at > now() - interval '7 days'
                           AND lb.status = 'failed')    AS failed_batches_7d
    FROM raw.load_batch lb
    WHERE lb.connection_id = c.id
) b ON true
WHERE c.tenant_id = core.current_tenant_id();

-- -----------------------------------------------------------------------------
-- THE LAYOUT VIEW. The frontend renders exactly what this returns; it never
-- decides on its own whether a widget is available.
--   available - all required domains connected AND carrying data
--   degraded  - required domains present, but an optional domain is missing
--   blocked   - a required domain has no active connection (or no data at all)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_country_dashboard_layout AS
WITH ds AS (
    SELECT * FROM stg.v_country_domain_status
    WHERE tenant_id = core.current_tenant_id()
),
w AS (
    SELECT wc.tenant_id, wc.country_code, cat.*
    FROM core.workspace_country wc
    CROSS JOIN core.widget_catalog cat
    WHERE wc.tenant_id = core.current_tenant_id()
      AND wc.is_active
      AND cat.is_active
),
evaluated AS (
    SELECT
        w.tenant_id,
        w.country_code,
        w.widget_code,
        w.tab,
        w.title,
        w.description,
        w.blocked_message,
        w.sort_order,
        w.required_domains,
        w.optional_domains,
        ARRAY(
            SELECT d FROM unnest(w.required_domains) AS d
            WHERE NOT EXISTS (
                SELECT 1 FROM ds
                WHERE ds.country_code = w.country_code
                  AND ds.domain = d
                  AND ds.has_connection
            )
        ) AS missing_required,
        ARRAY(
            SELECT d FROM unnest(w.required_domains) AS d
            WHERE EXISTS (
                SELECT 1 FROM ds
                WHERE ds.country_code = w.country_code
                  AND ds.domain = d
                  AND ds.has_connection
                  AND NOT ds.has_data
            )
        ) AS awaiting_data,
        ARRAY(
            SELECT d FROM unnest(w.optional_domains) AS d
            WHERE NOT EXISTS (
                SELECT 1 FROM ds
                WHERE ds.country_code = w.country_code
                  AND ds.domain = d
                  AND ds.has_connection
                  AND ds.has_data
            )
        ) AS missing_optional
    FROM w
)
SELECT
    e.tenant_id,
    e.country_code,
    e.widget_code,
    e.tab,
    e.title,
    e.description,
    e.sort_order,
    e.required_domains,
    e.optional_domains,
    e.missing_required,
    e.missing_optional,
    e.awaiting_data,
    CASE
        WHEN cardinality(e.missing_required) > 0 THEN 'blocked'
        WHEN cardinality(e.awaiting_data)    > 0 THEN 'degraded'
        WHEN cardinality(e.missing_optional) > 0 THEN 'degraded'
        ELSE 'available'
    END                                                         AS state,
    CASE
        WHEN cardinality(e.missing_required) > 0 THEN e.blocked_message
        WHEN cardinality(e.awaiting_data)    > 0
            THEN 'Conexión activa pero aún sin datos. Sube un reporte o espera la próxima sincronización.'
        WHEN cardinality(e.missing_optional) > 0
            THEN 'Vista parcial: falta ' || array_to_string(e.missing_optional, ', ')
                 || '. Los números que ves son correctos, pero incompletos.'
        ELSE NULL
    END                                                         AS state_message
FROM evaluated e;

-- -----------------------------------------------------------------------------
-- Ingestion history, for the /ingest screen.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_batch_history AS
SELECT
    b.tenant_id,
    b.id                                                        AS batch_id,
    b.connection_id,
    c.name                                                      AS connection_name,
    c.country_code,
    c.platform_code,
    b.source_name,
    b.kind,
    b.status,
    b.rows_total,
    b.rows_inserted,
    b.rows_updated,
    b.rows_skipped,
    b.rows_failed,
    b.error,
    b.started_at,
    b.finished_at,
    round(EXTRACT(EPOCH FROM (b.finished_at - b.started_at))::numeric, 2) AS duration_seconds,
    (SELECT count(*) FROM raw.load_discrepancy d WHERE d.batch_id = b.id) AS discrepancy_count
FROM raw.load_batch b
JOIN core.connection c ON c.id = b.connection_id
WHERE b.tenant_id = core.current_tenant_id();
