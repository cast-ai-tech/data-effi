-- =============================================================================
-- Norte - 008 - What the real Effi exports taught us.
--
-- Written after reading two actual reports from a live Ecuadorian operation
-- (1,649 guides, 2,152 wallet movements). Everything here exists because the
-- real files needed it, not because it seemed like a good idea:
--
--   * 'Disponible para retiro en oficina' is a state of its own. 278 of 1,649
--     guides were sitting in an agency waiting for the customer to collect
--     them. That is neither delivered nor returned - it is the single most
--     recoverable failure mode in Ecuadorian COD, and folding it into
--     "novedad" would hide it.
--   * Effi's wallet has movement types Norte did not model: withdrawals to a
--     bank, withdrawal commissions, and tax withholding. A withdrawal is NOT
--     an operating cost - counting it as one would double-charge the P&L.
--   * A guide carries two identities: Effi's internal id and the carrier's
--     tracking number. The movements report only ever cites the carrier's.
--   * Settlement ("liquidación") is when the money actually reaches you. It is
--     the difference between a delivered guide and a paid one.
--
-- Depends on: 001-007. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- New canonical status: waiting for pickup at the carrier's office.
-- Sits between "out for delivery" and "delivery issue": the parcel arrived, the
-- customer has not come for it, and the clock is running.
-- -----------------------------------------------------------------------------
INSERT INTO core.status_canon
    (code, label, sort_order, is_terminal, is_delivered, is_returned, bucket)
VALUES
    ('in_office', 'En oficina', 52, false, false, false, 'pipeline')
ON CONFLICT (code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Effi status vocabulary, taken verbatim from a real export.
--
-- Two columns carry status: `Estado global guía inicial` is the canonical one
-- (8 distinct values) and `Estado guía inicial` is the carrier's own wording
-- (dozens of values, different per carrier). Both are mapped; the global one
-- wins when present.
-- -----------------------------------------------------------------------------
INSERT INTO core.status_alias (platform_code, alias_norm, status_code) VALUES
    -- Estado global guía inicial
    ('effi', 'entregada a destino',              'delivered'),
    ('effi', 'disponible para retiro en oficina','in_office'),
    ('effi', 'en transito',                      'in_transit'),
    ('effi', 'devolucion a origen',              'returning'),
    ('effi', 'cancelada por transportadora',     'cancelled'),
    -- Estado guía inicial (carrier wording, observed in the wild)
    ('effi', 'generada effi',                    'created'),
    ('effi', 'ingresando en agencia',            'in_transit'),
    ('effi', 'ingresando operativo',             'in_transit'),
    ('effi', 'en ruta a concesion',              'in_transit'),
    ('effi', 'en distribucion a cliente',        'out_for_delivery'),
    ('effi', 'reportado entregado en agencia',   'in_office'),
    ('effi', 'reportado entregado en app',       'delivered'),
    ('effi', 'entregado en agencia',             'in_office'),
    ('effi', 'retirado en oficina',              'delivered'),
    ('effi', 'devuelto a origen',                'returned'),
    ('effi', 'devolucion entregada',             'returned'),
    ('effi', 'anulada por transportadora',       'cancelled'),
    ('effi', 'siniestro',                        'lost'),
    ('effi', 'extravio',                         'lost')
ON CONFLICT (platform_code, alias_norm) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Movement types the real wallet uses.
--
-- `withdrawal` is category 'transfer': money moving from the Effi wallet to a
-- bank account is not revenue and not a cost - it already counted when it was
-- collected. Every KPI view ignores this category, which is the point.
-- -----------------------------------------------------------------------------
ALTER TABLE core.movement_type DROP CONSTRAINT IF EXISTS movement_type_category_check;
ALTER TABLE core.movement_type ADD CONSTRAINT movement_type_category_check
    CHECK (category IN ('revenue', 'freight', 'cogs', 'fee', 'adjustment', 'transfer', 'other'));

INSERT INTO core.movement_type (code, label, sign, category) VALUES
    ('collection_fee',    'Comisión por recaudo',      -1, 'fee'),
    ('withdrawal',        'Retiro a cuenta bancaria',  -1, 'transfer'),
    ('withdrawal_fee',    'Comisión por retiro',       -1, 'fee'),
    ('tax_withholding',   'Retención en la fuente',     1, 'adjustment')
ON CONFLICT (code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Shipment: the fields the real report carries and Norte had nowhere to put.
-- -----------------------------------------------------------------------------
ALTER TABLE core.shipment
    ADD COLUMN IF NOT EXISTS carrier_tracking_number text,
    ADD COLUMN IF NOT EXISTS insurance_cost          numeric(14, 2),
    ADD COLUMN IF NOT EXISTS collection_fee          numeric(14, 2),
    -- Settlement: when the money actually landed, not when the parcel did.
    ADD COLUMN IF NOT EXISTS settled_at              timestamptz,
    ADD COLUMN IF NOT EXISTS settled_with_collection boolean,
    ADD COLUMN IF NOT EXISTS expected_delivery_date  date,
    ADD COLUMN IF NOT EXISTS status_detail           text,
    ADD COLUMN IF NOT EXISTS service_level           text;

CREATE INDEX IF NOT EXISTS ix_shipment_carrier_tracking
    ON core.shipment (tenant_id, carrier_tracking_number)
    WHERE carrier_tracking_number IS NOT NULL;

COMMENT ON COLUMN core.shipment.carrier_tracking_number IS
    'The carrier''s own number. Effi''s movements report cites only this, so it is
     what links money to guides.';
COMMENT ON COLUMN core.shipment.settled_at IS
    'When Effi settled the guide into the wallet. Delivered is not the same as paid.';

-- -----------------------------------------------------------------------------
-- Movements can also be relinked by the carrier's number, not just Effi's id.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.relink_orphan_movements(p_tenant_id uuid DEFAULT NULL)
RETURNS integer
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_linked integer;
    v_extra  integer;
BEGIN
    -- Pass 1: the shipment's own tracking number.
    WITH linked AS (
        UPDATE core.movement m
           SET shipment_id = s.id, updated_at = now()
          FROM core.shipment s
         WHERE m.shipment_id IS NULL
           AND m.tracking_number_raw IS NOT NULL
           AND s.connection_id = m.connection_id
           AND s.tracking_number = m.tracking_number_raw
           AND (p_tenant_id IS NULL OR m.tenant_id = p_tenant_id)
        RETURNING 1
    )
    SELECT count(*) INTO v_linked FROM linked;

    -- Pass 2: the carrier's number. Effi's wallet only ever cites this one, so
    -- without this pass every movement from a real export stays orphaned.
    WITH linked AS (
        UPDATE core.movement m
           SET shipment_id = s.id, updated_at = now()
          FROM core.shipment s
         WHERE m.shipment_id IS NULL
           AND m.tracking_number_raw IS NOT NULL
           AND s.tenant_id = m.tenant_id
           AND s.carrier_tracking_number = m.tracking_number_raw
           AND (p_tenant_id IS NULL OR m.tenant_id = p_tenant_id)
        RETURNING 1
    )
    SELECT count(*) INTO v_extra FROM linked;

    UPDATE core.cs_interaction ci
       SET shipment_id = s.id
      FROM core.shipment s
     WHERE ci.shipment_id IS NULL
       AND ci.tracking_number_raw IS NOT NULL
       AND s.connection_id = ci.connection_id
       AND s.tracking_number = ci.tracking_number_raw
       AND (p_tenant_id IS NULL OR ci.tenant_id = p_tenant_id);

    RETURN v_linked + v_extra;
END;
$fn$;

-- -----------------------------------------------------------------------------
-- Economics: fold in the two new cost columns, and exclude transfers.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW stg.v_movement_by_shipment AS
SELECT
    m.shipment_id,
    m.tenant_id,
    sum(m.amount) FILTER (WHERE mt.category = 'revenue')    AS revenue_amount,
    sum(m.amount) FILTER (WHERE mt.category = 'freight')    AS freight_amount,
    sum(m.amount) FILTER (WHERE mt.category = 'cogs')       AS cogs_amount,
    sum(m.amount) FILTER (WHERE mt.category = 'fee')        AS fee_amount,
    sum(m.amount * mt.sign) FILTER (WHERE mt.category = 'adjustment') AS adjustment_amount,
    count(*) FILTER (WHERE mt.category <> 'transfer')       AS movement_count,
    -- New columns go LAST: CREATE OR REPLACE VIEW can append but never insert.
    -- Money moved to a bank account. Reported, never counted as a cost.
    sum(m.amount) FILTER (WHERE mt.category = 'transfer')   AS transfer_amount
FROM core.movement m
JOIN core.movement_type mt ON mt.code = m.movement_type_code
WHERE m.shipment_id IS NOT NULL
GROUP BY m.shipment_id, m.tenant_id;

CREATE OR REPLACE VIEW stg.v_shipment_economics AS
SELECT
    s.id                AS shipment_id,
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
    sc.sort_order       AS status_sort_order,
    s.declared_value,

    CASE WHEN s.delivered_at IS NOT NULL
         THEN (s.delivered_at::date - s.created_date) END       AS days_to_deliver,
    CASE WHEN NOT sc.is_terminal
         THEN (CURRENT_DATE - s.created_date) END               AS days_open,

    COALESCE(
        mv.revenue_amount,
        CASE WHEN sc.is_delivered THEN COALESCE(s.cod_collected, s.declared_value) END,
        0
    )::numeric(14, 2)                                           AS revenue_amount,

    COALESCE(
        mv.freight_amount,
        COALESCE(s.freight_cost, 0) + COALESCE(s.return_freight_cost, 0),
        0
    )::numeric(14, 2)                                           AS freight_amount,

    COALESCE(
        mv.cogs_amount,
        s.product_cost,
        CASE WHEN sc.is_delivered THEN p.unit_cost * s.quantity END,
        0
    )::numeric(14, 2)                                           AS cogs_amount,

    COALESCE(
        mv.fee_amount,
        COALESCE(s.platform_fee, 0) + COALESCE(s.insurance_cost, 0)
            + COALESCE(s.collection_fee, 0),
        0
    )::numeric(14, 2)                                           AS fee_amount,

    COALESCE(mv.adjustment_amount, 0)::numeric(14, 2)           AS adjustment_amount,
    COALESCE(mv.movement_count, 0)                              AS movement_count,

    -- Appended, not inserted: see the note above.
    s.carrier_tracking_number,
    s.settled_at,
    -- Days from dispatch to money actually in the wallet. In COD this is the
    -- number that decides whether you can buy inventory next week.
    CASE WHEN s.settled_at IS NOT NULL
         THEN (s.settled_at::date - s.created_date) END         AS days_to_cash
FROM core.shipment s
JOIN core.status_canon sc ON sc.code = s.status_code
LEFT JOIN stg.v_movement_by_shipment mv ON mv.shipment_id = s.id
LEFT JOIN core.product p ON p.id = s.product_id;

-- -----------------------------------------------------------------------------
-- The "problem" rate the operation actually cares about: novedad + waiting in
-- an office + heading back. All three are guides that need a human today.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_problem_rate AS
SELECT
    e.tenant_id,
    e.country_code,
    e.carrier_id,
    COALESCE(c.name, 'Sin transportadora')                      AS carrier_name,
    count(*)                                                    AS shipments,
    count(*) FILTER (WHERE e.status_code = 'delivery_issue')     AS novedad,
    count(*) FILTER (WHERE e.status_code = 'in_office')          AS en_oficina,
    count(*) FILTER (WHERE e.status_code IN ('returning', 'returned')) AS devolucion,
    count(*) FILTER (WHERE e.status_code IN
        ('delivery_issue', 'in_office', 'returning', 'returned'))  AS con_problema,
    round(count(*) FILTER (WHERE e.status_code IN
        ('delivery_issue', 'in_office', 'returning', 'returned'))::numeric
        / NULLIF(count(*), 0) * 100, 2)                          AS problem_rate_pct,
    -- Money sitting in an office waiting for someone to walk in.
    sum(e.declared_value) FILTER (WHERE e.status_code = 'in_office')::numeric(14, 2)
                                                                AS value_in_office,
    min(e.currency_code)                                        AS currency_code
FROM stg.v_shipment_economics e
LEFT JOIN core.carrier c ON c.id = e.carrier_id
WHERE e.tenant_id = core.current_tenant_id()
GROUP BY e.tenant_id, e.country_code, e.carrier_id, c.name;

COMMENT ON VIEW mart.v_problem_rate IS
    'Novedad + en oficina + devolución as one number. Effi splits them; the
     operator has to chase all three the same way.';

-- -----------------------------------------------------------------------------
-- Cash cycle: delivered is not paid.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_cash_cycle AS
SELECT
    e.tenant_id,
    e.country_code,
    count(*) FILTER (WHERE e.settled_at IS NOT NULL)            AS settled,
    count(*) FILTER (WHERE e.is_delivered AND e.settled_at IS NULL) AS delivered_unsettled,
    round(avg(e.days_to_cash)::numeric, 1)                      AS avg_days_to_cash,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY e.days_to_cash)::numeric(6, 1)
                                                                AS p50_days_to_cash,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY e.days_to_cash)::numeric(6, 1)
                                                                AS p90_days_to_cash,
    -- Delivered, collected by the carrier, not yet in your wallet.
    sum(e.revenue_amount) FILTER (WHERE e.is_delivered AND e.settled_at IS NULL)
        ::numeric(14, 2)                                        AS cash_in_transit,
    min(e.currency_code)                                        AS currency_code
FROM stg.v_shipment_economics e
WHERE e.tenant_id = core.current_tenant_id()
GROUP BY e.tenant_id, e.country_code;

COMMENT ON VIEW mart.v_cash_cycle IS
    'How many days until money from a dispatch is actually spendable.';

-- New views need the same grants as the rest of mart.
GRANT SELECT ON mart.v_problem_rate, mart.v_cash_cycle TO norte_readonly;
GRANT SELECT ON mart.v_problem_rate, mart.v_cash_cycle TO norte_app;
