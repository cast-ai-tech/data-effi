-- =============================================================================
-- Norte - 009 - The metrics the Effi export was already carrying.
--
-- The guides report has 87 columns and Norte was reading 28 of them. The rest
-- were reported as "ignored" - which was honest, but they hold four things a
-- dropshipping operation needs and nobody was measuring:
--
--   1. THE MARGIN CHAIN. Effi records what you charged, what you paid the
--      supplier, and what the supplier invoiced, per guide. That is the whole
--      dropshipping P&L and it was sitting unused.
--   2. FULFILMENT TIME. `Fecha relación de despacho` is when the parcel
--      physically leaves. In a real export the median gap from guide creation
--      to dispatch was SIX DAYS - longer than the carrier then takes to
--      deliver. That is time the operator controls and could not see.
--   3. THE OFFICE QUEUE. Guides waiting at an agency decay into returns. How
--      fast they decay decides whether chasing them is worth it.
--   4. FREIGHT DETAIL. Weight, the three freight components, and the negotiated
--      discount - enough to tell whether a carrier is expensive or just
--      carrying heavier parcels.
--
-- Depends on: 001-008. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Shipment columns for what the export already knows.
-- -----------------------------------------------------------------------------
ALTER TABLE core.shipment
    -- Logistics
    ADD COLUMN IF NOT EXISTS weight_kg              numeric(10, 3),
    ADD COLUMN IF NOT EXISTS freight_base           numeric(14, 2),
    ADD COLUMN IF NOT EXISTS discount_pct           numeric(6, 4),
    ADD COLUMN IF NOT EXISTS dispatch_batch_ref     text,
    -- When the parcel physically left. The gap from created_date is the only
    -- part of the delivery clock the merchant owns.
    ADD COLUMN IF NOT EXISTS dispatched_batch_at    timestamptz,
    -- The dropshipping chain, per guide
    ADD COLUMN IF NOT EXISTS sale_total             numeric(14, 2),
    ADD COLUMN IF NOT EXISTS distributor_sale_total numeric(14, 2),
    ADD COLUMN IF NOT EXISTS distributor_cost_total numeric(14, 2),
    ADD COLUMN IF NOT EXISTS supplier_sale_total    numeric(14, 2),
    ADD COLUMN IF NOT EXISTS distributor_name       text,
    -- Settlement, beyond the collection one already modelled
    ADD COLUMN IF NOT EXISTS settled_any_at         timestamptz,
    ADD COLUMN IF NOT EXISTS settled_return_at      timestamptz,
    ADD COLUMN IF NOT EXISTS settled_return         boolean;

COMMENT ON COLUMN core.shipment.dispatched_batch_at IS
    'Fecha relación de despacho: when the parcel physically left. Confirmed with
     the operator - it is dispatch, not an accounting close.';
COMMENT ON COLUMN core.shipment.distributor_cost_total IS
    'What the merchant paid the supplier for this guide. The other half of the
     dropshipping margin.';

CREATE INDEX IF NOT EXISTS ix_shipment_dispatch_batch
    ON core.shipment (tenant_id, dispatch_batch_ref)
    WHERE dispatch_batch_ref IS NOT NULL;

-- -----------------------------------------------------------------------------
-- Product catalogue: what the operator maintains by hand.
--
-- Products are created automatically by ingestion (a name appears in a report,
-- a row appears here). The operator then fills in the commercial truth: real
-- cost, list price, weight. Those values OVERRIDE what the reports imply,
-- because the report knows what one guide cost and the catalogue knows what the
-- product costs.
-- -----------------------------------------------------------------------------
ALTER TABLE core.product
    ADD COLUMN IF NOT EXISTS list_price      numeric(14, 2),
    ADD COLUMN IF NOT EXISTS target_margin_pct numeric(6, 2),
    ADD COLUMN IF NOT EXISTS weight_kg       numeric(10, 3),
    ADD COLUMN IF NOT EXISTS notes           text,
    ADD COLUMN IF NOT EXISTS is_active       boolean NOT NULL DEFAULT true,
    -- Set when a human edits it. An untouched product is one the operator has
    -- never confirmed, and the UI says so instead of pretending the cost is real.
    ADD COLUMN IF NOT EXISTS reviewed_at     timestamptz,
    ADD COLUMN IF NOT EXISTS reviewed_by     uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_at      timestamptz NOT NULL DEFAULT now();

DROP TRIGGER IF EXISTS tr_product_touch ON core.product;
CREATE TRIGGER tr_product_touch BEFORE UPDATE ON core.product
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- Cost history: when a supplier raises a price, the margin moves and nobody
-- notices. This table is what makes that visible after the fact.
CREATE TABLE IF NOT EXISTS core.product_cost_history (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    product_id  uuid NOT NULL REFERENCES core.product(id) ON DELETE CASCADE,
    unit_cost   numeric(14, 2) NOT NULL,
    currency_code char(3),
    source      text NOT NULL DEFAULT 'manual'
                CHECK (source IN ('manual', 'import', 'observed')),
    changed_by  uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    changed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_product_cost_history
    ON core.product_cost_history (product_id, changed_at DESC);

ALTER TABLE core.product_cost_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.product_cost_history FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON core.product_cost_history;
CREATE POLICY tenant_isolation ON core.product_cost_history
    USING (tenant_id = core.current_tenant_id() OR core.is_service_context())
    WITH CHECK (tenant_id = core.current_tenant_id() OR core.is_service_context());

CREATE OR REPLACE FUNCTION core.record_product_cost()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.unit_cost IS NOT NULL
       AND NEW.unit_cost IS DISTINCT FROM COALESCE(OLD.unit_cost, -1) THEN
        INSERT INTO core.product_cost_history
            (tenant_id, product_id, unit_cost, currency_code, source, changed_by)
        VALUES (NEW.tenant_id, NEW.id, NEW.unit_cost, NEW.currency_code,
                CASE WHEN NEW.reviewed_by IS NOT NULL THEN 'manual' ELSE 'import' END,
                NEW.reviewed_by);
    END IF;
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS tr_product_cost_history ON core.product;
CREATE TRIGGER tr_product_cost_history AFTER INSERT OR UPDATE OF unit_cost ON core.product
    FOR EACH ROW EXECUTE FUNCTION core.record_product_cost();

-- =============================================================================
-- 1. THE DROPSHIPPING MARGIN CHAIN
-- =============================================================================

CREATE OR REPLACE VIEW mart.v_dropshipping_margin AS
WITH per_product AS (
    SELECT
        s.tenant_id,
        s.country_code,
        s.product_id,
        COALESCE(p.name, 'Sin producto')                        AS product_name,
        p.sku,
        COALESCE(sup.name, s.distributor_name, 'Sin proveedor') AS supplier_name,
        p.unit_cost                                             AS catalogue_cost,
        p.list_price                                            AS catalogue_price,
        p.reviewed_at IS NOT NULL                               AS catalogue_reviewed,
        count(*)                                                AS shipments,
        count(*) FILTER (WHERE sc.is_delivered)                 AS delivered,
        sum(s.quantity)                                         AS units,
        -- What the merchant charged (only guides that were actually collected)
        sum(COALESCE(s.distributor_sale_total, s.declared_value))
            FILTER (WHERE sc.is_delivered)                      AS revenue,
        -- What the merchant paid the supplier, on every guide dispatched -
        -- including the ones that came back, because the stock was still bought
        sum(COALESCE(s.distributor_cost_total, s.product_cost)) AS supplier_cost,
        sum(COALESCE(s.distributor_cost_total, s.product_cost))
            FILTER (WHERE sc.is_delivered)                      AS supplier_cost_delivered,
        sum(COALESCE(s.freight_cost, 0) + COALESCE(s.return_freight_cost, 0)) AS freight,
        min(s.currency_code)                                    AS currency_code
    FROM core.shipment s
    JOIN core.status_canon sc ON sc.code = s.status_code
    LEFT JOIN core.product p ON p.id = s.product_id
    LEFT JOIN core.supplier sup ON sup.id = p.supplier_id
    WHERE s.tenant_id = core.current_tenant_id()
    GROUP BY s.tenant_id, s.country_code, s.product_id, p.name, p.sku,
             sup.name, s.distributor_name, p.unit_cost, p.list_price, p.reviewed_at
)
SELECT
    tenant_id,
    country_code,
    product_id,
    product_name,
    sku,
    supplier_name,
    shipments,
    delivered,
    units,
    revenue::numeric(14, 2),
    supplier_cost::numeric(14, 2),
    freight::numeric(14, 2),

    -- Gross margin on what was actually sold.
    (revenue - COALESCE(supplier_cost_delivered, 0))::numeric(14, 2)  AS gross_margin,
    round((revenue - COALESCE(supplier_cost_delivered, 0))
          / NULLIF(revenue, 0) * 100, 2)                              AS gross_margin_pct,

    -- The number that matters in COD: freight is paid on everything dispatched,
    -- and stock is bought for everything dispatched - delivered or not.
    (revenue - COALESCE(supplier_cost, 0) - COALESCE(freight, 0))::numeric(14, 2)
                                                                      AS net_contribution,
    round((revenue - COALESCE(supplier_cost, 0) - COALESCE(freight, 0))
          / NULLIF(shipments, 0), 2)                                  AS contribution_per_shipment,

    -- What the returns cost: stock bought and freight paid for nothing.
    (COALESCE(supplier_cost, 0) - COALESCE(supplier_cost_delivered, 0))::numeric(14, 2)
                                                                      AS cost_of_undelivered,

    -- Break-even: below this delivery rate the product loses money.
    round(
        (COALESCE(supplier_cost, 0) + COALESCE(freight, 0))
        / NULLIF(revenue / NULLIF(delivered, 0) * shipments, 0) * 100, 2
    )                                                                 AS breakeven_delivery_pct,
    round(delivered::numeric / NULLIF(shipments, 0) * 100, 2)         AS delivery_rate_pct,

    catalogue_cost,
    catalogue_price,
    catalogue_reviewed,
    -- Observed cost per unit against the catalogue. A gap means the supplier
    -- changed the price, or the catalogue was never updated.
    round(COALESCE(supplier_cost, 0) / NULLIF(units, 0), 2)           AS observed_unit_cost,
    currency_code
FROM per_product;

COMMENT ON VIEW mart.v_dropshipping_margin IS
    'Charged, paid, and what is left - per product. Freight and stock count on
     every dispatch, not only the delivered ones.';

-- =============================================================================
-- 2. FULFILMENT: the part of the clock the merchant owns
-- =============================================================================

CREATE OR REPLACE VIEW mart.v_fulfillment_sla AS
WITH base AS (
    SELECT
        s.tenant_id,
        s.country_code,
        s.carrier_id,
        COALESCE(c.name, 'Sin transportadora')                  AS carrier_name,
        s.service_level,
        -- Negative values exist in real exports (a batch closed before the
        -- guide's own date). Treated as unknown rather than as a negative delay.
        CASE WHEN s.dispatched_batch_at IS NOT NULL
              AND s.dispatched_batch_at::date >= s.created_date
             THEN (s.dispatched_batch_at::date - s.created_date) END AS prep_days,
        CASE WHEN s.delivered_at IS NOT NULL AND s.dispatched_batch_at IS NOT NULL
              AND s.delivered_at >= s.dispatched_batch_at
             THEN (s.delivered_at::date - s.dispatched_batch_at::date) END AS transit_days,
        CASE WHEN s.delivered_at IS NOT NULL
             THEN (s.delivered_at::date - s.created_date) END     AS total_days,
        -- Did it arrive by the date the platform promised?
        CASE WHEN s.delivered_at IS NOT NULL AND s.expected_delivery_date IS NOT NULL
             THEN s.delivered_at::date <= s.expected_delivery_date END AS on_time,
        sc.is_delivered
    FROM core.shipment s
    JOIN core.status_canon sc ON sc.code = s.status_code
    LEFT JOIN core.carrier c ON c.id = s.carrier_id
    WHERE s.tenant_id = core.current_tenant_id()
)
SELECT
    tenant_id,
    country_code,
    carrier_id,
    carrier_name,
    COALESCE(service_level, 'Sin servicio')                     AS service_level,
    count(*)                                                    AS shipments,
    count(*) FILTER (WHERE is_delivered)                        AS delivered,

    -- Yours: from creating the guide to the parcel leaving.
    round(avg(prep_days)::numeric, 1)                           AS avg_prep_days,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY prep_days)::numeric(6, 1)
                                                                AS p50_prep_days,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY prep_days)::numeric(6, 1)
                                                                AS p90_prep_days,

    -- Theirs: from leaving to arriving.
    round(avg(transit_days)::numeric, 1)                        AS avg_transit_days,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY transit_days)::numeric(6, 1)
                                                                AS p90_transit_days,
    round(avg(total_days)::numeric, 1)                          AS avg_total_days,

    -- How much of the wait is the merchant's own doing.
    round(avg(prep_days) / NULLIF(avg(total_days), 0) * 100, 1) AS prep_share_pct,

    count(*) FILTER (WHERE on_time IS TRUE)                     AS on_time_count,
    count(*) FILTER (WHERE on_time IS NOT NULL)                 AS measurable_count,
    round(count(*) FILTER (WHERE on_time IS TRUE)::numeric
          / NULLIF(count(*) FILTER (WHERE on_time IS NOT NULL), 0) * 100, 2)
                                                                AS on_time_pct
FROM base
GROUP BY tenant_id, country_code, carrier_id, carrier_name, service_level;

COMMENT ON VIEW mart.v_fulfillment_sla IS
    'Splits the delivery clock into the part the merchant controls (preparation)
     and the part the carrier does (transit), plus promise compliance.';

-- =============================================================================
-- 3. THE OFFICE QUEUE: money waiting to be collected in person
-- =============================================================================

CREATE OR REPLACE VIEW mart.v_office_rescue AS
WITH in_office AS (
    SELECT
        s.tenant_id,
        s.country_code,
        s.geo_id,
        COALESCE(g.level1_name, 'Sin dato')                     AS level1_name,
        COALESCE(g.city_name, 'Sin dato')                       AS city_name,
        s.carrier_id,
        COALESCE(c.name, 'Sin transportadora')                  AS carrier_name,
        (CURRENT_DATE - s.created_date)                         AS days_waiting,
        s.declared_value,
        s.currency_code
    FROM core.shipment s
    LEFT JOIN core.geo g ON g.id = s.geo_id
    LEFT JOIN core.carrier c ON c.id = s.carrier_id
    WHERE s.tenant_id = core.current_tenant_id()
      AND s.status_code = 'in_office'
)
SELECT
    tenant_id,
    country_code,
    carrier_name,
    level1_name,
    city_name,
    count(*)                                                    AS shipments,
    sum(declared_value)::numeric(14, 2)                         AS value_waiting,
    round(avg(days_waiting)::numeric, 1)                        AS avg_days_waiting,
    -- Banded by how urgent the chase is. Past three weeks the parcel is
    -- normally sent back on its own.
    count(*) FILTER (WHERE days_waiting <= 7)                   AS fresh_0_7,
    count(*) FILTER (WHERE days_waiting BETWEEN 8 AND 14)       AS aging_8_14,
    count(*) FILTER (WHERE days_waiting BETWEEN 15 AND 21)      AS urgent_15_21,
    count(*) FILTER (WHERE days_waiting > 21)                   AS probably_lost,
    sum(declared_value) FILTER (WHERE days_waiting BETWEEN 8 AND 21)::numeric(14, 2)
                                                                AS value_still_recoverable,
    min(currency_code)                                          AS currency_code
FROM in_office
GROUP BY tenant_id, country_code, carrier_name, level1_name, city_name;

COMMENT ON VIEW mart.v_office_rescue IS
    'Guides sitting at a carrier office. Neither delivered nor returned, and the
     cheapest recovery available - one phone call.';

-- =============================================================================
-- 4. FREIGHT: weight, components, and the negotiated discount
-- =============================================================================

CREATE OR REPLACE VIEW mart.v_freight_analysis AS
SELECT
    s.tenant_id,
    s.country_code,
    s.carrier_id,
    COALESCE(c.name, 'Sin transportadora')                      AS carrier_name,
    COALESCE(s.service_level, 'Sin servicio')                   AS service_level,
    count(*)                                                    AS shipments,
    round(avg(s.weight_kg)::numeric, 2)                         AS avg_weight_kg,
    sum(s.weight_kg)::numeric(12, 2)                            AS total_weight_kg,

    sum(s.freight_cost)::numeric(14, 2)                         AS freight_total,
    round(avg(s.freight_cost)::numeric, 2)                      AS avg_freight,
    -- The comparable number. A carrier is not expensive because it carries
    -- heavier parcels.
    round(sum(s.freight_cost) / NULLIF(sum(s.weight_kg), 0), 2) AS freight_per_kg,

    round(avg(s.freight_base)::numeric, 2)                      AS avg_freight_base,
    round(avg(s.insurance_cost)::numeric, 2)                    AS avg_handling,
    round(avg(s.collection_fee)::numeric, 2)                    AS avg_collection_fee,
    round(avg(s.discount_pct)::numeric * 100, 1)                AS avg_discount_pct,
    -- What the negotiated discount is worth over the period.
    round(sum(s.freight_cost * s.discount_pct
              / NULLIF(1 - s.discount_pct, 0))::numeric, 2)     AS discount_value,

    -- Freight as a share of what the guide is worth: the real question.
    round(sum(s.freight_cost) / NULLIF(sum(s.declared_value), 0) * 100, 2)
                                                                AS freight_share_of_value_pct,
    sum(s.return_freight_cost)::numeric(14, 2)                  AS return_freight_total,
    min(s.currency_code)                                        AS currency_code
FROM core.shipment s
LEFT JOIN core.carrier c ON c.id = s.carrier_id
WHERE s.tenant_id = core.current_tenant_id()
GROUP BY s.tenant_id, s.country_code, s.carrier_id, c.name, s.service_level;

COMMENT ON VIEW mart.v_freight_analysis IS
    'Freight per kilo, by component, and what the negotiated discount is worth.';

-- =============================================================================
-- Product catalogue health: what the operator still has to fill in
-- =============================================================================

CREATE OR REPLACE VIEW mart.v_product_catalogue AS
SELECT
    p.tenant_id,
    p.id                                                        AS product_id,
    p.name                                                      AS product_name,
    p.sku,
    p.category,
    sup.name                                                    AS supplier_name,
    p.unit_cost,
    p.list_price,
    p.target_margin_pct,
    p.weight_kg,
    p.currency_code,
    p.is_active,
    p.reviewed_at,
    p.notes,
    count(s.id)                                                 AS shipments,
    count(s.id) FILTER (WHERE sc.is_delivered)                  AS delivered,
    max(s.created_date)                                         AS last_shipment_date,
    -- What the guides say this product actually costs, per unit.
    round(sum(COALESCE(s.distributor_cost_total, s.product_cost))
          / NULLIF(sum(s.quantity), 0), 2)                      AS observed_unit_cost,
    -- The catalogue and reality disagreeing is the signal worth acting on.
    CASE
        WHEN p.unit_cost IS NULL THEN 'sin_costo'
        WHEN p.reviewed_at IS NULL THEN 'sin_revisar'
        WHEN abs(p.unit_cost - COALESCE(
                round(sum(COALESCE(s.distributor_cost_total, s.product_cost))
                      / NULLIF(sum(s.quantity), 0), 2), p.unit_cost))
             / NULLIF(p.unit_cost, 0) > 0.10 THEN 'costo_desactualizado'
        ELSE 'ok'
    END                                                         AS catalogue_status,
    round(
        (COALESCE(p.list_price, 0) - COALESCE(p.unit_cost, 0))
        / NULLIF(p.list_price, 0) * 100, 2)                     AS catalogue_margin_pct
FROM core.product p
LEFT JOIN core.supplier sup ON sup.id = p.supplier_id
LEFT JOIN core.shipment s ON s.product_id = p.id
LEFT JOIN core.status_canon sc ON sc.code = s.status_code
WHERE p.tenant_id = core.current_tenant_id()
GROUP BY p.tenant_id, p.id, p.name, p.sku, p.category, sup.name, p.unit_cost,
         p.list_price, p.target_margin_pct, p.weight_kg, p.currency_code,
         p.is_active, p.reviewed_at, p.notes;

COMMENT ON VIEW mart.v_product_catalogue IS
    'The catalogue next to what the reports observed. A product with no cost, or
     a cost 10% away from reality, is flagged for the operator to fix.';

-- -----------------------------------------------------------------------------
-- Widgets for the new views. Blocked/degraded resolution comes for free.
-- -----------------------------------------------------------------------------
INSERT INTO core.widget_catalog
    (widget_code, tab, title, description, required_domains, optional_domains,
     blocked_message, sort_order) VALUES
    ('dropshipping_margin', 'efectividad', 'Cadena de márgenes',
     'Lo que cobras, lo que le pagas al proveedor y lo que queda, por producto.',
     ARRAY['shipments'], ARRAY['catalog'],
     'Necesitas al menos una conexión de guías.', 50),

    ('fulfillment_sla', 'logistica', 'Alistamiento y cumplimiento',
     'Cuánto tardas tú en despachar y cuánto tarda la transportadora en llegar.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Necesitas al menos una conexión de guías.', 15),

    ('office_rescue', 'logistica', 'Guías en oficina',
     'Paquetes esperando que el cliente los recoja, por antigüedad y ciudad.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Necesitas al menos una conexión de guías.', 25),

    ('freight_analysis', 'logistica', 'Análisis de flete',
     'Flete por kilo, por componente, y cuánto vale tu descuento negociado.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Necesitas al menos una conexión de guías.', 30),

    ('cash_cycle', 'finanzas', 'Ciclo de caja',
     'Cuántos días pasan desde que despachas hasta que la plata es tuya.',
     ARRAY['shipments'], ARRAY['movements'],
     'Necesitas al menos una conexión de guías.', 25)
ON CONFLICT (widget_code) DO NOTHING;

GRANT SELECT ON mart.v_dropshipping_margin, mart.v_fulfillment_sla,
                mart.v_office_rescue, mart.v_freight_analysis,
                mart.v_product_catalogue TO norte_readonly;
GRANT SELECT ON mart.v_dropshipping_margin, mart.v_fulfillment_sla,
                mart.v_office_rescue, mart.v_freight_analysis,
                mart.v_product_catalogue TO norte_app;
