-- =============================================================================
-- Data Effi - 049 - The carrier table said two things at once.
--
-- WHAT THE OPERATOR SAW. SERVIENTREGA: 1,138 guides, 99.1 % delivered, and a
-- contribution of -3,834. Three things were wrong with that row, and none of
-- them was the data:
--
--   * `delivery_rate_pct` divides by CLOSED guides (delivered + returned +
--     cancelled + lost), while the "Total ponderado" row the screen computed
--     divided by every guide dispatched. 99.1 % and 42.9 % in one table.
--   * `contribution` summed EVERY guide: an open guide has already paid its
--     freight and its product and collected nothing yet, so a carrier with
--     lots of recent volume reads as losing money. That is capital in the
--     street, not a loss (header of migration 015).
--
-- WHAT LANDS HERE. Five columns appended to `v_carrier_effectiveness` /
-- `f_carrier_effectiveness` (appended LAST: CREATE OR REPLACE VIEW cannot
-- reorder), both bases side by side so the screen can show the honest pair:
--
--   closed_shipments             guides already resolved (the old divisor)
--   delivery_rate_dispatched_pct delivered / every guide dispatched
--   return_rate_dispatched_pct   returned  / every guide dispatched
--   realised_contribution        revenue - freight - cogs - fee, CLOSED guides only
--   capital_in_street            freight + cogs + fee already paid on OPEN guides
--
-- The old columns keep their meaning (closed basis) for the callers that
-- compare carriers on matured guides. The view stays stand-alone (no f_* call
-- inside a view: header of 023); the parity test keeps both in step.
--
-- Depends on: 048. Idempotent.
-- =============================================================================

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
    min(e.currency_code)                                        AS currency_code,
    CASE WHEN count(*) FILTER (WHERE e.is_terminal) < 10
         THEN 'muestra_corta' ELSE 'suficiente' END             AS sample_quality,
    -- Appended by 049.
    count(*) FILTER (WHERE e.is_terminal)                       AS closed_shipments,
    round(count(*) FILTER (WHERE e.is_delivered)::numeric
        / NULLIF(count(*), 0) * 100, 2)                         AS delivery_rate_dispatched_pct,
    round(count(*) FILTER (WHERE e.is_returned)::numeric
        / NULLIF(count(*), 0) * 100, 2)                         AS return_rate_dispatched_pct,
    (sum(e.revenue_amount - e.freight_amount - e.cogs_amount - e.fee_amount)
        FILTER (WHERE e.is_terminal))::numeric(14, 2)           AS realised_contribution,
    (sum(e.freight_amount + e.cogs_amount + e.fee_amount)
        FILTER (WHERE NOT e.is_terminal))::numeric(14, 2)       AS capital_in_street
FROM stg.v_shipment_economics e
LEFT JOIN core.carrier c ON c.id = e.carrier_id
WHERE e.tenant_id = core.current_tenant_id()
GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;

DROP FUNCTION IF EXISTS mart.f_carrier_effectiveness(date, date, text, text);
CREATE FUNCTION mart.f_carrier_effectiveness(
    p_date_from  date DEFAULT NULL,
    p_date_to    date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion',
    p_platform   text DEFAULT NULL
)
RETURNS TABLE (
    tenant_id                    uuid,
    country_code                 char(2),
    carrier_id                   uuid,
    carrier_name                 text,
    first_shipment_date          date,
    last_shipment_date           date,
    shipments                    bigint,
    delivered                    bigint,
    returned                     bigint,
    in_transit                   bigint,
    delivery_rate_pct            numeric,
    return_rate_pct              numeric,
    avg_days_to_deliver          numeric,
    p90_days_to_deliver          numeric,
    freight_total                numeric,
    avg_freight_per_shipment     numeric,
    revenue                      numeric,
    contribution                 numeric,
    currency_code                char(3),
    sample_quality               text,
    closed_shipments             bigint,
    delivery_rate_dispatched_pct numeric,
    return_rate_dispatched_pct   numeric,
    realised_contribution        numeric,
    capital_in_street            numeric
)
LANGUAGE sql
STABLE
AS $fn$
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
        CASE WHEN count(*) FILTER (WHERE e.is_terminal) < 10
             THEN 'muestra_corta' ELSE 'suficiente' END,
        count(*) FILTER (WHERE e.is_terminal),
        round(count(*) FILTER (WHERE e.is_delivered)::numeric / NULLIF(count(*), 0) * 100, 2),
        round(count(*) FILTER (WHERE e.is_returned)::numeric / NULLIF(count(*), 0) * 100, 2),
        (sum(e.revenue_amount - e.freight_amount - e.cogs_amount - e.fee_amount)
            FILTER (WHERE e.is_terminal))::numeric(14, 2),
        (sum(e.freight_amount + e.cogs_amount + e.fee_amount)
            FILTER (WHERE NOT e.is_terminal))::numeric(14, 2)
    FROM stg.v_shipment_economics e
    LEFT JOIN core.carrier c ON c.id = e.carrier_id
    WHERE e.tenant_id = core.current_tenant_id()
      AND mart.f_platform_matches(e.connection_id, p_platform)
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;
$fn$;

COMMENT ON VIEW mart.v_carrier_effectiveness IS
    'Efectividad por transportadora. delivery_rate_pct / return_rate_pct dividen
     por guías cerradas; *_dispatched_pct por todas las despachadas.
     contribution suma todas las guías (incluye lo pagado en guías abiertas);
     realised_contribution solo las cerradas y capital_in_street lo que sigue
     en la calle.';
