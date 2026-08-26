-- =============================================================================
-- 054 - El % de devolución diario cuenta TODO lo no-entregado, no solo devueltas
-- =============================================================================
--
-- QUÉ CAMBIA. `mart.f_daily_status.pct_devolucion_total` medía
-- `devolucion / total`, así que un día con muchas guías en tránsito o en novedad
-- pero cero devoluciones marcaba 0% - y el comerciante veía un tablero que decía
-- "no hay problema" cuando media operación estaba sin entregar.
--
-- Regla nueva (decisión de Alexander, 2026-08-26): la devolución del día es TODO
-- lo que no sea "entregada", EXCEPTO la indemnización (el transportador ya pagó
-- la guía perdida, no es una pérdida de venta). Sobre la base de TODAS las guías
-- del día. Así cada día mide, aunque las devoluciones formales sean 0.
--
--   pct_devolucion_total = (guías - entregadas - indemnizadas) / guías * 100
--
-- `pct_entrega_cerradas` y `pct_devolucion_cerradas` (sobre cerradas) no cambian:
-- son la otra lectura, sobre lo ya resuelto.
--
-- OJO - DERIVA. Esta función se redefine sobre la forma que está DESPLEGADA en
-- Supabase, que usa `core.status_canon.status_group` con el grupo dedicado
-- `indemnizacion`. La 040 del repo quedó divergente (usa `display_group`); esta
-- migración es el estado correcto y vigente. Ver la nota de deriva de checksums.
--
-- Depende de: 040 (mart.f_daily_status, f_pick_date, f_platform_matches).
-- Idempotente.
-- =============================================================================

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
        -- El cambio: todo lo que no sea entregada ni indemnización, sobre el total.
        round(count(*) FILTER (WHERE b.status_group NOT IN ('entregada','indemnizacion'))::numeric
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
