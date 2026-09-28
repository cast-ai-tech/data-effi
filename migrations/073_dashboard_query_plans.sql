-- =============================================================================
-- Data Effi - 073 - Maduración de cohortes en una sola pasada.
--
-- QUÉ PASABA (prueba de carga, 140k guías en la empresa grande)
--
-- mart.v_cohort_maturation cruzaba cada cohorte con los 31 días de la curva y,
-- para CADA uno de esos 31 pares, volvía a unir todas las guías de la cohorte:
-- cohortes x 31 x guías por día. Con un año de historia eran ~4 millones de
-- filas intermedias para devolver unas 11 mil, y leía stg.v_shipment_economics
-- dos veces. Era la consulta más lenta del tablero: 2,5 s sola, 6 s con carga.
--
-- QUÉ CAMBIA
--
-- Las guías de cada cohorte se cuentan UNA vez por día real de entrega
-- (0..30), y la curva acumulada sale de sumar esos conteos hasta cada día.
-- Mismas columnas, mismos tipos, mismo resultado; lo que baja es el trabajo.
--
-- Idempotente (CREATE OR REPLACE VIEW, mismas columnas en el mismo orden).
-- No toca el esquema public.
-- =============================================================================

CREATE OR REPLACE VIEW mart.v_cohort_maturation AS
WITH base AS NOT MATERIALIZED (
    SELECT e.tenant_id, e.country_code, e.created_date, e.currency_code, e.days_to_deliver
    FROM stg.v_shipment_economics e
    WHERE e.tenant_id = core.current_tenant_id()
), cohorts AS (
    SELECT tenant_id, country_code, created_date AS cohort_date,
           count(*) AS cohort_size, min(currency_code) AS currency_code
    FROM base
    GROUP BY tenant_id, country_code, created_date
), delivered AS (
    -- Cuántas guías de la cohorte llegaron exactamente en el día N.
    SELECT tenant_id, country_code, created_date AS cohort_date,
           days_to_deliver, count(*) AS delivered
    FROM base
    WHERE days_to_deliver <= 30
    GROUP BY tenant_id, country_code, created_date, days_to_deliver
), days AS (
    SELECT generate_series(0, 30) AS days_since
), curve AS (
    SELECT c.tenant_id, c.country_code, c.cohort_date, c.cohort_size, d.days_since,
           COALESCE(sum(x.delivered), 0)::bigint AS delivered_by_day,
           c.currency_code
    FROM cohorts c
    CROSS JOIN days d
    LEFT JOIN delivered x
           ON x.tenant_id = c.tenant_id
          AND x.country_code = c.country_code
          AND x.cohort_date = c.cohort_date
          AND x.days_to_deliver <= d.days_since
    GROUP BY c.tenant_id, c.country_code, c.cohort_date, c.cohort_size, d.days_since,
             c.currency_code
)
SELECT cv.tenant_id,
    cv.country_code,
    cv.cohort_date,
    cv.cohort_size,
    cv.days_since,
    cv.delivered_by_day,
    round(cv.delivered_by_day::numeric / NULLIF(cv.cohort_size, 0)::numeric * 100::numeric, 2)
        AS delivery_rate_pct,
    (cv.cohort_date + cv.days_since) <= CURRENT_DATE AS is_observable,
    (cv.cohort_date + COALESCE(wc.maturation_days::integer, 21)) <= CURRENT_DATE AS is_mature,
    COALESCE(wc.maturation_days::integer, 21) AS maturation_days,
    cv.currency_code
FROM curve cv
LEFT JOIN core.workspace_country wc
       ON wc.tenant_id = cv.tenant_id AND wc.country_code = cv.country_code;
