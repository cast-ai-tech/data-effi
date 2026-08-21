-- =============================================================================
-- Data Effi - 029 - "0,00% a tiempo" measured a promise nobody made.
--
-- `on_time_pct` compares the delivery date against `Fecha de entrega esperada`.
-- In the operator's export that field is `created_date + 1` on 1,353 of 1,649
-- guides (82%): Effi's default, filled in automatically, never negotiated with
-- a carrier. Real deliveries average 4,71 days.
--
-- So the dashboard showed SERVIENTREGA at 0,00% on time - 0 of 448. Read
-- plainly that says the carrier has never once delivered on schedule, and it is
-- a reason to replace them. It measures nothing but the gap between a
-- placeholder and reality.
--
-- Same treatment as dispatch_quality in 026: withhold the figure rather than
-- publish it, and let `promise_quality` say why. The day an operator loads real
-- promised dates, the percentage comes back on its own.
--
-- The view body is generated from the function body with the range predicates
-- removed, so the two cannot drift - see 028 for what happens when they do.
--
-- Depends on: 028. Idempotent.
-- =============================================================================

DROP VIEW IF EXISTS mart.v_fulfillment_sla;
DROP FUNCTION IF EXISTS mart.f_fulfillment_sla(date, date, text);

CREATE OR REPLACE FUNCTION mart.f_fulfillment_sla(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text)
 RETURNS TABLE(tenant_id uuid, country_code character, carrier_id uuid, carrier_name text, service_level text, shipments bigint, delivered bigint, avg_prep_days numeric, p50_prep_days numeric, p90_prep_days numeric, avg_transit_days numeric, p90_transit_days numeric, avg_total_days numeric, prep_share_pct numeric, on_time_count bigint, measurable_count bigint, on_time_pct numeric, dispatch_quality text, promise_quality text)
 LANGUAGE sql
 STABLE
AS $function$
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
            -- An expected date exactly one day after creation is Effi's default,
            -- not a commitment anyone negotiated with a carrier.
            (s.expected_delivery_date = s.created_date + 1)             AS promise_is_default,
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
          AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) >= p_date_from)
          AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) <= p_date_to)
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
        CASE WHEN count(*) FILTER (WHERE b.promise_is_default) > 0.5 * NULLIF(count(*), 0)
             THEN NULL
             ELSE round(count(*) FILTER (WHERE b.on_time IS TRUE)::numeric
                  / NULLIF(count(*) FILTER (WHERE b.on_time IS NOT NULL), 0) * 100, 2) END,
        -- Flag, not a filter: the numbers still come through so the card is not
        -- blank, but the UI must not present preparation time as measured when
        -- the dispatch stamp is administrative.
        CASE WHEN count(*) FILTER (WHERE b.delivered_before_dispatch)
                  > 0.05 * NULLIF(count(*) FILTER (WHERE b.is_delivered), 0)
             THEN 'administrativo' ELSE 'medido' END,
        -- Same idea as dispatch_quality: name the promise as automatic instead
        -- of publishing a compliance figure measured against a placeholder.
        CASE WHEN count(*) FILTER (WHERE b.promise_is_default) > 0.5 * NULLIF(count(*), 0)
             THEN 'automatica' ELSE 'acordada' END
    FROM base b
    GROUP BY b.tenant_id, b.country_code, b.carrier_id, b.carrier_name, b.service_level;
$function$;

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
    dispatch_quality,
    promise_quality
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
            -- An expected date exactly one day after creation is Effi's default,
            -- not a commitment anyone negotiated with a carrier.
            (s.expected_delivery_date = s.created_date + 1)             AS promise_is_default,
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
        CASE WHEN count(*) FILTER (WHERE b.promise_is_default) > 0.5 * NULLIF(count(*), 0)
             THEN NULL
             ELSE round(count(*) FILTER (WHERE b.on_time IS TRUE)::numeric
                  / NULLIF(count(*) FILTER (WHERE b.on_time IS NOT NULL), 0) * 100, 2) END,
        -- Flag, not a filter: the numbers still come through so the card is not
        -- blank, but the UI must not present preparation time as measured when
        -- the dispatch stamp is administrative.
        CASE WHEN count(*) FILTER (WHERE b.delivered_before_dispatch)
                  > 0.05 * NULLIF(count(*) FILTER (WHERE b.is_delivered), 0)
             THEN 'administrativo' ELSE 'medido' END,
        -- Same idea as dispatch_quality: name the promise as automatic instead
        -- of publishing a compliance figure measured against a placeholder.
        CASE WHEN count(*) FILTER (WHERE b.promise_is_default) > 0.5 * NULLIF(count(*), 0)
             THEN 'automatica' ELSE 'acordada' END
    FROM base b
    GROUP BY b.tenant_id, b.country_code, b.carrier_id, b.carrier_name, b.service_level;

GRANT SELECT ON mart.v_fulfillment_sla TO norte_app, norte_readonly;
