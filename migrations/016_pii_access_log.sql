-- =============================================================================
-- Data Effi - 016 - The record of who read a customer's contact data.
--
-- Migration 015 made contact data readable again: the columns are Fernet
-- ciphertext and the API decrypts them for owner/analyst. Readable data with no
-- record of who read it is the part that turns a leak into an unanswerable
-- question - "someone exported the customer list" with no way to say who, when,
-- or how many people it covered.
--
-- WHAT THIS TABLE STORES, AND WHAT IT DELIBERATELY DOES NOT
-- It stores the FACT of an access: which tenant, which user, which endpoint,
-- how many records, when, and from where. It stores NO value - not a name, not
-- a phone number, not a customer_hash, not even ciphertext. An audit log that
-- accumulates the very data it audits is a second copy of the problem, and the
-- one nobody remembers to encrypt.
--
-- WHY A ROW COUNT AND NOT A ROW PER RECORD
-- One row per customer read would make this table bigger than the data it
-- watches over on the first page of a 200-row table, and every one of those
-- rows would be an identifier tying a person to a moment in time. The count
-- answers the question that matters after an incident - "how much left" -
-- without building an access history for each individual customer.
--
-- Same RLS shape as every other tenant table (see 007): a tenant context sees
-- its own rows, the service context sees all, and neither set means zero rows.
--
-- Depends on: 007, 015. Idempotent.
-- =============================================================================

CREATE TABLE IF NOT EXISTS raw.pii_access (
    id           bigserial PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    -- ON DELETE SET NULL, not CASCADE: removing a user must not erase the
    -- record that they read contact data. The row outlives the account.
    user_id      uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    -- The route template ('GET /orders'), never a rendered URL: a query string
    -- carries the operator's search terms, which are often a phone number.
    endpoint     text NOT NULL,
    record_count integer NOT NULL DEFAULT 0 CHECK (record_count >= 0),
    ip           inet,
    created_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE raw.pii_access IS
    'One row per response that carried decrypted contact data. Records the fact
     of the access - never the value. Written by the API on the same connection
     and transaction as the read, so a rolled-back request logs nothing.';

COMMENT ON COLUMN raw.pii_access.endpoint IS
    'Route template, e.g. GET /orders/{shipment_id}. Never the rendered URL: a
     query string can contain the phone number the operator searched for.';

COMMENT ON COLUMN raw.pii_access.record_count IS
    'How many records in that response actually carried contact data. Zero-count
     responses are not logged at all - nothing was disclosed.';

-- The two questions asked after an incident: "what did this tenant expose?"
-- and "what did this person read?"
CREATE INDEX IF NOT EXISTS ix_pii_access_tenant
    ON raw.pii_access (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_pii_access_user
    ON raw.pii_access (user_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- Row-level security, identical in shape to 007.
-- -----------------------------------------------------------------------------
ALTER TABLE raw.pii_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.pii_access FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON raw.pii_access;
CREATE POLICY tenant_isolation ON raw.pii_access
    USING (tenant_id = core.current_tenant_id() OR core.is_service_context())
    WITH CHECK (tenant_id = core.current_tenant_id() OR core.is_service_context());

-- -----------------------------------------------------------------------------
-- Grants.
--
-- INSERT and SELECT only. No UPDATE, no DELETE: an audit trail the application
-- can rewrite is not an audit trail. Retention, when it is needed, is an
-- administrative act by a role that owns the table - not something a bug in a
-- router can do by accident.
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT ON raw.pii_access TO norte_app;
REVOKE UPDATE, DELETE ON raw.pii_access FROM norte_app;
GRANT USAGE, SELECT ON SEQUENCE raw.pii_access_id_seq TO norte_app;

-- Deliberately NOT granted to norte_readonly: that role backs the NL->SQL
-- copilot, and the access log is a map of which operators handle customer data.
REVOKE ALL ON raw.pii_access FROM norte_readonly;
