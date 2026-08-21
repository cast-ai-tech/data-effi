-- =============================================================================
-- Data Effi - 026 - Preparation time measured a document, not a departure.
--
-- Two problems, one cause.
--
-- THE IMPOSSIBLE PERCENTAGE. `prep_share_pct` reached 106,9% for SERVIENTREGA
-- and 163,8% for GINTRACOM, on screen, today. A share of a total cannot exceed
-- the total. It happened because avg(prep_days) and avg(total_days) were taken
-- over DIFFERENT sets of guides - prep survives without a delivery, total does
-- not - so the ratio compared two populations, not two parts of one journey.
-- Both averages are now taken over the guides that have both numbers.
--
-- THE DISPATCH DATE IS ADMINISTRATIVE. `Fecha relación de despacho` is when
-- someone generated the dispatch document in Effi, in bulk and retroactively.
-- In the operator's export, 1,599 of 1,649 guides share a single afternoon, and
-- 451 of 666 guides reached their final state BEFORE their own "dispatch".
--
-- So the 6,30 days of preparation this project reported after migration 019 are
-- also not real. 019 fixed the column NAME - `dispatched_at` was holding the
-- ERP creation timestamp - and that fix stands. What it could not fix is that
-- the source has no physical departure date at all.
--
-- `dispatch_quality` says so per carrier instead of quietly publishing a
-- number. The figures still come through - a blank card explains nothing - but
-- the UI can now mark them as an artefact. `avg_total_days`, creation to
-- delivery, is unaffected and remains solid: 659 of 659 guides order correctly.
--
-- Depends on: 025. Idempotent.
-- =============================================================================

DROP VIEW IF EXISTS mart.v_fulfillment_sla;
DROP FUNCTION IF EXISTS mart.f_fulfillment_sla(date, date, text);

CREATE OR REPLACE FUNCTION mart.f_fulfillment_sla(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text)
 RETURNS TABLE(tenant_id uuid, country_code character, carrier_id uuid, carrier_name text, service_level text, shipments bigint, delivered bigint, avg_prep_days numeric, p50_prep_days numeric, p90_prep_days numeric, avg_transit_days numeric, p90_transit_days numeric, avg_total_days numeric, prep_share_pct numeric, on_time_count bigint, measurable_count bigint, on_time_pct numeric, dispatch_quality text)
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

        round(
            avg(b.prep_days)  FILTER (WHERE b.total_days IS NOT NULL)
            / NULLIF(avg(b.total_days) FILTER (WHERE b.prep_days IS NOT NULL), 0)
            * 100, 1),

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
$function$;

CREATE VIEW mart.v_fulfillment_sla AS
    SELECT * FROM mart.f_fulfillment_sla(NULL, NULL, 'creacion');

GRANT SELECT ON mart.v_fulfillment_sla TO norte_app, norte_readonly;
