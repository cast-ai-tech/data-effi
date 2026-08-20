-- =============================================================================
-- Norte - 010 - Keep every column, and work out the country from the file.
--
-- TWO THINGS THE OPERATOR ASKED FOR, AND ONE LINE HE COULD NOT ASK ME TO CROSS.
--
-- 1. "Store everything, in case a metric needs it later." Right instinct: a
--    column you discarded is a metric you cannot build. raw.source_row already
--    existed for this and was never being written. Now it is.
--
-- 2. EXCEPT the four columns that identify a human being. `Destinatario`,
--    `ID. destinatario`, `Dirección destinatario` and `Teléfonos destinatario`
--    are the name, national id, home address and phone of a real customer.
--    Ecuador's LOPDP and Colombia's Ley 1581 both make storing those a
--    liability, and this project's own rules forbid it outright. They are kept
--    as SHA-256 hashes, which still answers "is this the same person?" and
--    "did we ship to this address before?" while a database leak reveals
--    nothing about anybody.
--
-- 3. The reports say what country they are about. `País destinatario` was
--    'Ecuador' on all 1,649 rows of a real export, so the upload no longer has
--    to be told.
--
-- Depends on: 001-009. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Raw rows: every column of every file, kept for whatever gets asked next.
-- -----------------------------------------------------------------------------
ALTER TABLE raw.source_row
    ADD COLUMN IF NOT EXISTS entity_key text,
    ADD COLUMN IF NOT EXISTS redacted_fields text[] NOT NULL DEFAULT ARRAY[]::text[];

COMMENT ON COLUMN raw.source_row.payload IS
    'The complete original row, column names as the file wrote them. Personal
     identifiers are replaced by their SHA-256 - see redacted_fields.';
COMMENT ON COLUMN raw.source_row.redacted_fields IS
    'Which columns were hashed before storage. Never empty for a report that
     carries customer data.';

CREATE INDEX IF NOT EXISTS ix_source_row_entity
    ON raw.source_row (tenant_id, entity_key);
-- GIN over the payload so a future question can filter on any column without a
-- migration. This is what "store everything" is actually worth.
CREATE INDEX IF NOT EXISTS ix_source_row_payload
    ON raw.source_row USING gin (payload jsonb_path_ops);

ALTER TABLE raw.source_row ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.source_row FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON raw.source_row;
CREATE POLICY tenant_isolation ON raw.source_row
    USING (tenant_id = core.current_tenant_id() OR core.is_service_context())
    WITH CHECK (tenant_id = core.current_tenant_id() OR core.is_service_context());

-- -----------------------------------------------------------------------------
-- Country resolution from whatever the file calls it.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.country_alias (
    alias_norm   text PRIMARY KEY,
    country_code char(2) NOT NULL REFERENCES core.country(code) ON DELETE CASCADE
);

INSERT INTO core.country_alias (alias_norm, country_code)
SELECT core.normalize_text(name), code FROM core.country
ON CONFLICT (alias_norm) DO NOTHING;

INSERT INTO core.country_alias (alias_norm, country_code) VALUES
    ('co', 'CO'), ('col', 'CO'), ('colombia', 'CO'), ('republica de colombia', 'CO'),
    ('ec', 'EC'), ('ecu', 'EC'), ('ecuador', 'EC'), ('republica del ecuador', 'EC'),
    ('mx', 'MX'), ('mex', 'MX'), ('mexico', 'MX'), ('estados unidos mexicanos', 'MX'),
    ('pe', 'PE'), ('per', 'PE'), ('peru', 'PE'),
    ('cl', 'CL'), ('chl', 'CL'), ('chile', 'CL'),
    ('pa', 'PA'), ('pan', 'PA'), ('panama', 'PA'),
    ('gt', 'GT'), ('gtm', 'GT'), ('guatemala', 'GT')
ON CONFLICT (alias_norm) DO NOTHING;

CREATE OR REPLACE FUNCTION core.resolve_country(p_raw text)
RETURNS char(2)
LANGUAGE sql
STABLE
AS $fn$
    SELECT country_code FROM core.country_alias
    WHERE alias_norm = core.normalize_text(p_raw)
$fn$;

COMMENT ON FUNCTION core.resolve_country IS
    'Country name as written in a report -> ISO code. NULL when unrecognised,
     so the caller can fall back to the connection''s country and say so.';

-- -----------------------------------------------------------------------------
-- Batches remember what country the file turned out to be about, and whether
-- that matched the connection it was uploaded to.
-- -----------------------------------------------------------------------------
ALTER TABLE raw.load_batch
    ADD COLUMN IF NOT EXISTS detected_country_code char(2),
    ADD COLUMN IF NOT EXISTS profile_code text,
    ADD COLUMN IF NOT EXISTS rows_stored integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN raw.load_batch.detected_country_code IS
    'Country read from the file itself. Differs from the connection''s country
     when someone uploads an Ecuador report into a Colombia connection.';

-- -----------------------------------------------------------------------------
-- Reading the archive: what is in a batch, without touching core.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_source_row_archive AS
SELECT
    sr.tenant_id,
    sr.batch_id,
    b.source_name,
    b.kind,
    b.started_at                                                AS loaded_at,
    sr.row_number,
    sr.entity_key,
    sr.payload,
    sr.redacted_fields
FROM raw.source_row sr
JOIN raw.load_batch b ON b.id = sr.batch_id
WHERE sr.tenant_id = core.current_tenant_id();

COMMENT ON VIEW mart.v_source_row_archive IS
    'Every original column of every loaded row. The answer to "we need a metric
     from a column you did not map" is a query, not a re-upload.';

GRANT SELECT ON mart.v_source_row_archive TO norte_app;
-- Deliberately NOT granted to norte_readonly: the NL->SQL layer must never
-- reach raw rows, only aggregates. Hashed or not, this is row-level customer
-- data and a generated query has no business near it.
