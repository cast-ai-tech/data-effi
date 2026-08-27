-- =============================================================================
-- 056 - Filtro global de estados (fase 1: resumen diario y plataformas)
-- =============================================================================
--
-- QUÉ HABILITA. Un quinto parámetro, `p_status_groups text[]`, en las funciones
-- del informe. NULL = los cinco grupos, que es como se venía comportando. Con
-- una lista, las guías de los grupos apagados salen del universo ANTES de
-- agregar, así que TODO lo que se calcula después - conteos, porcentajes y
-- dinero - las ignora por completo.
--
-- Es la diferencia entre filtrar y restar: el % de devolución de un universo sin
-- "novedad" es la devolución de ese universo, no el mismo número con otra
-- etiqueta. Eso es lo que pidió el comerciante: "si desactivo un estado en el
-- filtro los números calculan en todas partes sin contar la data de ese estado".
--
-- Los cinco grupos viven en core.status_canon.status_group:
--   en_transito | novedad | entregada | devolucion | indemnizacion
--
-- FASE 1. Solo f_daily_status y f_platform_summary (el resumen diario y la tira
-- de plataformas). Las demás funciones de métrica - transportadoras, geo,
-- productos, aging, cash cycle - siguen con su firma de cuatro y se irán
-- sumando; la API solo manda el quinto argumento a las que ya lo aceptan
-- (`supports_status_filter` en api/routers/kpis.py).
--
-- Se dropean las versiones de cuatro parámetros: con las dos vivas, una llamada
-- de cuatro argumentos queda ambigua y PostgreSQL la rechaza.
--
-- Depende de: 040, 054, 055. Idempotente.
-- =============================================================================

CREATE OR REPLACE FUNCTION mart.f_daily_status(
    p_date_from    date DEFAULT NULL,
    p_date_to      date DEFAULT NULL,
    p_date_field   text DEFAULT 'creacion',
    p_platform     text DEFAULT NULL,
    p_status_groups text[] DEFAULT NULL
)
RETURNS TABLE (
    tenant_id uuid, country_code char(2), platform_code text, platform_name text,
    day date, shipments bigint, entregada bigint, devolucion bigint,
    en_transito bigint, novedad bigint, indemnizacion bigint, cerradas bigint,
    pct_entrega_cerradas numeric, pct_devolucion_cerradas numeric,
    pct_devolucion_total numeric, sample_quality text, declared_value numeric,
    revenue numeric, contribution numeric, currency_code char(3)
)
LANGUAGE sql
STABLE
AS $fn$
    WITH base AS (
        SELECT
            e.tenant_id, e.country_code,
            COALESCE(e.platform_code, 'sin_plataforma') AS platform_code,
            COALESCE(e.platform_name, 'Sin plataforma') AS platform_name,
            mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) AS day,
            sc.status_group, e.is_terminal, e.declared_value, e.revenue_amount,
            e.freight_amount, e.cogs_amount, e.fee_amount, e.currency_code
        FROM stg.v_shipment_economics e
        JOIN core.status_canon sc ON sc.code = e.status_code
        WHERE e.tenant_id = core.current_tenant_id()
          AND mart.f_platform_matches(e.connection_id, p_platform)
          -- El filtro, aplicado antes de cualquier agregación.
          AND (p_status_groups IS NULL OR sc.status_group = ANY(p_status_groups))
          AND mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) IS NOT NULL
          AND (p_date_from IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) >= p_date_from)
          AND (p_date_to   IS NULL OR mart.f_pick_date(p_date_field, e.created_date, e.dispatched_at, e.delivered_at) <= p_date_to)
    )
    SELECT
        b.tenant_id, b.country_code, b.platform_code, b.platform_name, b.day,
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
        round(count(*) FILTER (WHERE b.status_group NOT IN ('entregada','indemnizacion'))::numeric
              / NULLIF(count(*), 0) * 100, 2),
        CASE WHEN count(*) FILTER (WHERE b.is_terminal) < 10 THEN 'muestra_corta' ELSE 'suficiente' END,
        sum(b.declared_value)::numeric(14, 2),
        sum(b.revenue_amount)::numeric(14, 2),
        (sum(b.revenue_amount) - sum(b.freight_amount) - sum(b.cogs_amount) - sum(b.fee_amount))::numeric(14, 2),
        min(b.currency_code)
    FROM base b
    GROUP BY b.tenant_id, b.country_code, b.platform_code, b.platform_name, b.day;
$fn$;

CREATE OR REPLACE FUNCTION mart.f_platform_summary(
    p_date_from    date DEFAULT NULL,
    p_date_to      date DEFAULT NULL,
    p_date_field   text DEFAULT 'creacion',
    p_platform     text DEFAULT NULL,
    p_status_groups text[] DEFAULT NULL
)
RETURNS TABLE (
    tenant_id uuid, country_code char(2), platform_code text, platform_name text,
    shipments bigint, entregada bigint, devolucion bigint, en_transito bigint,
    novedad bigint, indemnizacion bigint, cerradas bigint,
    pct_entrega_cerradas numeric, pct_devolucion_cerradas numeric,
    pct_devolucion_total numeric, share_pct numeric, sample_quality text,
    declared_value numeric, revenue numeric, contribution numeric,
    currency_code char(3), first_day date, last_day date
)
LANGUAGE sql
STABLE
AS $fn$
    WITH per_platform AS (
        SELECT
            d.tenant_id, d.country_code, d.platform_code, d.platform_name,
            sum(d.shipments) AS shipments, sum(d.entregada) AS entregada,
            sum(d.devolucion) AS devolucion, sum(d.en_transito) AS en_transito,
            sum(d.novedad) AS novedad, sum(d.indemnizacion) AS indemnizacion,
            sum(d.cerradas) AS cerradas, sum(d.declared_value) AS declared_value,
            sum(d.revenue) AS revenue, sum(d.contribution) AS contribution,
            min(d.currency_code) AS currency_code,
            min(d.day) AS first_day, max(d.day) AS last_day
        FROM mart.f_daily_status(p_date_from, p_date_to, p_date_field, p_platform, p_status_groups) d
        GROUP BY d.tenant_id, d.country_code, d.platform_code, d.platform_name
    )
    SELECT
        pp.tenant_id, pp.country_code, pp.platform_code, pp.platform_name,
        pp.shipments, pp.entregada, pp.devolucion, pp.en_transito, pp.novedad,
        pp.indemnizacion, pp.cerradas,
        round(pp.entregada::numeric  / NULLIF(pp.cerradas, 0) * 100, 2),
        round(pp.devolucion::numeric / NULLIF(pp.cerradas, 0) * 100, 2),
        round((pp.shipments - pp.entregada - pp.indemnizacion)::numeric / NULLIF(pp.shipments, 0) * 100, 2),
        round(pp.shipments::numeric
              / NULLIF(sum(pp.shipments) OVER (PARTITION BY pp.tenant_id, pp.country_code), 0) * 100, 1),
        CASE WHEN pp.cerradas < 10 THEN 'muestra_corta' ELSE 'suficiente' END,
        pp.declared_value::numeric(14, 2), pp.revenue::numeric(14, 2),
        pp.contribution::numeric(14, 2), pp.currency_code, pp.first_day, pp.last_day
    FROM per_platform pp;
$fn$;

-- Las de cuatro parámetros quedan ambiguas frente a las nuevas.
DROP FUNCTION IF EXISTS mart.f_platform_summary(date, date, text, text);
DROP FUNCTION IF EXISTS mart.f_daily_status(date, date, text, text);
