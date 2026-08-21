-- =============================================================================
-- Data Effi - 021 - A carrier average built on one guide is not an average.
--
-- `mart.v_geo_performance` has had a minimum-sample rule since 003: below ten
-- terminal guides a city reports 'sin_datos' rather than a delivery rate,
-- because one delivered guide out of one is not a 100% success rate.
--
-- The carrier table never got that rule. Over the full history it did not
-- matter - every carrier had hundreds of guides. The date filter is what
-- changed the stakes: inside a narrow window a carrier can have two terminal
-- guides, and the screen prints "50,0% de entrega" beside carriers measured
-- over hundreds.
--
-- That invites the wrong decision. An operator comparing carriers reads the
-- small number as bad performance and moves volume away from a carrier that was
-- never really measured.
--
-- The percentages are still returned - a blank row explains nothing - but
-- `sample_quality` travels with them so the UI can mark them as an estimate.
-- Appended LAST, because Postgres refuses to reorder a view's columns and this
-- project has been bitten by that twice.
--
-- Depends on: 020. Idempotent.
-- =============================================================================

DROP VIEW IF EXISTS mart.v_carrier_effectiveness;
DROP FUNCTION IF EXISTS mart.f_carrier_effectiveness(date, date, text);

CREATE OR REPLACE FUNCTION mart.f_carrier_effectiveness(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text)
 RETURNS TABLE(tenant_id uuid, country_code character, carrier_id uuid, carrier_name text, first_shipment_date date, last_shipment_date date, shipments bigint, delivered bigint, returned bigint, in_transit bigint, delivery_rate_pct numeric, return_rate_pct numeric, avg_days_to_deliver numeric, p90_days_to_deliver numeric, freight_total numeric, avg_freight_per_shipment numeric, revenue numeric, contribution numeric, currency_code character, sample_quality text)
 LANGUAGE sql
 STABLE
AS $function$
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
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;
$function$;

COMMENT ON FUNCTION mart.f_carrier_effectiveness(date, date, text) IS
    'Efectividad por transportadora sobre el rango. sample_quality =
     muestra_corta cuando hay menos de 10 guías terminales: los porcentajes se
     devuelven igual, pero no deben presentarse como medidos.';

CREATE VIEW mart.v_carrier_effectiveness AS
    SELECT * FROM mart.f_carrier_effectiveness(NULL, NULL, 'creacion');

GRANT SELECT ON mart.v_carrier_effectiveness TO norte_app, norte_readonly;
