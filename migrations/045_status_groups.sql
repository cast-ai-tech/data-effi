-- =============================================================================
-- Data Effi - 045 - The five words the operator reads: entregado, en tránsito,
--                   novedad, devolución, indemnización.
--
-- WHAT THE OPERATOR ASKED FOR. The screen bundled everything that was not
-- delivered under one "en calle" / "en ruta". The operator needs the real
-- picture in five groups: Entregado, En tránsito, Novedad, Devolución and
-- Indemnización - the last one being the money the carrier pays back when it
-- loses a parcel. Migration 040 already had a grouping column
-- (`display_group`: entregada | devolucion | en_camino | novedad | muerta),
-- but only two views read it and its vocabulary was the old one. This
-- migration makes the column THE source of truth and gives it the operator's
-- words.
--
-- FOUR THINGS LAND HERE.
--
-- 1. `core.status_canon.status_group` (renamed from display_group). Five
--    values, every canonical status maps to exactly one:
--        entregada      delivered
--        devolucion     returning, returned, cancelled
--        en_transito    created, confirmed, picked_up, in_transit, out_for_delivery
--        novedad        in_office, delivery_issue
--        indemnizacion  lost, compensated
--    `cancelled` moves from the old "muerta" into devolución on the
--    operator's decision: the sale is lost and the product is back (or never
--    left), which is how the sheet reads it. `pct_devolucion` therefore
--    counts cancellations - a Dropi "Cancelado" by the seller included.
--
-- 2. A NEW CANONICAL STATUS: `compensated` ("Indemnizada"), sort 96,
--    terminal. A `lost` guide (siniestro, extravío) is one the carrier still
--    owes; a compensated one is the same parcel with the money paid back.
--    Effi's wallet already has an "Indemnización" movement
--    (pipeline/profiles.py, adjustment_in); this is the guide-side word.
--    THE ALIASES BELOW ARE CANDIDATES: no real export with the word has been
--    seen yet. `resolve_status` flags any spelling that is not here, so the
--    first file that carries it will name the missing alias in its report.
--
-- 3. ONE EXCEPTION IN `core.status_advance`: terminal statuses are frozen, and
--    `lost` is terminal, so without it a guide could never become
--    compensated. `lost -> compensated` is a forward step, not a regression,
--    and it is the only terminal-to-terminal move allowed. Mirror in
--    pipeline/ingest.py::merge_shipment.
--
-- 4. THE OBJECTS THAT SHOW THE GROUPS. `f_daily_status` /
--    `v_daily_status_by_platform` and `f_platform_summary` /
--    `v_platform_summary` are dropped and recreated (a column rename cannot go
--    through CREATE OR REPLACE): en_camino -> en_transito, muerta ->
--    indemnizacion. `v_orders` gains `status_group` at the END so the orders
--    screen can filter by the five words instead of the four buckets.
--    `v_global_summary` / `f_global_summary` gain the five counts at the END
--    so the multi-country tile stops adding novedades into "en tránsito".
--    Every view stays stand-alone (no f_* call inside a view: header of 023,
--    structural test in test_kpi_date_filters).
--
-- Depends on: 044. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. status_group: one column, the operator's words
-- -----------------------------------------------------------------------------
DO $do$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'core' AND table_name = 'status_canon'
          AND column_name = 'display_group'
    ) THEN
        ALTER TABLE core.status_canon RENAME COLUMN display_group TO status_group;
    END IF;
END
$do$;

ALTER TABLE core.status_canon
    DROP CONSTRAINT IF EXISTS status_canon_display_group_check,
    DROP CONSTRAINT IF EXISTS status_canon_status_group_check;

ALTER TABLE core.status_canon
    ALTER COLUMN status_group SET DEFAULT 'en_transito';

-- 2. The new canonical status, before the mapping so it gets its group too.
INSERT INTO core.status_canon
    (code, label, sort_order, is_terminal, is_delivered, is_returned, bucket, status_group)
VALUES
    ('compensated', 'Indemnizada', 96, true, false, false, 'dead', 'indemnizacion')
ON CONFLICT (code) DO NOTHING;

UPDATE core.status_canon SET status_group = CASE code
    WHEN 'delivered'        THEN 'entregada'
    WHEN 'returning'        THEN 'devolucion'
    WHEN 'returned'         THEN 'devolucion'
    WHEN 'cancelled'        THEN 'devolucion'
    WHEN 'in_office'        THEN 'novedad'
    WHEN 'delivery_issue'   THEN 'novedad'
    WHEN 'lost'             THEN 'indemnizacion'
    WHEN 'compensated'      THEN 'indemnizacion'
    ELSE 'en_transito'
END;

ALTER TABLE core.status_canon
    ADD CONSTRAINT status_canon_status_group_check
    CHECK (status_group IN ('entregada', 'devolucion', 'en_transito', 'novedad', 'indemnizacion'));

COMMENT ON COLUMN core.status_canon.status_group IS
    'Los cinco grupos que lee el operador: entregada | devolucion | en_transito |
     novedad | indemnizacion. Fuente de verdad de la agrupación; cada canónico
     cae en exactamente uno. Nunca reemplaza al código canónico. Espejos:
     pipeline/mapping.py::STATUS_GROUPS y web/lib/status.ts.';

-- Candidate spellings for the compensated guide (see header, point 2).
INSERT INTO core.status_alias (platform_code, alias_norm, status_code) VALUES
    ('effi',  'indemnizada',                    'compensated'),
    ('effi',  'indemnizado',                    'compensated'),
    ('effi',  'indemnizacion',                  'compensated'),
    ('effi',  'guia indemnizada',               'compensated'),
    ('effi',  'siniestro indemnizado',          'compensated'),
    ('effi',  'indemnizada por transportadora', 'compensated'),
    ('dropi', 'indemnizada',                    'compensated'),
    ('dropi', 'indemnizado',                    'compensated'),
    ('dropi', 'indemnizacion',                  'compensated'),
    ('dropi', 'guia indemnizada',               'compensated')
ON CONFLICT (platform_code, alias_norm) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. status_advance: forward only, terminal frozen - except lost -> compensated
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.status_advance(p_current text, p_incoming text)
RETURNS text
LANGUAGE sql
STABLE
AS $fn$
    SELECT CASE
        WHEN p_current IS NULL THEN p_incoming
        WHEN p_incoming IS NULL THEN p_current
        -- The one terminal-to-terminal step: a lost parcel gets paid back.
        WHEN p_current = 'lost' AND p_incoming = 'compensated' THEN p_incoming
        WHEN (SELECT is_terminal FROM core.status_canon WHERE code = p_current) THEN p_current
        WHEN (SELECT sort_order FROM core.status_canon WHERE code = p_incoming)
           > (SELECT sort_order FROM core.status_canon WHERE code = p_current) THEN p_incoming
        ELSE p_current
    END;
$fn$;

COMMENT ON FUNCTION core.status_advance IS
    'Mirror of merge_shipment status rule: forward only, terminal is frozen.
     Única excepción (045): lost -> compensated, la indemnización de un siniestro.';

-- -----------------------------------------------------------------------------
-- 4a. Day x platform x status group, with the five words as columns
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS mart.v_platform_summary;
DROP VIEW IF EXISTS mart.v_daily_status_by_platform;
DROP FUNCTION IF EXISTS mart.f_platform_summary(date, date, text, text);
DROP FUNCTION IF EXISTS mart.f_daily_status(date, date, text, text);

CREATE FUNCTION mart.f_daily_status(
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
    en_transito              bigint,
    novedad                  bigint,
    indemnizacion            bigint,
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
            sc.status_group,
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
        count(*) FILTER (WHERE b.status_group = 'entregada'),
        count(*) FILTER (WHERE b.status_group = 'devolucion'),
        count(*) FILTER (WHERE b.status_group = 'en_transito'),
        count(*) FILTER (WHERE b.status_group = 'novedad'),
        count(*) FILTER (WHERE b.status_group = 'indemnizacion'),
        count(*) FILTER (WHERE b.is_terminal),
        round(count(*) FILTER (WHERE b.status_group = 'entregada')::numeric
              / NULLIF(count(*) FILTER (WHERE b.is_terminal), 0) * 100, 2),
        round(count(*) FILTER (WHERE b.status_group = 'devolucion')::numeric
              / NULLIF(count(*) FILTER (WHERE b.is_terminal), 0) * 100, 2),
        round(count(*) FILTER (WHERE b.status_group = 'devolucion')::numeric
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
    'Guías por día, plataforma y grupo de estado (entregada, devolucion,
     en_transito, novedad, indemnizacion). pct_devolucion_total divide por
     todas las guías del día (como el informe manual); pct_devolucion_cerradas
     divide por las ya resueltas. sample_quality = muestra_corta con menos de
     10 cerradas.';

-- Same body, no call: a mart view that calls a mart function stops running as
-- its owner and the copilot's read-only role loses it (header of 023).
CREATE VIEW mart.v_daily_status_by_platform AS
WITH base AS (
    SELECT
        e.tenant_id,
        e.country_code,
        COALESCE(e.platform_code, 'sin_plataforma')     AS platform_code,
        COALESCE(e.platform_name, 'Sin plataforma')     AS platform_name,
        e.created_date                                  AS day,
        sc.status_group,
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
    count(*) FILTER (WHERE b.status_group = 'entregada')        AS entregada,
    count(*) FILTER (WHERE b.status_group = 'devolucion')       AS devolucion,
    count(*) FILTER (WHERE b.status_group = 'en_transito')      AS en_transito,
    count(*) FILTER (WHERE b.status_group = 'novedad')          AS novedad,
    count(*) FILTER (WHERE b.status_group = 'indemnizacion')    AS indemnizacion,
    count(*) FILTER (WHERE b.is_terminal)                       AS cerradas,
    round(count(*) FILTER (WHERE b.status_group = 'entregada')::numeric
          / NULLIF(count(*) FILTER (WHERE b.is_terminal), 0) * 100, 2) AS pct_entrega_cerradas,
    round(count(*) FILTER (WHERE b.status_group = 'devolucion')::numeric
          / NULLIF(count(*) FILTER (WHERE b.is_terminal), 0) * 100, 2) AS pct_devolucion_cerradas,
    round(count(*) FILTER (WHERE b.status_group = 'devolucion')::numeric
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
    'Resumen diario por grupo de estado y plataforma (Effi, Dropi...), sobre la
     fecha de creación de la guía. La versión con rango es mart.f_daily_status.';

-- -----------------------------------------------------------------------------
-- 4b. One row per platform
-- -----------------------------------------------------------------------------
CREATE FUNCTION mart.f_platform_summary(
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
    en_transito              bigint,
    novedad                  bigint,
    indemnizacion            bigint,
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
            sum(d.en_transito)    AS en_transito,
            sum(d.novedad)        AS novedad,
            sum(d.indemnizacion)  AS indemnizacion,
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
        pp.en_transito,
        pp.novedad,
        pp.indemnizacion,
        pp.cerradas,
        round(pp.entregada::numeric  / NULLIF(pp.cerradas, 0) * 100, 2),
        round(pp.devolucion::numeric / NULLIF(pp.cerradas, 0) * 100, 2),
        round(pp.devolucion::numeric / NULLIF(pp.shipments, 0) * 100, 2),
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
    'Una fila por plataforma: guías, los cinco grupos de estado, porcentajes y
     share_pct (qué parte de las guías del país entró por esa plataforma).';

CREATE VIEW mart.v_platform_summary AS
WITH per_platform AS (
    SELECT
        d.tenant_id,
        d.country_code,
        d.platform_code,
        d.platform_name,
        sum(d.shipments)      AS shipments,
        sum(d.entregada)      AS entregada,
        sum(d.devolucion)     AS devolucion,
        sum(d.en_transito)    AS en_transito,
        sum(d.novedad)        AS novedad,
        sum(d.indemnizacion)  AS indemnizacion,
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
    pp.en_transito,
    pp.novedad,
    pp.indemnizacion,
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
     histórico, con los cinco grupos de estado. share_pct dice qué parte de las
     guías del país entró por cada una.';

GRANT SELECT ON mart.v_daily_status_by_platform, mart.v_platform_summary
    TO norte_app, norte_readonly;

-- -----------------------------------------------------------------------------
-- 4c. v_orders: the group on every guide, appended last
-- -----------------------------------------------------------------------------
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
    s.dispatched_at,

    -- Appended by 045: the five words, so the orders screen filters by them.
    sc.status_group
FROM core.shipment s
JOIN core.status_canon sc     ON sc.code = s.status_code
JOIN core.country co          ON co.code = s.country_code
JOIN stg.v_shipment_economics ec ON ec.shipment_id = s.id
LEFT JOIN core.geo g          ON g.id = s.geo_id
LEFT JOIN core.product p      ON p.id = s.product_id
LEFT JOIN core.carrier ca     ON ca.id = s.carrier_id
WHERE s.tenant_id = core.current_tenant_id();

-- -----------------------------------------------------------------------------
-- 4d. Multi-country summary: the five counts, appended last
-- -----------------------------------------------------------------------------
-- `in_transit` (every open guide) stays for the callers that already read it;
-- the five new columns are what the screen shows from now on.
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
        max(e.created_date)                                     AS last_shipment_date,
        count(*) FILTER (WHERE sc.status_group = 'entregada')     AS entregada,
        count(*) FILTER (WHERE sc.status_group = 'devolucion')    AS devolucion,
        count(*) FILTER (WHERE sc.status_group = 'en_transito')   AS en_transito,
        count(*) FILTER (WHERE sc.status_group = 'novedad')       AS novedad,
        count(*) FILTER (WHERE sc.status_group = 'indemnizacion') AS indemnizacion
    FROM stg.v_shipment_economics e
    JOIN core.status_canon sc ON sc.code = e.status_code
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
    pc.last_shipment_date,
    -- Appended by 045.
    pc.entregada,
    pc.devolucion,
    pc.en_transito,
    pc.novedad,
    pc.indemnizacion
FROM per_country pc
JOIN core.country co ON co.code = pc.country_code
LEFT JOIN ads a ON a.tenant_id = pc.tenant_id AND a.country_code = pc.country_code
LEFT JOIN latest_fx fx ON fx.base_currency = pc.currency_code;

-- A function's return type cannot change through CREATE OR REPLACE.
DROP FUNCTION IF EXISTS mart.f_global_summary(date, date, text, text);
CREATE FUNCTION mart.f_global_summary(
    p_date_from  date DEFAULT NULL,
    p_date_to    date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion',
    p_platform   text DEFAULT NULL
)
RETURNS TABLE (
    tenant_id          uuid,
    country_code       char(2),
    country_name       text,
    currency_code      char(3),
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
    last_shipment_date date,
    entregada          bigint,
    devolucion         bigint,
    en_transito        bigint,
    novedad            bigint,
    indemnizacion      bigint
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
            max(e.created_date)                                     AS last_shipment_date,
            count(*) FILTER (WHERE sc.status_group = 'entregada')     AS entregada,
            count(*) FILTER (WHERE sc.status_group = 'devolucion')    AS devolucion,
            count(*) FILTER (WHERE sc.status_group = 'en_transito')   AS en_transito,
            count(*) FILTER (WHERE sc.status_group = 'novedad')       AS novedad,
            count(*) FILTER (WHERE sc.status_group = 'indemnizacion') AS indemnizacion
        FROM stg.v_shipment_economics e
        JOIN core.status_canon sc ON sc.code = e.status_code
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
        pc.last_shipment_date,
        pc.entregada,
        pc.devolucion,
        pc.en_transito,
        pc.novedad,
        pc.indemnizacion
    FROM per_country pc
    JOIN core.country co ON co.code = pc.country_code
    LEFT JOIN ads a ON a.tenant_id = pc.tenant_id AND a.country_code = pc.country_code
    LEFT JOIN latest_fx fx ON fx.base_currency = pc.currency_code;
$fn$;

-- -----------------------------------------------------------------------------
-- The widget describes the five words it shows
-- -----------------------------------------------------------------------------
UPDATE core.widget_catalog
   SET description = 'Cada día con sus guías entregadas, en tránsito, con novedad, '
                     'devueltas e indemnizadas, por plataforma.'
 WHERE widget_code = 'daily_status_table';
