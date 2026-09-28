-- =============================================================================
-- Data Effi - 072 - La economía por guía se calcula solo para la empresa que
--                   pregunta, no para toda la base.
--
-- QUÉ PASABA (encontrado en la prueba de carga, 208k guías en 3 empresas)
--
-- stg.v_movement_by_shipment agrupa core.movement por guía, y
-- stg.v_shipment_economics la une a core.shipment. Ninguna de las dos filtraba
-- por empresa: las vistas son de `norte` (dueño del esquema), así que se
-- ejecutan con sus permisos y la seguridad por fila NO se aplica dentro de
-- ellas. El filtro por empresa lo ponían las vistas y funciones de mart.* por
-- ENCIMA - correcto en el resultado, pero PostgreSQL no puede empujar ese
-- filtro dentro de un GROUP BY, y cada consulta del tablero:
--
--     Seq Scan on movement   rows=332066   (todas las empresas)
--     HashAggregate          rows=208802   (todas las guías, a disco)
--     Seq Scan on shipment   rows=208802
--
-- para quedarse después con las ~75k de un país de una empresa. Cada tarjeta
-- del tablero costaba 1-3 s y crecía con el tamaño de la base ENTERA, no con el
-- de la empresa: el cliente número cien pagaba por los otros noventa y nueve.
--
-- QUÉ CAMBIA
--
-- Las dos vistas filtran por core.current_tenant_id(), el mismo valor que ya
-- usan TODAS las vistas y funciones de mart.* que las leen (se comprobó en
-- pg_depend: las quince vistas y nueve funciones que dependen de estas dos
-- filtran por empresa). El resultado visible es idéntico; solo se deja de
-- calcular lo que después se tiraba. Sin empresa fijada devuelven cero filas,
-- exactamente como ya hacía todo lo que está encima.
--
-- Más un índice para la agregación de movimientos de UNA empresa, con las dos
-- columnas que suma, para que sea un index-only scan.
--
-- Idempotente: CREATE OR REPLACE VIEW (mismas columnas, mismo orden) e
-- IF NOT EXISTS. No toca el esquema public.
-- =============================================================================

CREATE OR REPLACE VIEW stg.v_movement_by_shipment AS
SELECT
    m.shipment_id,
    -- Igual a m.tenant_id: la vista ya solo ve esta empresa. Sacarlo del GROUP
    -- BY deja la vista agrupada solo por shipment_id, y eso le prueba a
    -- PostgreSQL que es única por guía: una consulta que no usa ninguna columna
    -- de dinero (un count(*), las cohortes, la antigüedad) ya ni la calcula
    -- (join removal del LEFT JOIN en stg.v_shipment_economics).
    core.current_tenant_id() AS tenant_id,
    sum(m.amount) FILTER (WHERE mt.category = 'revenue')    AS revenue_amount,
    sum(m.amount) FILTER (WHERE mt.category = 'freight')    AS freight_amount,
    sum(m.amount) FILTER (WHERE mt.category = 'cogs')       AS cogs_amount,
    sum(m.amount) FILTER (WHERE mt.category = 'fee')        AS fee_amount,
    sum(m.amount * mt.sign) FILTER (WHERE mt.category = 'adjustment') AS adjustment_amount,
    count(*) FILTER (WHERE mt.category <> 'transfer')       AS movement_count,
    sum(m.amount) FILTER (WHERE mt.category = 'transfer')   AS transfer_amount
FROM core.movement m
JOIN core.movement_type mt ON mt.code = m.movement_type_code
WHERE m.shipment_id IS NOT NULL
  AND m.tenant_id = core.current_tenant_id()
GROUP BY m.shipment_id;

CREATE OR REPLACE VIEW stg.v_shipment_economics AS
 SELECT s.id AS shipment_id, s.tenant_id, s.connection_id, s.country_code,
    s.store_id, s.tracking_number, s.created_date, s.delivered_at, s.returned_at,
    s.dispatched_at, s.carrier_id, s.geo_id, s.product_id, s.quantity,
    s.currency_code, s.status_code, sc.bucket, sc.is_terminal, sc.is_delivered,
    sc.is_returned, sc.sort_order AS status_sort_order, s.declared_value,
        CASE WHEN s.delivered_at IS NOT NULL AND s.delivered_at::date >= s.created_date
             THEN s.delivered_at::date - s.created_date ELSE NULL::integer END AS days_to_deliver,
        CASE WHEN NOT sc.is_terminal THEN CURRENT_DATE - s.created_date
             ELSE NULL::integer END AS days_open,
    COALESCE(mv.revenue_amount,
        CASE WHEN sc.is_delivered THEN COALESCE(s.cod_collected, s.declared_value)
             ELSE NULL::numeric END, 0::numeric)::numeric(14,2) AS revenue_amount,
    COALESCE(mv.freight_amount, COALESCE(s.freight_cost, 0::numeric) + COALESCE(s.return_freight_cost, 0::numeric), 0::numeric)::numeric(14,2) AS freight_amount,
    COALESCE(mv.cogs_amount, NULLIF(s.product_cost, 0::numeric), s.distributor_cost_total,
        CASE WHEN sc.is_delivered THEN p.unit_cost * s.quantity::numeric
             ELSE NULL::numeric END, 0::numeric)::numeric(14,2) AS cogs_amount,
    COALESCE(mv.fee_amount, COALESCE(s.platform_fee, 0::numeric), 0::numeric)::numeric(14,2) AS fee_amount,
    COALESCE(mv.adjustment_amount, 0::numeric)::numeric(14,2) AS adjustment_amount,
    COALESCE(mv.movement_count, 0::bigint) AS movement_count,
    s.carrier_tracking_number, s.settled_at,
        CASE WHEN s.settled_at IS NOT NULL THEN s.settled_at::date - s.created_date
             ELSE NULL::integer END AS days_to_cash,
    cn.platform_code, pl.name AS platform_name
   FROM core.shipment s
     JOIN core.status_canon sc ON sc.code = s.status_code
     LEFT JOIN stg.v_movement_by_shipment mv ON mv.shipment_id = s.id
     LEFT JOIN core.product p ON p.id = s.product_id
     LEFT JOIN core.connection cn ON cn.id = s.connection_id
     LEFT JOIN core.platform pl ON pl.code = cn.platform_code
  WHERE s.tenant_id = core.current_tenant_id()
    -- El filtro global de estados (migración 056).
    AND (core.current_status_groups() IS NULL
         OR sc.status_group = ANY (core.current_status_groups()));

CREATE INDEX IF NOT EXISTS ix_movement_tenant_shipment
    ON core.movement (tenant_id, shipment_id)
    INCLUDE (movement_type_code, amount)
    WHERE shipment_id IS NOT NULL;
