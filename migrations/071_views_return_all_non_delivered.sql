-- =============================================================================
-- Data Effi - 071 - Las VISTAS del resumen diario y por plataforma cuentan la
-- devolución igual que las funciones (todo lo no-entregado salvo indemnización)
-- =============================================================================
--
-- EL DESCUADRE. La 054 cambió `mart.f_daily_status.pct_devolucion_total` y la
-- 055 `mart.f_platform_summary.pct_devolucion_total` a la regla nueva:
--
--   pct_devolucion_total = (guías - entregadas - indemnizadas) / guías * 100
--
-- pero sus gemelas `mart.v_daily_status_by_platform` y `mart.v_platform_summary`
-- (definidas en la 045, "mismo cuerpo, sin llamada") se quedaron en la regla
-- vieja, `devolucion / guías`. El tablero lee las funciones y el copiloto lee
-- las vistas, así que a "¿cuánto devolvió Effi el lunes?" respondían dos números
-- distintos. tests/test_platform_filter.py::test_the_view_and_the_function_agree
-- lo detecta.
--
-- LO QUE CAMBIA. Solo la expresión de `pct_devolucion_total` en las dos vistas;
-- mismas columnas, mismos tipos, mismo orden, por eso basta CREATE OR REPLACE
-- VIEW (conserva dueño y permisos). `pct_entrega_cerradas` y
-- `pct_devolucion_cerradas` no cambian.
--
-- Depende de: 045 (las vistas), 054/055 (la regla). Idempotente.
-- =============================================================================

CREATE OR REPLACE VIEW mart.v_daily_status_by_platform AS
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
    -- El cambio (igual que la 054): todo lo que no sea entregada ni indemnización.
    round(count(*) FILTER (WHERE b.status_group NOT IN ('entregada','indemnizacion'))::numeric
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
    -- El cambio (igual que la 055): todo lo no-entregado salvo indemnización.
    round((pp.shipments - pp.entregada - pp.indemnizacion)::numeric
          / NULLIF(pp.shipments, 0) * 100, 2)                         AS pct_devolucion_total,
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

COMMENT ON VIEW mart.v_daily_status_by_platform IS
    'Resumen diario por grupo de estado y plataforma (Effi, Dropi...), sobre la
     fecha de creación de la guía. pct_devolucion_total = todo lo no-entregado
     salvo indemnización, sobre todas las guías del día (054). La versión con
     rango es mart.f_daily_status.';

COMMENT ON VIEW mart.v_platform_summary IS
    'Consolidado por plataforma (Effi vs. Dropi vs. carga manual) de todo el
     histórico, con los cinco grupos de estado. pct_devolucion_total = todo lo
     no-entregado salvo indemnización (055). share_pct dice qué parte de las
     guías del país entró por cada una.';

GRANT SELECT ON mart.v_daily_status_by_platform, mart.v_platform_summary
    TO norte_app, norte_readonly;
