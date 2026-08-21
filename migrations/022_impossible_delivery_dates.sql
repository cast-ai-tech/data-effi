-- =============================================================================
-- Data Effi - 022 - A delivery cannot precede its own guide.
--
-- One guide in the operator's real export is created 2026-08-15 and delivered
-- 2026-08-12. It is a source data error, not ours - but `days_to_deliver`
-- subtracted the two anyway and produced -3.
--
-- Over the whole history one bad row among 660 disappears into the average. The
-- date filter shipped in 018 is what makes it dangerous: inside a narrow window
-- that single row can be most of the sample, and /kpis/carriers reported -3.0
-- days for SERVIENTREGA between 10 and 15 August. A negative transit time is
-- not a small error, it is a number that destroys trust in the whole screen.
--
-- `mart.v_fulfillment_sla` already guarded against this
-- (`WHEN delivered_at >= dispatched_batch_at`); `days_to_deliver` did not. The
-- impossible pair now measures NULL - absent, not invented - and the guide
-- still appears everywhere else, because the rest of its data is fine.
--
-- Depends on: 021. Idempotent. Renumbered from 020: another agent shipped a
-- different 020 in parallel, and two files sharing a number makes apply order
-- depend on alphabetical sorting.
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
            WHEN s.delivered_at IS NOT NULL AND s.delivered_at::date >= s.created_date
            THEN s.delivered_at::date - s.created_date
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
    COALESCE(mv.cogs_amount, s.product_cost,
        CASE
            WHEN sc.is_delivered THEN p.unit_cost * s.quantity::numeric
            ELSE NULL::numeric
        END, 0::numeric)::numeric(14,2) AS cogs_amount,
    COALESCE(mv.fee_amount, COALESCE(s.platform_fee, 0::numeric) + COALESCE(s.insurance_cost, 0::numeric) + COALESCE(s.collection_fee, 0::numeric), 0::numeric)::numeric(14,2) AS fee_amount,
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
