-- =============================================================================
-- Data Effi - 023 - Two money bugs that cancelled each other out.
--
-- BUG A: insurance and the collection fee were charged twice.
-- `Precio flete total a cliente` ALREADY contains freight base + insurance +
-- collection fee. Verified on the operator's export: freight_cost equals
-- base + insurance + collection in 1,649 of 1,649 guides, and the carrier's own
-- wallet movement charges exactly that total. The view loaded the total into
-- `freight_amount` and then added insurance and collection AGAIN as
-- `fee_amount` - 3,211.51 USD of cost that was never charged, on 20,918.25 of
-- revenue. 15% of income, invented.
--
-- BUG B: the cost of goods was read from the wrong column.
-- `Costo documento de venta` is 0 on 704 guides. What the distributor actually
-- charged lives in `Total compra distribuidor` (`distributor_cost_total`),
-- populated on all 1,649 and matching the "Compra de mercancía" wallet movement
-- exactly. `mart.v_dropshipping_margin` already preferred it; the economics
-- view did not, understating COGS by 3,547.30.
--
-- THEY ARE FIXED TOGETHER ON PURPOSE.
-- A inflated cost by 3,211.51; B understated it by 3,547.30. The net effect on
-- the headline was 335.79 - close enough to look right, by pure coincidence.
-- Correcting either one alone would swing the number and read as a regression,
-- so both land in the same migration.
--
-- `NULLIF(product_cost, 0)` rather than dropping the column: where the sale
-- document does carry a cost it is the more specific figure, and a real 0 is
-- indistinguishable from a missing one in this source anyway.
--
-- Depends on: 022. Idempotent.
-- =============================================================================

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
        END AS days_to_cash
   FROM core.shipment s
     JOIN core.status_canon sc ON sc.code = s.status_code
     LEFT JOIN stg.v_movement_by_shipment mv ON mv.shipment_id = s.id
     LEFT JOIN core.product p ON p.id = s.product_id;
