-- =============================================================================
-- Data Effi - 020 - Three dates, and saying which one you are looking through.
--
-- WHAT CHANGED AND WHY
-- Migration 018 gave every KPI a date range, always measured against the day
-- the guide was created. That was the right default and it is still the
-- default. It was not the whole request. The operator, verbatim:
--
--     "todas las guías deben tener una fecha de creación y una fecha de
--      despacho... también fechas de cambios de estado, fecha de entregado.
--      Todas las fechas son importantes"
--
-- They are, and they answer different questions. "¿Cuánto despaché esta
-- semana?" is not "¿cuánto vendí esta semana?" and neither is "¿cuánto entregué
-- esta semana?". So the thirteen range-aware functions from 018 take a third
-- argument, `p_date_field`, and the API exposes it as `date_field`:
--
--     creacion  -> core.shipment.created_date   (por defecto)
--     despacho  -> core.shipment.dispatched_at  (la relación de despacho real,
--                                                desde la migración 019)
--     entrega   -> core.shipment.delivered_at
--
-- ONE MAPPING, ONE PLACE. `mart.f_pick_date` below is the only thing that knows
-- which column each name means. Thirteen functions call it; none of them
-- repeats the CASE. Adding a fourth date later is one edit.
--
-- THE TRAP, AND THE COLUMN THAT EXISTS TO DEFUSE IT
-- `created_date` is NOT NULL - every guide has one. The other two are not:
--
--     de las 1.649 guías reales de Ecuador, 989 NO tienen fecha de entrega
--
-- and a NULL date can never fall inside a range. So `date_field=entrega`
-- silently removes 60% of the operation: exactly the guides still in transit,
-- in novedad, or devueltas - the ones the operator has to chase. That is not a
-- bug to fix, it is the honest meaning of "entregadas entre el 1 y el 31". But
-- an operator must never see it and believe they are looking at everything.
--
-- `mart.f_excluded_no_date` counts them, and the API returns the number as
-- `excluded_no_date` so the interface can say "989 guías quedan fuera: aún no
-- tienen fecha de entrega". It counts ONLY when a range is actually applied -
-- with no range nothing is filtered and nothing is excluded.
--
-- Two widgets go empty under `date_field=entrega`, and correctly so:
-- `f_aging` and `f_office_rescue` describe guides that are still OPEN, and an
-- open guide has no delivery date by definition. They return zero rows and an
-- `excluded_no_date` equal to their whole population, which is the true answer
-- to "¿qué guías abiertas se entregaron en enero?".
--
-- WHERE `date_field` IS DELIBERATELY IGNORED (the API reports the basis it
-- really used, never the one that was asked for):
--
--   /kpis/daily-contribution - its `day` axis carries the ad spend joined on
--                              the same day. Re-axing it onto dispatch or
--                              delivery would attribute Monday's media cost to
--                              guides created the week before, quietly moving
--                              `contribution`. Stays on creation.
--   /kpis/cohorts            - a cohort IS the creation cohort; `days_since`
--                              measures creation to delivery. Grouping by
--                              delivery date makes every guide arrive on day 0
--                              of its own cohort. Circular, so: creation.
--   /kpis/cs                 - the day customer service worked (interaccion).
--   /kpis/cpa                - the day the money was spent on ads (pauta).
--
-- ONE LOOSE END, NAMED RATHER THAN HIDDEN. `f_global_summary` subtracts ad
-- spend from the same calendar window whatever `date_field` says. With
-- `despacho` or `entrega` that spend no longer belongs to the cohort of the
-- guides being shown - it is still "lo que gasté en pauta esa semana", which is
-- a real number, but it is not matched to those guides. Filtering ad spend by a
-- guide date is not possible: a spend row has one date and no guide.
--
-- `mart.v_orders` gains `dispatched_at`, appended LAST, because
-- `f_customer_metrics` reads the orders view and needed a dispatch date to
-- offer. New columns always go at the end - `CREATE OR REPLACE VIEW` cannot
-- reorder or drop.
--
-- The thirteen functions are DROPPED and recreated rather than replaced: a new
-- parameter is a new signature, and leaving the two-argument version behind
-- would make `f_x(a, b)` ambiguous. Nothing depends on them but the API - 018
-- deliberately left the views alone - so the drop is safe.
--
-- Depends on: 018, 019. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- The mapping. The only place that knows what "despacho" means.
-- -----------------------------------------------------------------------------
-- STABLE, not IMMUTABLE: casting a timestamptz to a date depends on the session
-- TimeZone, so promising immutability here would be a lie the planner believes.
CREATE OR REPLACE FUNCTION mart.f_pick_date(
    p_field      text,
    p_created    date,
    p_dispatched timestamptz,
    p_delivered  timestamptz
)
RETURNS date
LANGUAGE sql
STABLE
AS $fn$
    SELECT CASE p_field
        WHEN 'despacho' THEN p_dispatched::date
        WHEN 'entrega'  THEN p_delivered::date
        ELSE p_created
    END;
$fn$;

COMMENT ON FUNCTION mart.f_pick_date IS
    'Traduce el nombre de una fecha (creacion|despacho|entrega) a la columna que
     le corresponde. NULL si la guía todavía no tiene esa fecha, y una fecha
     NULL nunca cae dentro de un rango: por eso existe f_excluded_no_date.';

-- -----------------------------------------------------------------------------
-- How many guides the chosen date leaves out entirely
-- -----------------------------------------------------------------------------
-- Not "how many fell outside the window" - that is what a filter is for - but
-- how many CANNOT be in any window, because the date they would be judged by
-- does not exist yet.
CREATE OR REPLACE FUNCTION mart.f_excluded_no_date(
    p_country    text DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS bigint
LANGUAGE sql
STABLE
AS $fn$
    SELECT count(*)
    FROM core.shipment s
    WHERE s.tenant_id = core.current_tenant_id()
      AND (p_country IS NULL OR s.country_code = upper(p_country))
      AND mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at,
                           s.delivered_at) IS NULL;
$fn$;

COMMENT ON FUNCTION mart.f_excluded_no_date IS
    'Guías que no pueden aparecer en NINGÚN rango sobre esa fecha porque aún no
     la tienen. Con date_field=entrega son las que siguen en la calle: el número
     que la interfaz debe mostrar junto al filtro.';

-- -----------------------------------------------------------------------------
-- The orders view needs a dispatch date to offer
-- -----------------------------------------------------------------------------
-- Appended last. CREATE OR REPLACE VIEW can add a column at the end and nothing
-- else - not reorder, not drop, not retype.
CREATE OR REPLACE VIEW mart.v_orders AS
SELECT
    s.id                                                        AS shipment_id,
    s.country_code,
    s.tracking_number,
    s.carrier_tracking_number,
    s.created_date,
    s.delivered_at,
    s.status_code,
    sc.label                                                    AS status_label,
    sc.bucket                                                   AS status_bucket,
    sc.is_terminal,
    sc.is_delivered,
    sc.is_returned,

    s.customer_hash,
    s.customer_name_enc,
    s.customer_phone_enc,
    s.customer_address_enc,
    s.customer_document_enc,
    COALESCE(s.customer_city_name, g.city_name)                 AS city_name,
    g.level1_name                                               AS province_name,

    p.name                                                      AS product_name,
    s.quantity,
    ca.name                                                     AS carrier_name,

    ec.revenue_amount,
    ec.freight_amount,
    ec.cogs_amount,
    ec.fee_amount,
    (ec.revenue_amount - ec.freight_amount - ec.cogs_amount - ec.fee_amount)
        ::numeric(14,2)                                         AS contribution,
    co.currency_code,

    ec.days_open,
    ec.movement_count,

    -- Appended by 020, so the customer view can be filtered by dispatch date.
    s.dispatched_at
FROM core.shipment s
JOIN core.status_canon sc     ON sc.code = s.status_code
JOIN core.country co          ON co.code = s.country_code
JOIN stg.v_shipment_economics ec ON ec.shipment_id = s.id
LEFT JOIN core.geo g          ON g.id = s.geo_id
LEFT JOIN core.product p      ON p.id = s.product_id
LEFT JOIN core.carrier ca     ON ca.id = s.carrier_id
WHERE s.tenant_id = core.current_tenant_id();

-- -----------------------------------------------------------------------------
-- Out with the two-argument versions
-- -----------------------------------------------------------------------------
-- A default on the new third argument would make `f_x(from, to)` ambiguous
-- between the old and the new function, so the old one goes.
DROP FUNCTION IF EXISTS mart.f_contribution_split(date, date);
DROP FUNCTION IF EXISTS mart.f_carrier_effectiveness(date, date);
DROP FUNCTION IF EXISTS mart.f_geo_performance(date, date);
DROP FUNCTION IF EXISTS mart.f_product_performance(date, date);
DROP FUNCTION IF EXISTS mart.f_aging(date, date);
DROP FUNCTION IF EXISTS mart.f_dropshipping_margin(date, date);
DROP FUNCTION IF EXISTS mart.f_fulfillment_sla(date, date);
DROP FUNCTION IF EXISTS mart.f_office_rescue(date, date);
DROP FUNCTION IF EXISTS mart.f_freight_analysis(date, date);
DROP FUNCTION IF EXISTS mart.f_cash_cycle(date, date);
DROP FUNCTION IF EXISTS mart.f_problem_rate(date, date);
DROP FUNCTION IF EXISTS mart.f_global_summary(date, date);
DROP FUNCTION IF EXISTS mart.f_customer_metrics(date, date);

-- =============================================================================
-- THE THIRTEEN, NOW WITH A CHOICE OF DATE
--
-- Every body below is the one migration 018 established, unchanged except for
-- the third parameter and the two range predicates, which now go through
-- mart.f_pick_date. The parity test in tests/test_kpi_date_filters.py still
-- compares each `f_x(NULL, NULL)` against its view, so the arithmetic cannot
-- have drifted while this parameter was threaded through.
-- =============================================================================

CREATE OR REPLACE FUNCTION mart.f_contribution_split(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    country_code           char(2),
    currency_code          char(3),
    shipments              bigint,
    closed_shipments       bigint,
    open_shipments         bigint,
    realised_revenue       numeric,
    realised_cost          numeric,
    realised_contribution  numeric,
    realised_margin_pct    numeric,
    capital_in_street      numeric,
    committed_revenue      numeric,
    net_contribution       numeric,
    maturity_pct           numeric
)
LANGUAGE sql
STABLE
AS $fn$
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
$fn$;

CREATE OR REPLACE FUNCTION mart.f_carrier_effectiveness(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    tenant_id                uuid,
    country_code             char(2),
    carrier_id               uuid,
    carrier_name             text,
    first_shipment_date      date,
    last_shipment_date       date,
    shipments                bigint,
    delivered                bigint,
    returned                 bigint,
    in_transit               bigint,
    delivery_rate_pct        numeric,
    return_rate_pct          numeric,
    avg_days_to_deliver      numeric,
    p90_days_to_deliver      numeric,
    freight_total            numeric,
    avg_freight_per_shipment numeric,
    revenue                  numeric,
    contribution             numeric,
    currency_code            bpchar
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
        min(e.currency_code)
    FROM stg.v_shipment_economics e
    LEFT JOIN core.carrier c ON c.id = e.carrier_id
    WHERE e.tenant_id = core.current_tenant_id()
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;
$fn$;

CREATE OR REPLACE FUNCTION mart.f_geo_performance(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    tenant_id           uuid,
    country_code        char(2),
    geo_id              uuid,
    level1_name         text,
    city_name           text,
    city_normalized     text,
    shipments           bigint,
    delivered           bigint,
    returned            bigint,
    in_transit          bigint,
    delivery_rate_pct   numeric,
    revenue             numeric,
    contribution        numeric,
    avg_days_to_deliver numeric,
    traffic_light       text,
    currency_code       bpchar
)
LANGUAGE sql
STABLE
AS $fn$
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
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.geo_id,
             g.level1_name, g.city_name, g.city_normalized;
$fn$;

CREATE OR REPLACE FUNCTION mart.f_product_performance(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    tenant_id                 uuid,
    country_code              char(2),
    product_id                uuid,
    product_name              text,
    sku                       text,
    supplier_name             text,
    shipments                 bigint,
    units                     bigint,
    delivered                 bigint,
    returned                  bigint,
    delivery_rate_pct         numeric,
    revenue                   numeric,
    cogs                      numeric,
    freight                   numeric,
    contribution              numeric,
    contribution_per_shipment numeric,
    margin_pct                numeric,
    currency_code             bpchar
)
LANGUAGE sql
STABLE
AS $fn$
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
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.product_id, p.name, p.sku, sup.name;
$fn$;

CREATE OR REPLACE FUNCTION mart.f_aging(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    tenant_id     uuid,
    country_code  char(2),
    aging_bucket  text,
    bucket_order  integer,
    shipments     bigint,
    value_at_risk numeric,
    avg_days_open numeric,
    currency_code bpchar
)
LANGUAGE sql
STABLE
AS $fn$
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
      AND NOT e.is_terminal
      AND e.days_open IS NOT NULL
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, 3, 4;
$fn$;

CREATE OR REPLACE FUNCTION mart.f_dropshipping_margin(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    tenant_id                 uuid,
    country_code              char(2),
    product_id                uuid,
    product_name              text,
    sku                       text,
    supplier_name             text,
    shipments                 bigint,
    delivered                 bigint,
    units                     bigint,
    revenue                   numeric,
    supplier_cost             numeric,
    freight                   numeric,
    gross_margin              numeric,
    gross_margin_pct          numeric,
    net_contribution          numeric,
    contribution_per_shipment numeric,
    cost_of_undelivered       numeric,
    breakeven_delivery_pct    numeric,
    delivery_rate_pct         numeric,
    catalogue_cost            numeric,
    catalogue_price           numeric,
    catalogue_reviewed        boolean,
    observed_unit_cost        numeric,
    currency_code             bpchar
)
LANGUAGE sql
STABLE
AS $fn$
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
$fn$;

CREATE OR REPLACE FUNCTION mart.f_fulfillment_sla(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    tenant_id        uuid,
    country_code     char(2),
    carrier_id       uuid,
    carrier_name     text,
    service_level    text,
    shipments        bigint,
    delivered        bigint,
    avg_prep_days    numeric,
    p50_prep_days    numeric,
    p90_prep_days    numeric,
    avg_transit_days numeric,
    p90_transit_days numeric,
    avg_total_days   numeric,
    prep_share_pct   numeric,
    on_time_count    bigint,
    measurable_count bigint,
    on_time_pct      numeric
)
LANGUAGE sql
STABLE
AS $fn$
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

        round(avg(b.prep_days) / NULLIF(avg(b.total_days), 0) * 100, 1),

        count(*) FILTER (WHERE b.on_time IS TRUE),
        count(*) FILTER (WHERE b.on_time IS NOT NULL),
        round(count(*) FILTER (WHERE b.on_time IS TRUE)::numeric
              / NULLIF(count(*) FILTER (WHERE b.on_time IS NOT NULL), 0) * 100, 2)
    FROM base b
    GROUP BY b.tenant_id, b.country_code, b.carrier_id, b.carrier_name, b.service_level;
$fn$;

CREATE OR REPLACE FUNCTION mart.f_office_rescue(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    tenant_id               uuid,
    country_code            char(2),
    carrier_name            text,
    level1_name             text,
    city_name               text,
    shipments               bigint,
    value_waiting           numeric,
    avg_days_waiting        numeric,
    fresh_0_7               bigint,
    aging_8_14              bigint,
    urgent_15_21            bigint,
    probably_lost           bigint,
    value_still_recoverable numeric,
    currency_code           bpchar
)
LANGUAGE sql
STABLE
AS $fn$
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
$fn$;

CREATE OR REPLACE FUNCTION mart.f_freight_analysis(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    tenant_id                  uuid,
    country_code               char(2),
    carrier_id                 uuid,
    carrier_name               text,
    service_level              text,
    shipments                  bigint,
    avg_weight_kg              numeric,
    total_weight_kg            numeric,
    freight_total              numeric,
    avg_freight                numeric,
    freight_per_kg             numeric,
    avg_freight_base           numeric,
    avg_handling               numeric,
    avg_collection_fee         numeric,
    avg_discount_pct           numeric,
    discount_value             numeric,
    freight_share_of_value_pct numeric,
    return_freight_total       numeric,
    currency_code              bpchar
)
LANGUAGE sql
STABLE
AS $fn$
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
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, s.created_date, s.dispatched_at, s.delivered_at) <= p_date_to)
    GROUP BY s.tenant_id, s.country_code, s.carrier_id, c.name, s.service_level;
$fn$;

CREATE OR REPLACE FUNCTION mart.f_cash_cycle(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    tenant_id           uuid,
    country_code        char(2),
    settled             bigint,
    delivered_unsettled bigint,
    avg_days_to_cash    numeric,
    p50_days_to_cash    numeric,
    p90_days_to_cash    numeric,
    cash_in_transit     numeric,
    currency_code       bpchar
)
LANGUAGE sql
STABLE
AS $fn$
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
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code;
$fn$;

CREATE OR REPLACE FUNCTION mart.f_problem_rate(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    tenant_id        uuid,
    country_code     char(2),
    carrier_id       uuid,
    carrier_name     text,
    shipments        bigint,
    novedad          bigint,
    en_oficina       bigint,
    devolucion       bigint,
    con_problema     bigint,
    problem_rate_pct numeric,
    value_in_office  numeric,
    currency_code    bpchar
)
LANGUAGE sql
STABLE
AS $fn$
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
      AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
      AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;
$fn$;

CREATE OR REPLACE FUNCTION mart.f_global_summary(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    tenant_id          uuid,
    country_code       char(2),
    country_name       text,
    currency_code      bpchar,
    shipments          bigint,
    delivered          bigint,
    returned           bigint,
    in_transit         bigint,
    delivery_rate_pct  numeric,
    revenue            numeric,
    ad_spend           numeric,
    contribution       numeric,
    fx_rate_to_usd     numeric,
    fx_rate_date       date,
    contribution_usd   numeric,
    fx_missing         boolean,
    last_shipment_date date
)
LANGUAGE sql
STABLE
AS $fn$
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
          AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
          AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
        GROUP BY e.tenant_id, e.country_code
    ),
    ads AS (
        SELECT a.tenant_id, a.country_code, sum(a.spend) AS ad_spend
        FROM core.ad_spend a
        WHERE a.tenant_id = core.current_tenant_id()
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
$fn$;

CREATE OR REPLACE FUNCTION mart.f_customer_metrics(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion'
)
RETURNS TABLE (
    customer_hash          char(64),
    customer_ref           text,
    country_code           char(2),
    currency_code          bpchar,
    orders                 bigint,
    delivered              bigint,
    returned               bigint,
    open_orders            bigint,
    revenue                numeric,
    contribution           numeric,
    returned_cost          numeric,
    distinct_products      bigint,
    main_city              text,
    first_order_date       date,
    last_order_date        date,
    days_since_last_order  integer,
    delivery_rate_pct      numeric,
    contribution_per_order numeric,
    customer_grade         text,
    last_name_enc          bytea,
    last_phone_enc         bytea,
    -- Appended by 017_customer_contact_detail.sql, kept in that exact position.
    last_document_enc      bytea,
    last_address_enc       bytea,
    last_city_name         text,
    last_province_name     text
)
LANGUAGE sql
STABLE
AS $fn$
    WITH per_customer AS (
        SELECT
            o.customer_hash,
            o.country_code,
            count(*)                                                AS orders,
            count(*) FILTER (WHERE o.is_delivered)                  AS delivered,
            count(*) FILTER (WHERE o.is_returned)                   AS returned,
            count(*) FILTER (WHERE NOT o.is_terminal)               AS open_orders,
            sum(o.revenue_amount)::numeric(14,2)                    AS revenue,
            sum(o.contribution)::numeric(14,2)                      AS contribution,
            sum(o.freight_amount + o.cogs_amount + o.fee_amount)
                FILTER (WHERE o.is_returned)::numeric(14,2)         AS returned_cost,
            min(o.created_date)                                     AS first_order_date,
            max(o.created_date)                                     AS last_order_date,
            max(o.currency_code)                                    AS currency_code,
            count(DISTINCT o.product_name)                          AS distinct_products,
            mode() WITHIN GROUP (ORDER BY o.city_name)              AS main_city,
            (array_agg(o.customer_name_enc ORDER BY o.created_date DESC)
                FILTER (WHERE o.customer_name_enc IS NOT NULL))[1]  AS last_name_enc,
            (array_agg(o.customer_phone_enc ORDER BY o.created_date DESC)
                FILTER (WHERE o.customer_phone_enc IS NOT NULL))[1] AS last_phone_enc,

            -- From 017: same rule, one field at a time - the most recent guide
            -- that actually carried that particular field.
            (array_agg(o.customer_document_enc ORDER BY o.created_date DESC)
                FILTER (WHERE o.customer_document_enc IS NOT NULL))[1] AS last_document_enc,
            (array_agg(o.customer_address_enc ORDER BY o.created_date DESC)
                FILTER (WHERE o.customer_address_enc IS NOT NULL))[1]  AS last_address_enc,
            (array_agg(o.city_name ORDER BY o.created_date DESC)
                FILTER (WHERE o.city_name IS NOT NULL))[1]          AS last_city_name,
            (array_agg(o.province_name ORDER BY o.created_date DESC)
                FILTER (WHERE o.province_name IS NOT NULL))[1]      AS last_province_name
        FROM mart.v_orders o
        WHERE o.customer_hash IS NOT NULL
          AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, o.created_date, o.dispatched_at, o.delivered_at) >= p_date_from)
          AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, o.created_date, o.dispatched_at, o.delivered_at) <= p_date_to)
        GROUP BY o.customer_hash, o.country_code
    )
    SELECT
        pc.customer_hash,
        ('#' || upper(left(pc.customer_hash, 6))),
        pc.country_code,
        pc.currency_code,
        pc.orders,
        pc.delivered,
        pc.returned,
        pc.open_orders,
        pc.revenue,
        pc.contribution,
        pc.returned_cost,
        pc.distinct_products,
        pc.main_city,
        pc.first_order_date,
        pc.last_order_date,
        (CURRENT_DATE - pc.last_order_date),

        round(pc.delivered::numeric
              / NULLIF(pc.delivered + pc.returned, 0) * 100, 1),
        round(pc.contribution / NULLIF(pc.orders, 0), 2),

        CASE
            WHEN pc.delivered + pc.returned < 2                        THEN 'nuevo'
            WHEN pc.returned = 0 AND pc.delivered >= 3                 THEN 'excelente'
            WHEN pc.delivered::numeric
                 / NULLIF(pc.delivered + pc.returned, 0) >= 0.8        THEN 'bueno'
            WHEN pc.delivered::numeric
                 / NULLIF(pc.delivered + pc.returned, 0) >= 0.5        THEN 'regular'
            ELSE 'riesgo'
        END,
        pc.last_name_enc,
        pc.last_phone_enc,
        pc.last_document_enc,
        pc.last_address_enc,
        pc.last_city_name,
        pc.last_province_name
    FROM per_customer pc;
$fn$;
-- =============================================================================
-- GRANTS
--
-- The functions were dropped, so their grants went with them. Same matrix as
-- 018: revoked from PUBLIC (one of these reads rows about real people), granted
-- to norte_app and to nothing else. norte_readonly - the copilot - keeps
-- reading the views, which this migration does not touch either.
-- =============================================================================
REVOKE ALL ON FUNCTION
    mart.f_pick_date(text, date, timestamptz, timestamptz),
    mart.f_excluded_no_date(text, text),
    mart.f_contribution_split(date, date, text),
    mart.f_carrier_effectiveness(date, date, text),
    mart.f_geo_performance(date, date, text),
    mart.f_product_performance(date, date, text),
    mart.f_aging(date, date, text),
    mart.f_dropshipping_margin(date, date, text),
    mart.f_fulfillment_sla(date, date, text),
    mart.f_office_rescue(date, date, text),
    mart.f_freight_analysis(date, date, text),
    mart.f_cash_cycle(date, date, text),
    mart.f_problem_rate(date, date, text),
    mart.f_global_summary(date, date, text),
    mart.f_customer_metrics(date, date, text)
FROM PUBLIC;

GRANT EXECUTE ON FUNCTION
    mart.f_pick_date(text, date, timestamptz, timestamptz),
    mart.f_excluded_no_date(text, text),
    mart.f_contribution_split(date, date, text),
    mart.f_carrier_effectiveness(date, date, text),
    mart.f_geo_performance(date, date, text),
    mart.f_product_performance(date, date, text),
    mart.f_aging(date, date, text),
    mart.f_dropshipping_margin(date, date, text),
    mart.f_fulfillment_sla(date, date, text),
    mart.f_office_rescue(date, date, text),
    mart.f_freight_analysis(date, date, text),
    mart.f_cash_cycle(date, date, text),
    mart.f_problem_rate(date, date, text),
    mart.f_global_summary(date, date, text),
    mart.f_customer_metrics(date, date, text)
TO norte_app;

GRANT SELECT ON mart.v_orders TO norte_app;
