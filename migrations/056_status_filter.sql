-- =============================================================================
-- 056 - Filtro global de estados, en la puerta por la que pasan todas las métricas
-- =============================================================================
--
-- QUÉ HABILITA. Apagar un estado en el tablero lo saca del universo de TODOS los
-- números - conteos, porcentajes y dinero -, no de una tarjeta suelta. Es lo que
-- pidió el comerciante: "si desactivo un estado en el filtro los números
-- calculan en todas partes sin contar la data de ese estado".
--
-- Filtrar no es restar: el % de devolución de un universo sin "novedad" es la
-- devolución de ESE universo, no el mismo número con otra etiqueta. Por eso el
-- filtro se aplica ANTES de agregar.
--
-- DÓNDE VIVE, Y POR QUÉ AHÍ
-- --------------------------
-- En `stg.v_shipment_economics`, no en cada función. Hay trece funciones de
-- métrica (f_daily_status, f_carrier_effectiveness, f_product_performance,
-- f_aging, f_cash_cycle...) y varias vistas, y TODAS leen de esa vista. Pasarles
-- el filtro una por una es garantizar que alguna se quede fuera y muestre una
-- tarjeta contando un universo distinto al del resto del tablero - exactamente
-- el error que el filtro existe para evitar. Un solo predicado en la vista las
-- cubre a todas, incluidas las que se escriban mañana.
--
-- El vehículo es una variable de sesión, igual que el tenant: la API la fija con
-- `set_config('norte.status_groups', ..., true)` una vez por petición (ver
-- `_apply_status_filter` en api/deps.py). El `true` la hace LOCAL a la
-- transacción, así que una conexión del pool nunca se lleva el filtro de una
-- persona a la petición de otra - la misma garantía que protege `norte.tenant_id`.
--
-- Vacía = los cinco grupos, que es el comportamiento de siempre: quien nunca
-- toque el filtro ve exactamente lo que veía.
--
-- Los cinco grupos viven en core.status_canon.status_group:
--   en_transito | novedad | entregada | devolucion | indemnizacion
--
-- Depende de: 003 (la vista), 045 (status_group). Idempotente.
-- =============================================================================

CREATE OR REPLACE FUNCTION core.current_status_groups()
RETURNS text[]
LANGUAGE sql
STABLE
AS $fn$
    SELECT CASE
        WHEN coalesce(current_setting('norte.status_groups', true), '') = '' THEN NULL
        ELSE string_to_array(current_setting('norte.status_groups', true), ',')
    END;
$fn$;

COMMENT ON FUNCTION core.current_status_groups IS
    'Grupos de estado activos en esta transacción, o NULL para todos. La fija la
     API por petición; la lee stg.v_shipment_economics.';

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
  -- El filtro global. Una línea, y la respetan las trece funciones y las vistas.
  WHERE core.current_status_groups() IS NULL
     OR sc.status_group = ANY (core.current_status_groups());
