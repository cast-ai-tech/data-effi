-- =============================================================================
-- Data Effi - 041 - Every range-aware function learns to filter by platform.
--
-- GENERATED, NOT HAND-EDITED. Each body below is the exact definition that was
-- live after migration 039 (read back with pg_get_functiondef), with exactly
-- two changes applied mechanically:
--
--   1. a fourth argument, `p_platform text DEFAULT NULL`, appended to the
--      signature. NULL means "todas las plataformas", so every existing call
--      with three arguments keeps meaning what it meant;
--   2. one predicate, `AND mart.f_platform_matches(<guide>.connection_id,
--      p_platform)`, added right after the tenant clause of the guide scan.
--
-- Nothing else moved. Reading a diff of this file against 018-028 shows only
-- those two lines per function - which is the point of generating it: the
-- thirteen bodies stay byte-identical to what the parity tests already check.
--
-- WHY A PARAMETER AND NOT A SESSION SETTING. A GUC read inside a row-level
-- policy would filter transparently, and silently: a pool connection that
-- forgot to clear it would show Effi's numbers under a header that says
-- "todas". A parameter has to be passed, and the API echoes back which one it
-- passed (`platform` on every KpiResponse), so the interface can say when a
-- card did NOT separate by platform instead of letting the reader assume.
--
-- THE ONE PLACE THE FILTER IS INCOMPLETE, NAMED. `f_global_summary` subtracts
-- ad spend. Ad spend belongs to an ads connection (Meta, TikTok), never to
-- Effi or Dropi, so under a platform filter there is no honest number to
-- subtract: the ads CTE returns nothing and `contribution` is contribution
-- BEFORE media. The API documents this on the endpoint.
--
-- THE THREE-ARGUMENT VERSIONS ARE DROPPED FIRST. A default on the new fourth
-- argument would make `f_x(a, b, c)` ambiguous between the old and the new
-- function, exactly as migration 020 did when it added the third one.
--
-- `f_customer_metrics` is deliberately not here: it reads mart.v_orders, which
-- carries no connection, and the customers screen is not a KPI card.
--
-- Depends on: 040. Idempotent.
-- =============================================================================
-- ---------------------------------------------------------------------------
-- f_aging
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_aging(date, date, text);
DROP FUNCTION IF EXISTS mart.f_aging(date, date, text, text);
CREATE FUNCTION mart.f_aging(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
 RETURNS TABLE(tenant_id uuid, country_code character, aging_bucket text, bucket_order integer, shipments bigint, value_at_risk numeric, avg_days_open numeric, currency_code character)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT
        e.tenant_id,
        e.country_code,
        CASE
            WHEN e.days_open <= 3  THEN '0-3'
            WHEN e.days_open <= 7  THEN '4-7'
            WHEN e.days_open <= 12 THEN '8-12'
            WHEN e.days_open <= 20 THEN '13-20'
            ELSE '21+'
        END,
        CASE
            WHEN e.days_open <= 3  THEN 1
            WHEN e.days_open <= 7  THEN 2
            WHEN e.days_open <= 12 THEN 3
            WHEN e.days_open <= 20 THEN 4
            ELSE 5
        END,
        count(*),
        sum(e.declared_value)::numeric(14, 2),
        round(avg(e.days_open)::numeric, 1),
        min(e.currency_code)
    FROM stg.v_shipment_economics e
    WHERE e.tenant_id = core.current_tenant_id()
      AND mart.f_platform_matches(e.connection_id, p_platform)
      AND NOT e.is_terminal
      AND e.days_open IS NOT NULL
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, 3, 4;
$function$;

-- ---------------------------------------------------------------------------
-- f_carrier_effectiveness
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_carrier_effectiveness(date, date, text);
DROP FUNCTION IF EXISTS mart.f_carrier_effectiveness(date, date, text, text);
CREATE FUNCTION mart.f_carrier_effectiveness(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
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
      AND mart.f_platform_matches(e.connection_id, p_platform)
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;
$function$;

-- ---------------------------------------------------------------------------
-- f_cash_cycle
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_cash_cycle(date, date, text);
DROP FUNCTION IF EXISTS mart.f_cash_cycle(date, date, text, text);
CREATE FUNCTION mart.f_cash_cycle(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
 RETURNS TABLE(tenant_id uuid, country_code character, settled bigint, delivered_unsettled bigint, avg_days_to_cash numeric, p50_days_to_cash numeric, p90_days_to_cash numeric, cash_in_transit numeric, currency_code character)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT
        e.tenant_id,
        e.country_code,
        count(*) FILTER (WHERE e.settled_at IS NOT NULL),
        count(*) FILTER (WHERE e.is_delivered AND e.settled_at IS NULL),
        round(avg(e.days_to_cash)::numeric, 1),
        percentile_cont(0.5) WITHIN GROUP (ORDER BY e.days_to_cash)::numeric(6, 1),
        percentile_cont(0.9) WITHIN GROUP (ORDER BY e.days_to_cash)::numeric(6, 1),
        sum(e.revenue_amount) FILTER (WHERE e.is_delivered AND e.settled_at IS NULL)
            ::numeric(14, 2),
        min(e.currency_code)
    FROM stg.v_shipment_economics e
    WHERE e.tenant_id = core.current_tenant_id()
      AND mart.f_platform_matches(e.connection_id, p_platform)
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code;
$function$;

-- ---------------------------------------------------------------------------
-- f_contribution_split
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_contribution_split(date, date, text);
DROP FUNCTION IF EXISTS mart.f_contribution_split(date, date, text, text);
CREATE FUNCTION mart.f_contribution_split(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
 RETURNS TABLE(country_code character, currency_code character, shipments bigint, closed_shipments bigint, open_shipments bigint, realised_revenue numeric, realised_cost numeric, realised_contribution numeric, realised_margin_pct numeric, capital_in_street numeric, committed_revenue numeric, net_contribution numeric, maturity_pct numeric)
 LANGUAGE sql
 STABLE
AS $function$
    WITH e AS (
        SELECT
            s.country_code,
            s.is_terminal,
            s.is_delivered,
            s.is_returned,
            s.revenue_amount,
            s.freight_amount,
            s.cogs_amount,
            s.fee_amount
        FROM stg.v_shipment_economics s
        WHERE s.tenant_id = core.current_tenant_id()
          AND mart.f_platform_matches(s.connection_id, p_platform)
          AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) >= p_date_from)
          AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) <= p_date_to)
    )
    SELECT
        e.country_code,
        co.currency_code,

        count(*)                                                AS shipments,
        count(*) FILTER (WHERE e.is_terminal)                   AS closed_shipments,
        count(*) FILTER (WHERE NOT e.is_terminal)               AS open_shipments,

        sum(e.revenue_amount)  FILTER (WHERE e.is_terminal)::numeric(14,2),
        sum(e.freight_amount + e.cogs_amount + e.fee_amount)
            FILTER (WHERE e.is_terminal)::numeric(14,2),
        sum(e.revenue_amount - e.freight_amount - e.cogs_amount - e.fee_amount)
            FILTER (WHERE e.is_terminal)::numeric(14,2),
        round(
            sum(e.revenue_amount - e.freight_amount - e.cogs_amount - e.fee_amount)
                FILTER (WHERE e.is_terminal)
            / NULLIF(sum(e.revenue_amount) FILTER (WHERE e.is_terminal), 0) * 100, 2
        ),

        sum(e.freight_amount + e.cogs_amount + e.fee_amount)
            FILTER (WHERE NOT e.is_terminal)::numeric(14,2),
        sum(e.revenue_amount)
            FILTER (WHERE NOT e.is_terminal)::numeric(14,2),

        sum(e.revenue_amount - e.freight_amount - e.cogs_amount - e.fee_amount)
            ::numeric(14,2),

        round(count(*) FILTER (WHERE e.is_terminal)::numeric
              / NULLIF(count(*), 0) * 100, 1)
    FROM e
    JOIN core.country co ON co.code = e.country_code
    GROUP BY e.country_code, co.currency_code;
$function$;

-- ---------------------------------------------------------------------------
-- f_dropshipping_margin
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_dropshipping_margin(date, date, text);
DROP FUNCTION IF EXISTS mart.f_dropshipping_margin(date, date, text, text);
CREATE FUNCTION mart.f_dropshipping_margin(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
 RETURNS TABLE(tenant_id uuid, country_code character, product_id uuid, product_name text, sku text, supplier_name text, shipments bigint, delivered bigint, units bigint, revenue numeric, supplier_cost numeric, freight numeric, gross_margin numeric, gross_margin_pct numeric, net_contribution numeric, contribution_per_shipment numeric, cost_of_undelivered numeric, breakeven_delivery_pct numeric, delivery_rate_pct numeric, catalogue_cost numeric, catalogue_price numeric, catalogue_reviewed boolean, observed_unit_cost numeric, currency_code character)
 LANGUAGE sql
 STABLE
AS $function$
    WITH per_product AS (
        SELECT
            s.tenant_id,
            s.country_code,
            s.product_id,
            COALESCE(p.name, 'Sin producto')                        AS product_name,
            p.sku,
            COALESCE(sup.name, s.distributor_name, 'Sin proveedor') AS supplier_name,
            p.unit_cost                                             AS catalogue_cost,
            p.list_price                                            AS catalogue_price,
            p.reviewed_at IS NOT NULL                               AS catalogue_reviewed,
            count(*)                                                AS shipments,
            count(*) FILTER (WHERE sc.is_delivered)                 AS delivered,
            sum(s.quantity)                                         AS units,
            sum(COALESCE(s.distributor_sale_total, s.declared_value))
                FILTER (WHERE sc.is_delivered)                      AS revenue,
            sum(COALESCE(s.distributor_cost_total, s.product_cost)) AS supplier_cost,
            sum(COALESCE(s.distributor_cost_total, s.product_cost))
                FILTER (WHERE sc.is_delivered)                      AS supplier_cost_delivered,
            sum(COALESCE(s.freight_cost, 0) + COALESCE(s.return_freight_cost, 0)) AS freight,
            min(s.currency_code)                                    AS currency_code
        FROM core.shipment s
        JOIN core.status_canon sc ON sc.code = s.status_code
        LEFT JOIN core.product p ON p.id = s.product_id
        LEFT JOIN core.supplier sup ON sup.id = p.supplier_id
        WHERE s.tenant_id = core.current_tenant_id()
          AND mart.f_platform_matches(s.connection_id, p_platform)
          AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) >= p_date_from)
          AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) <= p_date_to)
        GROUP BY s.tenant_id, s.country_code, s.product_id, p.name, p.sku,
                 sup.name, s.distributor_name, p.unit_cost, p.list_price, p.reviewed_at
    )
    SELECT
        pp.tenant_id,
        pp.country_code,
        pp.product_id,
        pp.product_name,
        pp.sku,
        pp.supplier_name,
        pp.shipments,
        pp.delivered,
        pp.units,
        pp.revenue::numeric(14, 2),
        pp.supplier_cost::numeric(14, 2),
        pp.freight::numeric(14, 2),

        (pp.revenue - COALESCE(pp.supplier_cost_delivered, 0))::numeric(14, 2),
        round((pp.revenue - COALESCE(pp.supplier_cost_delivered, 0))
              / NULLIF(pp.revenue, 0) * 100, 2),

        (pp.revenue - COALESCE(pp.supplier_cost, 0) - COALESCE(pp.freight, 0))::numeric(14, 2),
        round((pp.revenue - COALESCE(pp.supplier_cost, 0) - COALESCE(pp.freight, 0))
              / NULLIF(pp.shipments, 0), 2),

        (COALESCE(pp.supplier_cost, 0) - COALESCE(pp.supplier_cost_delivered, 0))::numeric(14, 2),

        round(
            (COALESCE(pp.supplier_cost, 0) + COALESCE(pp.freight, 0))
            / NULLIF(pp.revenue / NULLIF(pp.delivered, 0) * pp.shipments, 0) * 100, 2
        ),
        round(pp.delivered::numeric / NULLIF(pp.shipments, 0) * 100, 2),

        pp.catalogue_cost,
        pp.catalogue_price,
        pp.catalogue_reviewed,
        round(COALESCE(pp.supplier_cost, 0) / NULLIF(pp.units, 0), 2),
        pp.currency_code
    FROM per_product pp;
$function$;

-- ---------------------------------------------------------------------------
-- f_freight_analysis
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_freight_analysis(date, date, text);
DROP FUNCTION IF EXISTS mart.f_freight_analysis(date, date, text, text);
CREATE FUNCTION mart.f_freight_analysis(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
 RETURNS TABLE(tenant_id uuid, country_code character, carrier_id uuid, carrier_name text, service_level text, shipments bigint, avg_weight_kg numeric, total_weight_kg numeric, freight_total numeric, avg_freight numeric, freight_per_kg numeric, avg_freight_base numeric, avg_handling numeric, avg_collection_fee numeric, avg_discount_pct numeric, discount_value numeric, freight_share_of_value_pct numeric, return_freight_total numeric, currency_code character)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT
        s.tenant_id,
        s.country_code,
        s.carrier_id,
        COALESCE(c.name, 'Sin transportadora'),
        COALESCE(s.service_level, 'Sin servicio'),
        count(*),
        round(avg(s.weight_kg)::numeric, 2),
        sum(s.weight_kg)::numeric(12, 2),

        sum(s.freight_cost)::numeric(14, 2),
        round(avg(s.freight_cost)::numeric, 2),
        round(sum(s.freight_cost) / NULLIF(sum(s.weight_kg), 0), 2),

        round(avg(s.freight_base)::numeric, 2),
        round(avg(s.insurance_cost)::numeric, 2),
        round(avg(s.collection_fee)::numeric, 2),
        round(avg(s.discount_pct)::numeric * 100, 1),
        round(sum(s.freight_cost * s.discount_pct
                  / NULLIF(1 - s.discount_pct, 0))::numeric, 2),

        round(sum(s.freight_cost) / NULLIF(sum(s.declared_value), 0) * 100, 2),
        sum(s.return_freight_cost)::numeric(14, 2),
        min(s.currency_code)
    FROM core.shipment s
    LEFT JOIN core.carrier c ON c.id = s.carrier_id
    WHERE s.tenant_id = core.current_tenant_id()
      AND mart.f_platform_matches(s.connection_id, p_platform)
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) <= p_date_to)
    GROUP BY s.tenant_id, s.country_code, s.carrier_id, c.name, s.service_level;
$function$;

-- ---------------------------------------------------------------------------
-- f_fulfillment_sla
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_fulfillment_sla(date, date, text);
DROP FUNCTION IF EXISTS mart.f_fulfillment_sla(date, date, text, text);
CREATE FUNCTION mart.f_fulfillment_sla(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
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
          AND mart.f_platform_matches(s.connection_id, p_platform)
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

-- ---------------------------------------------------------------------------
-- f_geo_performance
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_geo_performance(date, date, text);
DROP FUNCTION IF EXISTS mart.f_geo_performance(date, date, text, text);
CREATE FUNCTION mart.f_geo_performance(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
 RETURNS TABLE(tenant_id uuid, country_code character, geo_id uuid, level1_name text, city_name text, city_normalized text, shipments bigint, delivered bigint, returned bigint, in_transit bigint, delivery_rate_pct numeric, revenue numeric, contribution numeric, avg_days_to_deliver numeric, traffic_light text, currency_code character)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT
        e.tenant_id,
        e.country_code,
        e.geo_id,
        COALESCE(g.level1_name, 'Sin dato'),
        COALESCE(g.city_name, 'Sin dato'),
        g.city_normalized,
        count(*),
        count(*) FILTER (WHERE e.is_delivered),
        count(*) FILTER (WHERE e.is_returned),
        count(*) FILTER (WHERE NOT e.is_terminal),
        round(count(*) FILTER (WHERE e.is_delivered)::numeric
            / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) * 100, 2),
        sum(e.revenue_amount)::numeric(14, 2),
        (sum(e.revenue_amount) - sum(e.freight_amount) - sum(e.cogs_amount)
            - sum(e.fee_amount))::numeric(14, 2),
        round(avg(e.days_to_deliver)::numeric, 1),
        CASE
            WHEN count(*) FILTER (WHERE e.is_terminal) < 10 THEN 'sin_datos'
            WHEN count(*) FILTER (WHERE e.is_delivered)::numeric
                 / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) >= 0.80 THEN 'verde'
            WHEN count(*) FILTER (WHERE e.is_delivered)::numeric
                 / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) >= 0.65 THEN 'amarillo'
            ELSE 'rojo'
        END,
        min(e.currency_code)
    FROM stg.v_shipment_economics e
    LEFT JOIN core.geo g ON g.id = e.geo_id
    WHERE e.tenant_id = core.current_tenant_id()
      AND mart.f_platform_matches(e.connection_id, p_platform)
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.geo_id,
             g.level1_name, g.city_name, g.city_normalized;
$function$;

-- ---------------------------------------------------------------------------
-- f_global_summary
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_global_summary(date, date, text);
DROP FUNCTION IF EXISTS mart.f_global_summary(date, date, text, text);
CREATE FUNCTION mart.f_global_summary(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
 RETURNS TABLE(tenant_id uuid, country_code character, country_name text, currency_code character, shipments bigint, delivered bigint, returned bigint, in_transit bigint, delivery_rate_pct numeric, revenue numeric, ad_spend numeric, contribution numeric, fx_rate_to_usd numeric, fx_rate_date date, contribution_usd numeric, fx_missing boolean, last_shipment_date date)
 LANGUAGE sql
 STABLE
AS $function$
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
          AND mart.f_platform_matches(e.connection_id, p_platform)
          AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
          AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
        GROUP BY e.tenant_id, e.country_code
    ),
    ads AS (
        SELECT a.tenant_id, a.country_code, sum(a.spend) AS ad_spend
        FROM core.ad_spend a
        WHERE a.tenant_id = core.current_tenant_id()
          -- Ad spend has no platform of ours: nothing to subtract under a filter.
          AND p_platform IS NULL
          AND (p_date_from IS NULL OR a.spend_date >= p_date_from)
          AND (p_date_to   IS NULL OR a.spend_date <= p_date_to)
        GROUP BY a.tenant_id, a.country_code
    ),
    latest_fx AS (
        SELECT DISTINCT ON (fr.base_currency, fr.quote_currency)
            fr.base_currency, fr.quote_currency, fr.rate, fr.rate_date
        FROM core.fx_rate fr
        WHERE fr.quote_currency = 'USD'
        ORDER BY fr.base_currency, fr.quote_currency, fr.rate_date DESC
    )
    SELECT
        pc.tenant_id,
        pc.country_code,
        co.name,
        pc.currency_code,
        pc.shipments,
        pc.delivered,
        pc.returned,
        pc.in_transit,
        round(pc.delivered::numeric / NULLIF(pc.delivered + pc.returned, 0) * 100, 2),
        pc.revenue::numeric(14, 2),
        COALESCE(a.ad_spend, 0)::numeric(14, 2),
        (pc.contribution - COALESCE(a.ad_spend, 0))::numeric(14, 2),
        fx.rate,
        fx.rate_date,
        CASE WHEN fx.rate IS NOT NULL
             THEN round((pc.contribution - COALESCE(a.ad_spend, 0)) * fx.rate, 2) END,
        (fx.rate IS NULL),
        pc.last_shipment_date
    FROM per_country pc
    JOIN core.country co ON co.code = pc.country_code
    LEFT JOIN ads a ON a.tenant_id = pc.tenant_id AND a.country_code = pc.country_code
    LEFT JOIN latest_fx fx ON fx.base_currency = pc.currency_code;
$function$;

-- ---------------------------------------------------------------------------
-- f_office_rescue
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_office_rescue(date, date, text);
DROP FUNCTION IF EXISTS mart.f_office_rescue(date, date, text, text);
CREATE FUNCTION mart.f_office_rescue(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
 RETURNS TABLE(tenant_id uuid, country_code character, carrier_name text, level1_name text, city_name text, shipments bigint, value_waiting numeric, avg_days_waiting numeric, fresh_0_7 bigint, aging_8_14 bigint, urgent_15_21 bigint, probably_lost bigint, value_still_recoverable numeric, currency_code character)
 LANGUAGE sql
 STABLE
AS $function$
    WITH in_office AS (
        SELECT
            s.tenant_id,
            s.country_code,
            s.geo_id,
            COALESCE(g.level1_name, 'Sin dato')                     AS level1_name,
            COALESCE(g.city_name, 'Sin dato')                       AS city_name,
            s.carrier_id,
            COALESCE(c.name, 'Sin transportadora')                  AS carrier_name,
            (CURRENT_DATE - s.created_date)                         AS days_waiting,
            s.declared_value,
            s.currency_code
        FROM core.shipment s
        LEFT JOIN core.geo g ON g.id = s.geo_id
        LEFT JOIN core.carrier c ON c.id = s.carrier_id
        WHERE s.tenant_id = core.current_tenant_id()
          AND mart.f_platform_matches(s.connection_id, p_platform)
          AND s.status_code = 'in_office'
          AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) >= p_date_from)
          AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) <= p_date_to)
    )
    SELECT
        o.tenant_id,
        o.country_code,
        o.carrier_name,
        o.level1_name,
        o.city_name,
        count(*),
        sum(o.declared_value)::numeric(14, 2),
        round(avg(o.days_waiting)::numeric, 1),
        count(*) FILTER (WHERE o.days_waiting <= 7),
        count(*) FILTER (WHERE o.days_waiting BETWEEN 8 AND 14),
        count(*) FILTER (WHERE o.days_waiting BETWEEN 15 AND 21),
        count(*) FILTER (WHERE o.days_waiting > 21),
        sum(o.declared_value) FILTER (WHERE o.days_waiting BETWEEN 8 AND 21)::numeric(14, 2),
        min(o.currency_code)
    FROM in_office o
    GROUP BY o.tenant_id, o.country_code, o.carrier_name, o.level1_name, o.city_name;
$function$;

-- ---------------------------------------------------------------------------
-- f_problem_rate
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_problem_rate(date, date, text);
DROP FUNCTION IF EXISTS mart.f_problem_rate(date, date, text, text);
CREATE FUNCTION mart.f_problem_rate(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
 RETURNS TABLE(tenant_id uuid, country_code character, carrier_id uuid, carrier_name text, shipments bigint, novedad bigint, en_oficina bigint, devolucion bigint, con_problema bigint, problem_rate_pct numeric, value_in_office numeric, currency_code character)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT
        e.tenant_id,
        e.country_code,
        e.carrier_id,
        COALESCE(c.name, 'Sin transportadora'),
        count(*),
        count(*) FILTER (WHERE e.status_code = 'delivery_issue'),
        count(*) FILTER (WHERE e.status_code = 'in_office'),
        count(*) FILTER (WHERE e.status_code IN ('returning', 'returned')),
        count(*) FILTER (WHERE e.status_code IN
            ('delivery_issue', 'in_office', 'returning', 'returned')),
        round(count(*) FILTER (WHERE e.status_code IN
            ('delivery_issue', 'in_office', 'returning', 'returned'))::numeric
            / NULLIF(count(*), 0) * 100, 2),
        sum(e.declared_value) FILTER (WHERE e.status_code = 'in_office')::numeric(14, 2),
        min(e.currency_code)
    FROM stg.v_shipment_economics e
    LEFT JOIN core.carrier c ON c.id = e.carrier_id
    WHERE e.tenant_id = core.current_tenant_id()
      AND mart.f_platform_matches(e.connection_id, p_platform)
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;
$function$;

-- ---------------------------------------------------------------------------
-- f_product_performance
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_product_performance(date, date, text);
DROP FUNCTION IF EXISTS mart.f_product_performance(date, date, text, text);
CREATE FUNCTION mart.f_product_performance(p_date_from date DEFAULT NULL::date, p_date_to date DEFAULT NULL::date, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
 RETURNS TABLE(tenant_id uuid, country_code character, product_id uuid, product_name text, sku text, supplier_name text, shipments bigint, units bigint, delivered bigint, returned bigint, delivery_rate_pct numeric, revenue numeric, cogs numeric, freight numeric, contribution numeric, contribution_per_shipment numeric, margin_pct numeric, currency_code character)
 LANGUAGE sql
 STABLE
AS $function$
    SELECT
        e.tenant_id,
        e.country_code,
        e.product_id,
        COALESCE(p.name, 'Sin producto'),
        p.sku,
        sup.name,
        count(*),
        sum(e.quantity),
        count(*) FILTER (WHERE e.is_delivered),
        count(*) FILTER (WHERE e.is_returned),
        round(count(*) FILTER (WHERE e.is_delivered)::numeric
            / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) * 100, 2),
        sum(e.revenue_amount)::numeric(14, 2),
        sum(e.cogs_amount)::numeric(14, 2),
        sum(e.freight_amount)::numeric(14, 2),
        (sum(e.revenue_amount) - sum(e.freight_amount) - sum(e.cogs_amount)
            - sum(e.fee_amount))::numeric(14, 2),
        round((sum(e.revenue_amount) - sum(e.freight_amount) - sum(e.cogs_amount)
            - sum(e.fee_amount)) / NULLIF(count(*), 0), 2),
        round((sum(e.revenue_amount) - sum(e.freight_amount) - sum(e.cogs_amount)
            - sum(e.fee_amount)) / NULLIF(sum(e.revenue_amount), 0) * 100, 2),
        min(e.currency_code)
    FROM stg.v_shipment_economics e
    LEFT JOIN core.product p    ON p.id = e.product_id
    LEFT JOIN core.supplier sup ON sup.id = p.supplier_id
    WHERE e.tenant_id = core.current_tenant_id()
      AND mart.f_platform_matches(e.connection_id, p_platform)
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.product_id, p.name, p.sku, sup.name;
$function$;

-- ---------------------------------------------------------------------------
-- f_excluded_no_date: the excluded count must describe the same guides the
-- screen shows, so it takes the platform too.
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS mart.f_excluded_no_date(text, text);
DROP FUNCTION IF EXISTS mart.f_excluded_no_date(text, text, text);
CREATE FUNCTION mart.f_excluded_no_date(p_country text DEFAULT NULL::text, p_date_field text DEFAULT 'creacion'::text, p_platform text DEFAULT NULL::text)
 RETURNS bigint
 LANGUAGE sql
 STABLE
AS $function$
    SELECT count(*)
    FROM core.shipment s
    WHERE s.tenant_id = core.current_tenant_id()
      AND mart.f_platform_matches(s.connection_id, p_platform)
      AND (p_country IS NULL OR s.country_code = upper(p_country))
      AND mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at,
                           s.delivered_at) IS NULL;
$function$;
