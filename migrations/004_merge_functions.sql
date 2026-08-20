-- =============================================================================
-- Norte - 004 - Merge helpers used by PostgresStore's ON CONFLICT clause.
--
-- These functions encode the same policy as pipeline/ingest.py::merge_shipment.
-- Keeping them as SQL functions means the rule is written once, not inlined into
-- a generated statement where nobody would ever find it again.
--
-- Depends on: 001, 002. Idempotent.
-- =============================================================================

-- Status may only move forward, and never at all once terminal.
CREATE OR REPLACE FUNCTION core.status_advance(p_current text, p_incoming text)
RETURNS text
LANGUAGE sql
STABLE
AS $fn$
    SELECT CASE
        WHEN p_current IS NULL THEN p_incoming
        WHEN p_incoming IS NULL THEN p_current
        WHEN (SELECT is_terminal FROM core.status_canon WHERE code = p_current) THEN p_current
        WHEN (SELECT sort_order FROM core.status_canon WHERE code = p_incoming)
           > (SELECT sort_order FROM core.status_canon WHERE code = p_current) THEN p_incoming
        ELSE p_current
    END;
$fn$;

COMMENT ON FUNCTION core.status_advance IS
    'Mirror of merge_shipment status rule: forward only, terminal is frozen.';

-- Relink movements that arrived before their shipment existed.
CREATE OR REPLACE FUNCTION core.relink_orphan_movements(p_tenant_id uuid DEFAULT NULL)
RETURNS integer
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_linked integer;
BEGIN
    WITH linked AS (
        UPDATE core.movement m
           SET shipment_id = s.id,
               updated_at  = now()
          FROM core.shipment s
         WHERE m.shipment_id IS NULL
           AND m.tracking_number_raw IS NOT NULL
           AND s.connection_id = m.connection_id
           AND s.tracking_number = m.tracking_number_raw
           AND (p_tenant_id IS NULL OR m.tenant_id = p_tenant_id)
        RETURNING 1
    )
    SELECT count(*) INTO v_linked FROM linked;

    -- CS interactions arrive the same way and link on the same key.
    UPDATE core.cs_interaction ci
       SET shipment_id = s.id
      FROM core.shipment s
     WHERE ci.shipment_id IS NULL
       AND ci.tracking_number_raw IS NOT NULL
       AND s.connection_id = ci.connection_id
       AND s.tracking_number = ci.tracking_number_raw
       AND (p_tenant_id IS NULL OR ci.tenant_id = p_tenant_id);

    RETURN v_linked;
END;
$fn$;

-- Measured p90 of days-to-delivery per country. The worker proposes this as a
-- maturation window; it never applies it on its own.
CREATE OR REPLACE FUNCTION core.measure_maturation_p90(p_tenant_id uuid, p_country_code char(2))
RETURNS integer
LANGUAGE sql
STABLE
AS $fn$
    SELECT ceil(percentile_cont(0.9) WITHIN GROUP (
               ORDER BY (s.delivered_at::date - s.created_date)
           ))::integer
    FROM core.shipment s
    WHERE s.tenant_id = p_tenant_id
      AND s.country_code = p_country_code
      AND s.delivered_at IS NOT NULL
      AND s.delivered_at::date >= s.created_date
      AND s.created_date >= CURRENT_DATE - interval '180 days';
$fn$;

-- Supporting indexes for the ingestion hot path.
CREATE INDEX IF NOT EXISTS ix_shipment_conn_tracking
    ON core.shipment (connection_id, tracking_number);
CREATE INDEX IF NOT EXISTS ix_carrier_lookup
    ON core.carrier (tenant_id, country_code, name_norm);
CREATE INDEX IF NOT EXISTS ix_product_lookup
    ON core.product (tenant_id, name_norm);
CREATE INDEX IF NOT EXISTS ix_product_alias_lookup
    ON core.product_alias (tenant_id, alias_norm);
CREATE INDEX IF NOT EXISTS ix_supplier_lookup
    ON core.supplier (tenant_id, name_norm);
