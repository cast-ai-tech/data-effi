-- =============================================================================
-- Data Effi - 018 - A date range for every KPI, not only for finance.
--
-- THE PROBLEM
-- The date picker worked on exactly three widgets. `v_daily_contribution`,
-- `v_cohort_maturation`, `v_cs_confirmation` and `v_cpa_roas` carry a date in
-- their grain, so the router could filter them with a WHERE clause. Every other
-- mart view is an aggregate with the date already summed away - one row per
-- carrier, per city, per product - so "del 1 al 31 de enero" silently returned
-- the whole history. A filter that is ignored is worse than a filter that is
-- absent: the operator believes they are looking at January.
--
-- WHY A FUNCTION AND NOT A LOWER-GRAIN VIEW
-- The obvious fix is to add `created_date` to each view's GROUP BY and let the
-- router re-aggregate over the range. It does not survive contact with two of
-- these views. `p90_days_to_deliver`, `p50_prep_days` and `p90_days_to_cash` are
-- percentiles: you cannot recover the 90th percentile of a month from thirty
-- daily 90th percentiles, and `main_city` in the customer view is a mode(), with
-- the same problem. Re-aggregating them would produce a number that looks
-- plausible and is wrong, which is the one outcome worth avoiding.
--
-- So each aggregate becomes a SET-RETURNING FUNCTION that takes the range and
-- computes from the base rows. Percentiles are exact because they are taken over
-- the filtered set, not reassembled from summaries.
--
-- THE VIEWS ARE NOT TOUCHED, AND THAT COSTS SOMETHING WORTH NAMING.
-- The tidy version of this migration repoints each view at its function -
-- `CREATE OR REPLACE VIEW mart.v_x AS SELECT * FROM mart.f_x(NULL, NULL)` - so
-- the arithmetic lives in exactly one place. It was written that way first, and
-- then reverted, because of a rule that is easy to miss:
--
--     a view reads its TABLES with the view owner's privileges, but EXECUTE on
--     a FUNCTION is checked against the caller - and so is everything that
--     function's body then reads, because SECURITY INVOKER is the default.
--
-- So the moment a view calls a function, its body stops running as the view
-- owner. `norte_readonly` - the role behind the NL->SQL copilot, which has no
-- USAGE on `core` or `stg` on purpose - went from reading eleven aggregates
-- fine to "permission denied for schema stg". The fixes on offer were to widen
-- that role's reach across two schemas, or to make thirteen SECURITY DEFINER
-- functions. Both trade a real containment boundary for tidier SQL.
--
-- So the views stay exactly as 003, 008, 009, 015 and 017 left them, and the
-- functions are a second definition of the same arithmetic. That duplication is
-- the actual cost here, and it is held down by a parity test
-- (tests/test_kpi_date_filters.py) that asserts `f_x(NULL, NULL)` returns the
-- same rows as `v_x`, column for column. If the two ever disagree, that test
-- says so before a dashboard does.
--
-- WHICH DATE. The default is the CREATION DATE OF THE GUIDE - the cohort date,
-- the way `v_daily_contribution` already worked. It is the only date every guide
-- has. A guide in transit has no delivery date and no settlement date, so
-- filtering on either makes the still-open guides disappear from the dashboard -
-- and those are precisely the ones the operator has to chase today. 939 of the
-- 1,649 guides in the Ecuador dataset are open; a delivery-date filter would
-- hide 57% of the operation.
--
-- WHERE THAT DECISION WAS RE-EXAMINED, PER VIEW:
--
--   f_cash_cycle       - the money date was the tempting choice here and it is
--                        wrong. Filtering on `settled_at` drops every row where
--                        `settled_at IS NULL`, and those rows ARE the metric:
--                        `delivered_unsettled` and `cash_in_transit` count
--                        exactly them. The widget would report "nothing pending"
--                        for any range. Filters by creation date.
--
--   f_aging,           - both describe a state as of TODAY (days open, days
--   f_office_rescue      waiting at the office). The range selects WHICH guides
--                        are in scope - the ones created in that window - while
--                        the age is still measured against CURRENT_DATE. A guide
--                        created in January and still in an office in August
--                        correctly reports 200 days waiting, not 31.
--
--   f_global_summary   - shipments filter by creation date and ad spend by spend
--                        date, which is the same calendar axis. The FX rate is
--                        deliberately NOT filtered: it stays "the latest rate we
--                        know", because converting a historical window at a
--                        historical rate restates today's exposure as something
--                        it is not.
--
--   f_customer_metrics - filtering changes what the columns MEAN, and that is
--                        intended. With a range, `first_order_date` is the first
--                        order inside the window and `customer_grade` grades
--                        behaviour inside the window. An operator who filters to
--                        January is asking about January's customers.
--
-- NOT COVERED, ON PURPOSE:
--   mart.v_country_dashboard_layout (/kpis/layout) - it answers "which widgets
--   exist for this country", which has no date dimension and must not gain one.
--   That endpoint reports date_basis = null rather than pretending.
--
-- GRANTS. A new function is EXECUTE-able by PUBLIC unless you say otherwise, and
-- one of these reads rows about real people. All thirteen are revoked from
-- PUBLIC and granted to `norte_app` alone - the API is the only caller. The
-- copilot keeps reading the views, which is what its allow-list names and all it
-- has ever needed.
--
-- NUMBERING. This was commissioned as 017; 017_customer_contact_detail.sql took
-- that number first and appended four columns to mart.v_customer_metrics, which
-- this file rebuilds. Two files sharing a number would leave that dependency
-- resting on alphabetical tie-breaking. It is 018 so the order is stated, not
-- inferred.
--
-- Depends on: 003, 008, 009, 015, 017. Idempotent.
-- =============================================================================

-- =============================================================================
-- 1. CONTRIBUTION, SPLIT  (mart.v_contribution_split)
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_contribution_split(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
          AND (p_date_from IS NULL OR s.created_date >= p_date_from)
          AND (p_date_to   IS NULL OR s.created_date <= p_date_to)
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

COMMENT ON FUNCTION mart.f_contribution_split IS
    'Contribución cerrada vs. capital en la calle, sobre las guías CREADAS en el
     rango. Sin rango = todo el histórico.';

-- =============================================================================
-- 2. CARRIER EFFECTIVENESS  (mart.v_carrier_effectiveness)
-- Percentile lives here: p90 over the filtered set, never reassembled.
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_carrier_effectiveness(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
      AND (p_date_from IS NULL OR e.created_date >= p_date_from)
      AND (p_date_to   IS NULL OR e.created_date <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;
$fn$;

COMMENT ON FUNCTION mart.f_carrier_effectiveness IS
    'Efectividad por transportadora sobre las guías creadas en el rango. El p90
     se calcula sobre el conjunto filtrado, no se recompone de p90 diarios.';

-- =============================================================================
-- 3. GEOGRAPHY  (mart.v_geo_performance)
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_geo_performance(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
      AND (p_date_from IS NULL OR e.created_date >= p_date_from)
      AND (p_date_to   IS NULL OR e.created_date <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.geo_id,
             g.level1_name, g.city_name, g.city_normalized;
$fn$;

COMMENT ON FUNCTION mart.f_geo_performance IS
    'Semáforo geográfico sobre las guías creadas en el rango. El umbral de 10
     guías terminales para salir de "sin_datos" se aplica DENTRO del rango: una
     ciudad con poco volumen en enero dice "sin_datos", que es la verdad.';

-- =============================================================================
-- 4. PRODUCTS  (mart.v_product_performance)
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_product_performance(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
      AND (p_date_from IS NULL OR e.created_date >= p_date_from)
      AND (p_date_to   IS NULL OR e.created_date <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.product_id, p.name, p.sku, sup.name;
$fn$;

COMMENT ON FUNCTION mart.f_product_performance IS
    'Margen x entrega por producto, sobre las guías creadas en el rango.';

-- =============================================================================
-- 5. AGING  (mart.v_aging)
-- The range picks WHICH open guides; the age is still measured against today.
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_aging(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
      AND (p_date_from IS NULL OR e.created_date >= p_date_from)
      AND (p_date_to   IS NULL OR e.created_date <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, 3, 4;
$fn$;

COMMENT ON FUNCTION mart.f_aging IS
    'Guías abiertas HOY, creadas dentro del rango. La antigüedad se mide contra
     CURRENT_DATE, no contra el final del rango: una guía de enero que sigue
     abierta lleva los días que lleva.';

-- =============================================================================
-- 6. DROPSHIPPING MARGIN CHAIN  (mart.v_dropshipping_margin)
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_dropshipping_margin(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
          AND (p_date_from IS NULL OR s.created_date >= p_date_from)
          AND (p_date_to   IS NULL OR s.created_date <= p_date_to)
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

COMMENT ON FUNCTION mart.f_dropshipping_margin IS
    'Cadena de márgenes por producto sobre las guías creadas en el rango. Flete y
     stock siguen contando en TODA guía despachada, no sólo en las entregadas.';

-- =============================================================================
-- 7. FULFILMENT SLA  (mart.v_fulfillment_sla)
-- Three percentiles: the reason this is a function and not a lower-grain view.
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_fulfillment_sla(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
          AND (p_date_from IS NULL OR s.created_date >= p_date_from)
          AND (p_date_to   IS NULL OR s.created_date <= p_date_to)
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

COMMENT ON FUNCTION mart.f_fulfillment_sla IS
    'Alistamiento vs. tránsito y cumplimiento de promesa, sobre las guías creadas
     en el rango. Los tres percentiles se calculan sobre el conjunto filtrado.';

-- =============================================================================
-- 8. OFFICE QUEUE  (mart.v_office_rescue)
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_office_rescue(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
          AND (p_date_from IS NULL OR s.created_date >= p_date_from)
          AND (p_date_to   IS NULL OR s.created_date <= p_date_to)
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

COMMENT ON FUNCTION mart.f_office_rescue IS
    'Guías esperando en oficina HOY, creadas dentro del rango. Los días de espera
     se miden contra CURRENT_DATE.';

-- =============================================================================
-- 9. FREIGHT  (mart.v_freight_analysis)
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_freight_analysis(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
      AND (p_date_from IS NULL OR s.created_date >= p_date_from)
      AND (p_date_to   IS NULL OR s.created_date <= p_date_to)
    GROUP BY s.tenant_id, s.country_code, s.carrier_id, c.name, s.service_level;
$fn$;

COMMENT ON FUNCTION mart.f_freight_analysis IS
    'Flete por kilo y por componente sobre las guías creadas en el rango.';

-- =============================================================================
-- 10. CASH CYCLE  (mart.v_cash_cycle)
--
-- Filters by CREATION DATE, not by settlement date, and the reason is the whole
-- point of the widget: `delivered_unsettled` and `cash_in_transit` count rows
-- where settled_at IS NULL. A settlement-date filter removes every one of them,
-- and the widget would report that nothing is pending in any range.
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_cash_cycle(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
      AND (p_date_from IS NULL OR e.created_date >= p_date_from)
      AND (p_date_to   IS NULL OR e.created_date <= p_date_to)
    GROUP BY e.tenant_id, e.country_code;
$fn$;

COMMENT ON FUNCTION mart.f_cash_cycle IS
    'Ciclo de caja de las guías CREADAS en el rango. No filtra por settled_at a
     propósito: eso borraría justamente las guías sin liquidar, que son la mitad
     de la métrica.';

-- =============================================================================
-- 11. PROBLEM RATE  (mart.v_problem_rate)
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_problem_rate(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
      AND (p_date_from IS NULL OR e.created_date >= p_date_from)
      AND (p_date_to   IS NULL OR e.created_date <= p_date_to)
    GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;
$fn$;

COMMENT ON FUNCTION mart.f_problem_rate IS
    'Novedad + oficina + devolución como un solo número, sobre las guías creadas
     en el rango.';

-- =============================================================================
-- 12. GLOBAL SUMMARY  (mart.v_global_summary)
--
-- The FX rate stays "the latest one we know" even with a range. A historical
-- window converted at a historical rate answers a different question than the
-- one the dashboard asks, which is "what is this worth to me now".
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_global_summary(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
          AND (p_date_from IS NULL OR e.created_date >= p_date_from)
          AND (p_date_to   IS NULL OR e.created_date <= p_date_to)
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

COMMENT ON FUNCTION mart.f_global_summary IS
    'Consolidado multi-país sobre las guías creadas en el rango; la pauta se
     filtra por spend_date, el mismo eje de calendario. La tasa de cambio NO se
     filtra: siempre es la última conocida.';

-- =============================================================================
-- 13. CUSTOMER METRICS  (mart.v_customer_metrics)
--
-- WITH A RANGE, THESE COLUMNS CHANGE MEANING, AND THAT IS THE POINT.
-- `first_order_date` becomes the first order INSIDE the window, and
-- `customer_grade` grades behaviour inside the window. An operator filtering to
-- January is asking about January's customers, not about each customer's whole
-- history seen through a January lens.
--
-- `main_city` is a mode(). Like the percentiles above, it cannot be recomposed
-- from daily summaries - computing it here, over the filtered set, is the only
-- way it stays correct.
-- =============================================================================
CREATE OR REPLACE FUNCTION mart.f_customer_metrics(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL
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
          AND (p_date_from IS NULL OR o.created_date >= p_date_from)
          AND (p_date_to   IS NULL OR o.created_date <= p_date_to)
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

COMMENT ON FUNCTION mart.f_customer_metrics IS
    'Un cliente por fila, sobre los pedidos creados en el rango. Con rango,
     first_order_date y customer_grade describen el comportamiento DENTRO de la
     ventana, no el histórico completo.';

-- =============================================================================
-- GRANTS
--
-- A new function is EXECUTE-able by PUBLIC unless you say otherwise, and
-- `f_customer_metrics` reads rows about real people. Revoked from PUBLIC, then
-- granted to `norte_app` and to nothing else: the API is the only caller, and it
-- is the role these functions were shaped for - non-superuser, NOBYPASSRLS, so
-- every read inside a function body is still subject to row-level security on
-- top of the explicit `tenant_id = core.current_tenant_id()` filter.
--
-- norte_readonly is deliberately absent. It reads the views, which this
-- migration does not touch, so its reach is exactly what it was.
-- =============================================================================
REVOKE ALL ON FUNCTION
    mart.f_contribution_split(date, date),
    mart.f_carrier_effectiveness(date, date),
    mart.f_geo_performance(date, date),
    mart.f_product_performance(date, date),
    mart.f_aging(date, date),
    mart.f_dropshipping_margin(date, date),
    mart.f_fulfillment_sla(date, date),
    mart.f_office_rescue(date, date),
    mart.f_freight_analysis(date, date),
    mart.f_cash_cycle(date, date),
    mart.f_problem_rate(date, date),
    mart.f_global_summary(date, date),
    mart.f_customer_metrics(date, date)
FROM PUBLIC;

GRANT EXECUTE ON FUNCTION
    mart.f_contribution_split(date, date),
    mart.f_carrier_effectiveness(date, date),
    mart.f_geo_performance(date, date),
    mart.f_product_performance(date, date),
    mart.f_aging(date, date),
    mart.f_dropshipping_margin(date, date),
    mart.f_fulfillment_sla(date, date),
    mart.f_office_rescue(date, date),
    mart.f_freight_analysis(date, date),
    mart.f_cash_cycle(date, date),
    mart.f_problem_rate(date, date),
    mart.f_global_summary(date, date),
    mart.f_customer_metrics(date, date)
TO norte_app;
