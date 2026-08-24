-- =============================================================================
-- Data Effi - 040 - The platform as a dimension: Effi next to Dropi.
--
-- WHAT THE OPERATOR SHOWED US. A hand-made daily report, one block per
-- platform (Effi, Dropi), one row per day, four status columns, a total and a
-- "% devolución", and a strip at the bottom adding both up. Everything in it
-- is already in this database - a guide knows which connection loaded it, and
-- a connection knows its platform - except that no view or function ever
-- GROUPED by it. This migration adds the dimension; 041 threads it through
-- the existing range-aware functions as a filter.
--
-- FOUR THINGS LAND HERE.
--
-- 1. STATUS GROUPS FOR THE SCREEN. Twelve canonical statuses are the right
--    granularity for merging files and the wrong one for a daily table. The
--    report uses four words - entregada, devolución, en tránsito, novedad - and
--    they mean the same thing on both platforms even though Effi writes
--    "Entregada a destino" and Dropi writes "Entregado". `display_group` on
--    core.status_canon is that vocabulary. It never replaces the canonical
--    code: an "en oficina" guide still says "en oficina" on its own row, it
--    just counts under "novedad" in a column that has no room for twelve.
--
-- 2. THE ALIASES DROPI ACTUALLY WRITES. "Incidencia en ruta" appeared in the
--    operator's Dropi export and matched nothing, so it would have fallen to
--    `created` with a warning. The rest are the same words Effi already has,
--    registered under Dropi so a per-platform lookup finds them.
--
-- 3. THE PLATFORM ON EVERY GUIDE'S ECONOMICS. `stg.v_shipment_economics`
--    gains `platform_code` and `platform_name`, appended LAST (CREATE OR
--    REPLACE VIEW cannot reorder). `mart.f_platform_matches` is the one-line
--    predicate 041 adds to every function: NULL means "todas".
--
-- 4. THE TWO NEW ANSWERS. `v_daily_status_by_platform` / `f_daily_status` is
--    the table in the report: day x platform x status group. `v_platform_summary`
--    / `f_platform_summary` is the strip at the bottom: one row per platform
--    with its share of the guides.
--
-- TWO PERCENTAGES, ON PURPOSE. The report divides returns by ALL guides of the
-- day, which understates the return rate on every recent day - a guide still
-- in transit cannot have been returned yet. Both numbers travel:
--   pct_devolucion_total     devoluciones / guías del día   (lo que dice el informe)
--   pct_devolucion_cerradas  devoluciones / guías cerradas  (lo que va a pasar)
-- and `sample_quality` marks a day with fewer than ten closed guides as an
-- estimate, the same rule migration 021 gave the carrier table.
--
-- "VENTAS" IS NOT A COLUMN. The report calls every guide a sale. A guide is a
-- sale when it is delivered and paid; until then it is a parcel. The column is
-- `shipments`, and revenue is what was actually collected.
--
-- Depends on: 001-039. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Status groups for the screen
-- -----------------------------------------------------------------------------
ALTER TABLE core.status_canon
    ADD COLUMN IF NOT EXISTS display_group text NOT NULL DEFAULT 'en_camino'
        CHECK (display_group IN ('entregada', 'devolucion', 'en_camino', 'novedad', 'muerta'));

UPDATE core.status_canon SET display_group = CASE code
    WHEN 'delivered'        THEN 'entregada'
    WHEN 'returning'        THEN 'devolucion'
    WHEN 'returned'         THEN 'devolucion'
    WHEN 'delivery_issue'   THEN 'novedad'
    WHEN 'in_office'        THEN 'novedad'
    WHEN 'cancelled'        THEN 'muerta'
    WHEN 'lost'             THEN 'muerta'
    ELSE 'en_camino'
END;

COMMENT ON COLUMN core.status_canon.display_group IS
    'Vocabulario de pantalla, cinco palabras: entregada | devolucion | en_camino |
     novedad | muerta. Nunca reemplaza al código canónico; agrupa columnas donde
     no caben doce estados. Espejo en pipeline/mapping.py::DISPLAY_GROUPS.';

-- -----------------------------------------------------------------------------
-- 2. What Dropi writes
-- -----------------------------------------------------------------------------
INSERT INTO core.status_alias (platform_code, alias_norm, status_code) VALUES
    ('dropi', 'incidencia en ruta',    'delivery_issue'),
    ('dropi', 'incidencia',            'delivery_issue'),
    ('dropi', 'en ruta',               'in_transit'),
    ('dropi', 'entregada',             'delivered'),
    ('dropi', 'devolucion a origen',   'returning'),
    ('dropi', 'devuelto a origen',     'returned'),
    ('dropi', 'devuelta',              'returned'),
    ('dropi', 'cancelada',             'cancelled'),
    ('dropi', 'generada',              'created'),
    ('dropi', 'confirmado',            'confirmed'),
    ('dropi', 'confirmada',            'confirmed'),
    ('dropi', 'recogido',              'picked_up'),
    ('dropi', 'en distribucion',       'out_for_delivery'),
    ('dropi', 'extraviado',            'lost'),
    -- Seen on an Effi export too; costs nothing to know it on both sides.
    ('effi',  'incidencia en ruta',    'delivery_issue')
ON CONFLICT (platform_code, alias_norm) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. The platform on every guide
-- -----------------------------------------------------------------------------
-- NULL platform = "todas". Written as a function so 041 adds ONE line to each
-- of thirteen functions instead of a subquery with a fresh alias in each.
CREATE OR REPLACE FUNCTION mart.f_platform_matches(p_connection_id uuid, p_platform text)
RETURNS boolean
LANGUAGE sql
STABLE
AS $fn$
    SELECT p_platform IS NULL
        OR EXISTS (
            SELECT 1 FROM core.connection c
            WHERE c.id = p_connection_id
              AND c.platform_code = lower(p_platform)
        );
$fn$;

COMMENT ON FUNCTION mart.f_platform_matches IS
    'TRUE cuando la guía entró por una conexión de esa plataforma, o cuando no
     se pidió plataforma (NULL = todas). Es el filtro que 041 agrega a cada f_*.';

-- Same body as 023, plus the two columns at the very end.
CREATE OR REPLACE VIEW stg.v_shipment_economics AS
 SELECT s.id AS shipment_id,
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
    sc.sort_order AS status_sort_order,
    s.declared_value,
        CASE
            WHEN s.delivered_at IS NOT NULL AND s.delivered_at::date >= s.created_date THEN s.delivered_at::date - s.created_date
            ELSE NULL::integer
        END AS days_to_deliver,
        CASE
            WHEN NOT sc.is_terminal THEN CURRENT_DATE - s.created_date
            ELSE NULL::integer
        END AS days_open,
    COALESCE(mv.revenue_amount,
        CASE
            WHEN sc.is_delivered THEN COALESCE(s.cod_collected, s.declared_value)
            ELSE NULL::numeric
        END, 0::numeric)::numeric(14,2) AS revenue_amount,
    COALESCE(mv.freight_amount, COALESCE(s.freight_cost, 0::numeric) + COALESCE(s.return_freight_cost, 0::numeric), 0::numeric)::numeric(14,2) AS freight_amount,
    COALESCE(mv.cogs_amount, NULLIF(s.product_cost, 0::numeric), s.distributor_cost_total,
        CASE
            WHEN sc.is_delivered THEN p.unit_cost * s.quantity::numeric
            ELSE NULL::numeric
        END, 0::numeric)::numeric(14,2) AS cogs_amount,
    COALESCE(mv.fee_amount, COALESCE(s.platform_fee, 0::numeric), 0::numeric)::numeric(14,2) AS fee_amount,
    COALESCE(mv.adjustment_amount, 0::numeric)::numeric(14,2) AS adjustment_amount,
    COALESCE(mv.movement_count, 0::bigint) AS movement_count,
    s.carrier_tracking_number,
    s.settled_at,
        CASE
            WHEN s.settled_at IS NOT NULL THEN s.settled_at::date - s.created_date
            ELSE NULL::integer
        END AS days_to_cash,
    -- Appended by 040. Which platform loaded the guide, through its connection.
    cn.platform_code,
    pl.name AS platform_name
   FROM core.shipment s
     JOIN core.status_canon sc ON sc.code = s.status_code
     LEFT JOIN stg.v_movement_by_shipment mv ON mv.shipment_id = s.id
     LEFT JOIN core.product p ON p.id = s.product_id
     LEFT JOIN core.connection cn ON cn.id = s.connection_id
     LEFT JOIN core.platform pl ON pl.code = cn.platform_code;

-- -----------------------------------------------------------------------------
-- 4a. Day x platform x status group - the table in the report
-- -----------------------------------------------------------------------------
-- The function takes the range and the date. `day` IS the chosen date: with
-- `entrega` the table reads "entregadas por día de entrega", which is a
-- different and equally valid report. A guide without that date has no day to
-- sit on and is left out - f_excluded_no_date counts it.
CREATE OR REPLACE FUNCTION mart.f_daily_status(
    p_date_from  date DEFAULT NULL,
    p_date_to    date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion',
    p_platform   text DEFAULT NULL
)
RETURNS TABLE (
    tenant_id                uuid,
    country_code             char(2),
    platform_code            text,
    platform_name            text,
    day                      date,
    shipments                bigint,
    entregada                bigint,
    devolucion               bigint,
    en_camino                bigint,
    novedad                  bigint,
    muerta                   bigint,
    cerradas                 bigint,
    pct_entrega_cerradas     numeric,
    pct_devolucion_cerradas  numeric,
    pct_devolucion_total     numeric,
    sample_quality           text,
    declared_value           numeric,
    revenue                  numeric,
    contribution             numeric,
    currency_code            char(3)
)
LANGUAGE sql
STABLE
AS $fn$
    WITH base AS (
        SELECT
            e.tenant_id,
            e.country_code,
            COALESCE(e.platform_code, 'sin_plataforma')             AS platform_code,
            COALESCE(e.platform_name, 'Sin plataforma')             AS platform_name,
            mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) AS day,
            sc.display_group,
            e.is_terminal,
            e.declared_value,
            e.revenue_amount,
            e.freight_amount,
            e.cogs_amount,
            e.fee_amount,
            e.currency_code
        FROM stg.v_shipment_economics e
        JOIN core.status_canon sc ON sc.code = e.status_code
        WHERE e.tenant_id = core.current_tenant_id()
          AND mart.f_platform_matches(e.connection_id, p_platform)
          AND mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) IS NOT NULL
          AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
          AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    )
    SELECT
        b.tenant_id,
        b.country_code,
        b.platform_code,
        b.platform_name,
        b.day,
        count(*),
        count(*) FILTER (WHERE b.display_group = 'entregada'),
        count(*) FILTER (WHERE b.display_group = 'devolucion'),
        count(*) FILTER (WHERE b.display_group = 'en_camino'),
        count(*) FILTER (WHERE b.display_group = 'novedad'),
        count(*) FILTER (WHERE b.display_group = 'muerta'),
        count(*) FILTER (WHERE b.is_terminal),
        round(count(*) FILTER (WHERE b.display_group = 'entregada')::numeric
              / NULLIF(count(*) FILTER (WHERE b.is_terminal), 0) * 100, 2),
        round(count(*) FILTER (WHERE b.display_group = 'devolucion')::numeric
              / NULLIF(count(*) FILTER (WHERE b.is_terminal), 0) * 100, 2),
        round(count(*) FILTER (WHERE b.display_group = 'devolucion')::numeric
              / NULLIF(count(*), 0) * 100, 2),
        CASE WHEN count(*) FILTER (WHERE b.is_terminal) < 10
             THEN 'muestra_corta' ELSE 'suficiente' END,
        sum(b.declared_value)::numeric(14, 2),
        sum(b.revenue_amount)::numeric(14, 2),
        (sum(b.revenue_amount) - sum(b.freight_amount) - sum(b.cogs_amount)
            - sum(b.fee_amount))::numeric(14, 2),
        min(b.currency_code)
    FROM base b
    GROUP BY b.tenant_id, b.country_code, b.platform_code, b.platform_name, b.day;
$fn$;

COMMENT ON FUNCTION mart.f_daily_status IS
    'Guías por día, plataforma y grupo de estado. pct_devolucion_total divide por
     todas las guías del día (como el informe manual); pct_devolucion_cerradas
     divide por las ya resueltas, que es la cifra que se va a cumplir.
     sample_quality = muestra_corta con menos de 10 cerradas.';

-- The copilot's version: no range, on the creation date. Must agree with
-- f_daily_status(NULL, NULL, 'creacion', NULL) column for column - the
-- parity test in tests/test_platform_filter.py holds them together.
--
-- Written out in full rather than as SELECT * FROM the function, on purpose:
-- a mart view that calls a mart function stops running as its owner and the
-- read-only copilot role loses it (header of migration 023, and the
-- structural test in test_kpi_date_filters). Same body, no call.
CREATE OR REPLACE VIEW mart.v_daily_status_by_platform AS
WITH base AS (
    SELECT
        e.tenant_id,
        e.country_code,
        COALESCE(e.platform_code, 'sin_plataforma')     AS platform_code,
        COALESCE(e.platform_name, 'Sin plataforma')     AS platform_name,
        e.created_date                                  AS day,
        sc.display_group,
        e.is_terminal,
        e.declared_value,
        e.revenue_amount,
        e.freight_amount,
        e.cogs_amount,
        e.fee_amount,
        e.currency_code
    FROM stg.v_shipment_economics e
    JOIN core.status_canon sc ON sc.code = e.status_code
    WHERE e.tenant_id = core.current_tenant_id()
)
SELECT
    b.tenant_id,
    b.country_code,
    b.platform_code,
    b.platform_name,
    b.day,
    count(*)                                                    AS shipments,
    count(*) FILTER (WHERE b.display_group = 'entregada')       AS entregada,
    count(*) FILTER (WHERE b.display_group = 'devolucion')      AS devolucion,
    count(*) FILTER (WHERE b.display_group = 'en_camino')       AS en_camino,
    count(*) FILTER (WHERE b.display_group = 'novedad')         AS novedad,
    count(*) FILTER (WHERE b.display_group = 'muerta')          AS muerta,
    count(*) FILTER (WHERE b.is_terminal)                       AS cerradas,
    round(count(*) FILTER (WHERE b.display_group = 'entregada')::numeric
          / NULLIF(count(*) FILTER (WHERE b.is_terminal), 0) * 100, 2) AS pct_entrega_cerradas,
    round(count(*) FILTER (WHERE b.display_group = 'devolucion')::numeric
          / NULLIF(count(*) FILTER (WHERE b.is_terminal), 0) * 100, 2) AS pct_devolucion_cerradas,
    round(count(*) FILTER (WHERE b.display_group = 'devolucion')::numeric
          / NULLIF(count(*), 0) * 100, 2)                       AS pct_devolucion_total,
    CASE WHEN count(*) FILTER (WHERE b.is_terminal) < 10
         THEN 'muestra_corta' ELSE 'suficiente' END             AS sample_quality,
    sum(b.declared_value)::numeric(14, 2)                       AS declared_value,
    sum(b.revenue_amount)::numeric(14, 2)                       AS revenue,
    (sum(b.revenue_amount) - sum(b.freight_amount) - sum(b.cogs_amount)
        - sum(b.fee_amount))::numeric(14, 2)                    AS contribution,
    min(b.currency_code)                                        AS currency_code
FROM base b
GROUP BY b.tenant_id, b.country_code, b.platform_code, b.platform_name, b.day;

COMMENT ON VIEW mart.v_daily_status_by_platform IS
    'Resumen diario por estados y plataforma (Effi, Dropi...), sobre la fecha de
     creación de la guía. La versión con rango es mart.f_daily_status.';

-- -----------------------------------------------------------------------------
-- 4b. One row per platform - the strip at the bottom of the report
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION mart.f_platform_summary(
    p_date_from  date DEFAULT NULL,
    p_date_to    date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion',
    p_platform   text DEFAULT NULL
)
RETURNS TABLE (
    tenant_id                uuid,
    country_code             char(2),
    platform_code            text,
    platform_name            text,
    shipments                bigint,
    entregada                bigint,
    devolucion               bigint,
    en_camino                bigint,
    novedad                  bigint,
    muerta                   bigint,
    cerradas                 bigint,
    pct_entrega_cerradas     numeric,
    pct_devolucion_cerradas  numeric,
    pct_devolucion_total     numeric,
    share_pct                numeric,
    sample_quality           text,
    declared_value           numeric,
    revenue                  numeric,
    contribution             numeric,
    currency_code            char(3),
    first_day                date,
    last_day                 date
)
LANGUAGE sql
STABLE
AS $fn$
    WITH per_platform AS (
        SELECT
            d.tenant_id,
            d.country_code,
            d.platform_code,
            d.platform_name,
            sum(d.shipments)      AS shipments,
            sum(d.entregada)      AS entregada,
            sum(d.devolucion)     AS devolucion,
            sum(d.en_camino)      AS en_camino,
            sum(d.novedad)        AS novedad,
            sum(d.muerta)         AS muerta,
            sum(d.cerradas)       AS cerradas,
            sum(d.declared_value) AS declared_value,
            sum(d.revenue)        AS revenue,
            sum(d.contribution)   AS contribution,
            min(d.currency_code)  AS currency_code,
            min(d.day)            AS first_day,
            max(d.day)            AS last_day
        FROM mart.f_daily_status(p_date_from, p_date_to, p_date_field, p_platform) d
        GROUP BY d.tenant_id, d.country_code, d.platform_code, d.platform_name
    )
    SELECT
        pp.tenant_id,
        pp.country_code,
        pp.platform_code,
        pp.platform_name,
        pp.shipments,
        pp.entregada,
        pp.devolucion,
        pp.en_camino,
        pp.novedad,
        pp.muerta,
        pp.cerradas,
        round(pp.entregada::numeric  / NULLIF(pp.cerradas, 0) * 100, 2),
        round(pp.devolucion::numeric / NULLIF(pp.cerradas, 0) * 100, 2),
        round(pp.devolucion::numeric / NULLIF(pp.shipments, 0) * 100, 2),
        -- Share of the country's guides in the range. With p_platform set it
        -- is 100 by construction; the API calls this without one on purpose.
        round(pp.shipments::numeric
              / NULLIF(sum(pp.shipments) OVER (PARTITION BY pp.tenant_id, pp.country_code), 0)
              * 100, 1),
        CASE WHEN pp.cerradas < 10 THEN 'muestra_corta' ELSE 'suficiente' END,
        pp.declared_value::numeric(14, 2),
        pp.revenue::numeric(14, 2),
        pp.contribution::numeric(14, 2),
        pp.currency_code,
        pp.first_day,
        pp.last_day
    FROM per_platform pp;
$fn$;

COMMENT ON FUNCTION mart.f_platform_summary IS
    'Una fila por plataforma: guías, grupos de estado, porcentajes y share_pct
     (qué parte de las guías del país entró por esa plataforma).';

-- Stand-alone for the same reason as the daily view above: it reads the daily
-- VIEW, never the function, so the copilot keeps it.
CREATE OR REPLACE VIEW mart.v_platform_summary AS
WITH per_platform AS (
    SELECT
        d.tenant_id,
        d.country_code,
        d.platform_code,
        d.platform_name,
        sum(d.shipments)      AS shipments,
        sum(d.entregada)      AS entregada,
        sum(d.devolucion)     AS devolucion,
        sum(d.en_camino)      AS en_camino,
        sum(d.novedad)        AS novedad,
        sum(d.muerta)         AS muerta,
        sum(d.cerradas)       AS cerradas,
        sum(d.declared_value) AS declared_value,
        sum(d.revenue)        AS revenue,
        sum(d.contribution)   AS contribution,
        min(d.currency_code)  AS currency_code,
        min(d.day)            AS first_day,
        max(d.day)            AS last_day
    FROM mart.v_daily_status_by_platform d
    GROUP BY d.tenant_id, d.country_code, d.platform_code, d.platform_name
)
SELECT
    pp.tenant_id,
    pp.country_code,
    pp.platform_code,
    pp.platform_name,
    pp.shipments,
    pp.entregada,
    pp.devolucion,
    pp.en_camino,
    pp.novedad,
    pp.muerta,
    pp.cerradas,
    round(pp.entregada::numeric  / NULLIF(pp.cerradas, 0) * 100, 2)   AS pct_entrega_cerradas,
    round(pp.devolucion::numeric / NULLIF(pp.cerradas, 0) * 100, 2)   AS pct_devolucion_cerradas,
    round(pp.devolucion::numeric / NULLIF(pp.shipments, 0) * 100, 2)  AS pct_devolucion_total,
    round(pp.shipments::numeric
          / NULLIF(sum(pp.shipments) OVER (PARTITION BY pp.tenant_id, pp.country_code), 0)
          * 100, 1)                                                   AS share_pct,
    CASE WHEN pp.cerradas < 10 THEN 'muestra_corta' ELSE 'suficiente' END AS sample_quality,
    pp.declared_value::numeric(14, 2)                                 AS declared_value,
    pp.revenue::numeric(14, 2)                                        AS revenue,
    pp.contribution::numeric(14, 2)                                   AS contribution,
    pp.currency_code,
    pp.first_day,
    pp.last_day
FROM per_platform pp;

COMMENT ON VIEW mart.v_platform_summary IS
    'Consolidado por plataforma (Effi vs. Dropi vs. carga manual) de todo el
     histórico. share_pct dice qué parte de las guías del país entró por cada una.';

-- -----------------------------------------------------------------------------
-- The copilot may read both. Same reasoning as 025: aggregates, no people.
-- -----------------------------------------------------------------------------
GRANT SELECT ON mart.v_daily_status_by_platform, mart.v_platform_summary TO norte_readonly;

-- -----------------------------------------------------------------------------
-- Two widgets, both on the logistics tab, above the carrier table
-- -----------------------------------------------------------------------------
INSERT INTO core.widget_catalog
    (widget_code, tab, title, description, required_domains, optional_domains, blocked_message, sort_order) VALUES
    ('platform_split', 'logistica', 'Plataformas',
     'Effi, Dropi y carga manual lado a lado: guías, entregas, devoluciones y qué parte del volumen lleva cada una.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Necesitas al menos una conexión de guías.', 3),

    ('daily_status_table', 'logistica', 'Resumen diario por estados',
     'Cada día con sus guías entregadas, devueltas, en camino y con novedad, por plataforma.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Necesitas al menos una conexión de guías.', 5)
ON CONFLICT (widget_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Dropi in the catalogue: honest about what works today
-- -----------------------------------------------------------------------------
UPDATE core.platform SET
    setup_hint = 'Exporta el reporte de órdenes desde Dropi (CSV o Excel) y súbelo a esta '
                 'conexión. Las guías de Dropi quedan separadas de las de Effi en el tablero.'
WHERE code = 'dropi';
