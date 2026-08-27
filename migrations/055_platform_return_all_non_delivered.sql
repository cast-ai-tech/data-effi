-- =============================================================================
-- 055 - El % de devolución por plataforma cuenta todo lo no-entregado
-- =============================================================================
--
-- Misma regla que la 054 (f_daily_status), ahora en la tira de plataformas del
-- pie del informe: `mart.f_platform_summary.pct_devolucion_total` pasaba de
-- `devolucion / shipments` (solo el bucket de devueltas) a
-- `(shipments - entregada - indemnizacion) / shipments` - todo lo que no sea
-- entregada, salvo la indemnización, sobre el total.
--
-- Sin esto, la tarjeta de una plataforma decía "4,0% devolución" (144/3645)
-- mientras el resumen diario ya medía la devolución real de la operación, y los
-- dos números no cuadraban.
--
-- `pct_entrega_cerradas` y `pct_devolucion_cerradas` (sobre cerradas) no cambian.
-- Ya aplicada a la base viva por hotfix; esta migración la registra. Se redefine
-- sobre la forma desplegada (status_group / grupo indemnizacion). Ver la nota de
-- deriva de checksums y la 054.
--
-- Depende de: 040 (f_platform_summary, f_daily_status). Idempotente.
-- =============================================================================

CREATE OR REPLACE FUNCTION mart.f_platform_summary(
    p_date_from date DEFAULT NULL,
    p_date_to   date DEFAULT NULL,
    p_date_field text DEFAULT 'creacion',
    p_platform  text DEFAULT NULL
)
RETURNS TABLE (
    tenant_id       uuid,
    country_code    char(2),
    platform_code   text,
    platform_name   text,
    shipments       bigint,
    entregada       bigint,
    devolucion      bigint,
    en_transito     bigint,
    novedad         bigint,
    indemnizacion   bigint,
    cerradas        bigint,
    pct_entrega_cerradas    numeric,
    pct_devolucion_cerradas numeric,
    pct_devolucion_total    numeric,
    share_pct       numeric,
    sample_quality  text,
    declared_value  numeric,
    revenue         numeric,
    contribution    numeric,
    currency_code   char(3),
    first_day       date,
    last_day        date
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
        -- El cambio: todo lo no-entregado salvo indemnización, sobre el total.
        round((pp.shipments - pp.entregada - pp.indemnizacion)::numeric / NULLIF(pp.shipments, 0) * 100, 2),
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
