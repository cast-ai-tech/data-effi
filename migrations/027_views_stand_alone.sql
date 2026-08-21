-- =============================================================================
-- Data Effi - 027 - Views must carry their own body.
--
-- Migrations 021 and 026 defined `mart.v_carrier_effectiveness` and
-- `mart.v_fulfillment_sla` as `SELECT * FROM mart.f_...()`. That looked like
-- healthy reuse and quietly broke the security model: a view whose body calls a
-- function stops executing as its owner, so `norte_readonly` - which cannot read
-- `stg` - loses the view entirely. Migration 018 avoided this on purpose and
-- left every view standing alone; a test now enforces it.
--
-- Also fixed here: `prep_share_pct` is NULL when the dispatch stamp is
-- administrative. Restricting both averages to the same guides (026) was
-- necessary but not sufficient - preparation genuinely exceeds the total
-- journey when the "dispatch" is recorded after the delivery, so the ratio was
-- still above 100%. A share of a journey that cannot be computed is absent, not
-- 121,8%.
--
-- Depends on: 026. Idempotent.
-- =============================================================================

DROP VIEW IF EXISTS mart.v_carrier_effectiveness;
CREATE VIEW mart.v_carrier_effectiveness (
    tenant_id,
    country_code,
    carrier_id,
    carrier_name,
    first_shipment_date,
    last_shipment_date,
    shipments,
    delivered,
    returned,
    in_transit,
    delivery_rate_pct,
    return_rate_pct,
    avg_days_to_deliver,
    p90_days_to_deliver,
    freight_total,
    avg_freight_per_shipment,
    revenue,
    contribution,
    currency_code,
    sample_quality
) AS
SELECT
        e.tenant_id,
        e.country_code,
        e.carrier_id,
        COALESCE(c.name, 'Sin transportadora'),
        min(e.created_date),
        max(e.created_date),
        count(*),
        count(*) FILTER (WHERE e.is_delivered),
        count(*) FILTER (WHERE e.is_returned),
        count(*) FILTER (WHERE NOT e.is_terminal),
        round(count(*) FILTER (WHERE e.is_delivered)::numeric
            / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) * 100, 2),
        round(count(*) FILTER (WHERE e.is_returned)::numeric
            / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) * 100, 2),
        round(avg(e.days_to_deliver)::numeric, 1),
        percentile_cont(0.9) WITHIN GROUP (ORDER BY e.days_to_deliver)::numeric(6, 1),
        sum(e.freight_amount)::numeric(14, 2),
        round(sum(e.freight_amount) / NULLIF(count(*), 0), 2),
        sum(e.revenue_amount)::numeric(14, 2),
        (sum(e.revenue_amount) - sum(e.freight_amount) - sum(e.cogs_amount)
            - sum(e.fee_amount))::numeric(14, 2),
        min(e.currency_code),
        -- The rates are still returned: a blank row with no explanation is
        -- worse. The flag is what lets the UI mark them as an estimate rather
        -- than present them as measured.
        CASE WHEN count(*) FILTER (WHERE e.is_terminal) < 10
             THEN 'muestra_corta' ELSE 'suficiente' END
    FROM stg.v_shipment_economics e
    LEFT JOIN core.carrier c ON c.id = e.carrier_id
    WHERE e.tenant_id = core.current_tenant_id()
    GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;

GRANT SELECT ON mart.v_carrier_effectiveness TO norte_app, norte_readonly;

DROP VIEW IF EXISTS mart.v_fulfillment_sla;
CREATE VIEW mart.v_fulfillment_sla (
    tenant_id,
    country_code,
    carrier_id,
    carrier_name,
    service_level,
    shipments,
    delivered,
    avg_prep_days,
    p50_prep_days,
    p90_prep_days,
    avg_transit_days,
    p90_transit_days,
    avg_total_days,
    prep_share_pct,
    on_time_count,
    measurable_count,
    on_time_pct,
    dispatch_quality
) AS
WITH base AS (
        SELECT
            s.tenant_id,
            s.country_code,
            s.carrier_id,
            COALESCE(c.name, 'Sin transportadora')                  AS carrier_name,
            s.service_level,
            CASE WHEN s.dispatched_batch_at IS NOT NULL
                  AND s.dispatched_batch_at::date >= s.created_date
                 THEN (s.dispatched_batch_at::date - s.created_date) END AS prep_days,
            CASE WHEN s.delivered_at IS NOT NULL AND s.dispatched_batch_at IS NOT NULL
                  AND s.delivered_at >= s.dispatched_batch_at
                 THEN (s.delivered_at::date - s.dispatched_batch_at::date) END AS transit_days,
            CASE WHEN s.delivered_at IS NOT NULL
                 THEN (s.delivered_at::date - s.created_date) END     AS total_days,
            CASE WHEN s.delivered_at IS NOT NULL AND s.expected_delivery_date IS NOT NULL
                 THEN s.delivered_at::date <= s.expected_delivery_date END AS on_time,
            -- A parcel cannot arrive before it leaves. When this is true for a
            -- large share of a carrier's guides, the "dispatch" timestamp is a
            -- document someone generated afterwards, not a departure.
            (s.delivered_at IS NOT NULL AND s.dispatched_batch_at IS NOT NULL
             AND s.delivered_at < s.dispatched_batch_at)                AS delivered_before_dispatch,
            sc.is_delivered
        FROM core.shipment s
        JOIN core.status_canon sc ON sc.code = s.status_code
        LEFT JOIN core.carrier c ON c.id = s.carrier_id
        WHERE s.tenant_id = core.current_tenant_id()
    )
    SELECT
        b.tenant_id,
        b.country_code,
        b.carrier_id,
        b.carrier_name,
        COALESCE(b.service_level, 'Sin servicio'),
        count(*),
        count(*) FILTER (WHERE b.is_delivered),

        round(avg(b.prep_days)::numeric, 1),
        percentile_cont(0.5) WITHIN GROUP (ORDER BY b.prep_days)::numeric(6, 1),
        percentile_cont(0.9) WITHIN GROUP (ORDER BY b.prep_days)::numeric(6, 1),

        round(avg(b.transit_days)::numeric, 1),
        percentile_cont(0.9) WITHIN GROUP (ORDER BY b.transit_days)::numeric(6, 1),
        round(avg(b.total_days)::numeric, 1),

        CASE WHEN count(*) FILTER (WHERE b.delivered_before_dispatch)
                  > 0.05 * NULLIF(count(*) FILTER (WHERE b.is_delivered), 0)
             THEN NULL
             ELSE round(
                 avg(b.prep_days)  FILTER (WHERE b.total_days IS NOT NULL)
                 / NULLIF(avg(b.total_days) FILTER (WHERE b.prep_days IS NOT NULL), 0)
                 * 100, 1) END,

        count(*) FILTER (WHERE b.on_time IS TRUE),
        count(*) FILTER (WHERE b.on_time IS NOT NULL),
        round(count(*) FILTER (WHERE b.on_time IS TRUE)::numeric
              / NULLIF(count(*) FILTER (WHERE b.on_time IS NOT NULL), 0) * 100, 2),
        -- Flag, not a filter: the numbers still come through so the card is not
        -- blank, but the UI must not present preparation time as measured when
        -- the dispatch stamp is administrative.
        CASE WHEN count(*) FILTER (WHERE b.delivered_before_dispatch)
                  > 0.05 * NULLIF(count(*) FILTER (WHERE b.is_delivered), 0)
             THEN 'administrativo' ELSE 'medido' END
    FROM base b
    GROUP BY b.tenant_id, b.country_code, b.carrier_id, b.carrier_name, b.service_level;

GRANT SELECT ON mart.v_fulfillment_sla TO norte_app, norte_readonly;
