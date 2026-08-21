-- =============================================================================
-- Data Effi - 013 - Where a Google Sheet connection keeps its URL.
--
-- Every other connection stores the NAME of an environment variable in
-- `secret_ref`, and the value never leaves the server. A Google Sheet published
-- to the web is the one case where that rule buys nothing: the URL Google hands
-- out is already public - anyone who has it can read the sheet without logging
-- in, which is precisely what "Publicar en la web" means. Forcing the operator
-- to also ask an administrator to set an env var would add friction and protect
-- nothing.
--
-- SO THIS COLUMN IS FOR PUBLIC URLs AND NOTHING ELSE. It is readable by anyone
-- who can read the connection row, it appears in backups in the clear, and it is
-- never redacted. Never put an API key, a token, a session cookie or a signed
-- URL here: those still belong in an environment variable named by `secret_ref`.
--
-- Depends on: 012. Idempotent.
-- =============================================================================

ALTER TABLE core.connection
    ADD COLUMN IF NOT EXISTS source_url text;

COMMENT ON COLUMN core.connection.source_url IS
    'PUBLIC source URL, currently only a Google Sheet published to the web as
     CSV. Stored in the clear on purpose because it is already public. NOT a
     place for credentials - those go in an env var named by secret_ref.';

-- Only https, and only a host we intend to fetch from. The connector checks the
-- same thing before every request; this constraint means a row that skipped the
-- API could never smuggle in an address the connector would then be asked to
-- fetch (SSRF).
ALTER TABLE core.connection
    DROP CONSTRAINT IF EXISTS connection_source_url_is_public_https;

ALTER TABLE core.connection
    ADD CONSTRAINT connection_source_url_is_public_https
    CHECK (source_url IS NULL OR source_url ~ '^https://docs\.google\.com/');

-- =============================================================================
-- A batch remembers its own country.
--
-- Until migration 012 the connection could always answer "which country is this
-- load about?", because every connection had one. A GLOBAL connection cannot:
-- the file says. So the batch has to carry the answer the ingestion worked out,
-- or the load history shows a blank column for every manual upload, webhook and
-- sheet - which is most of them.
-- =============================================================================

ALTER TABLE raw.load_batch
    ADD COLUMN IF NOT EXISTS country_code char(2);

COMMENT ON COLUMN raw.load_batch.country_code IS
    'Country this load was filed under: the connection''s, or the one read from
     the file when the connection is global. NULL only on batches loaded before
     migration 013.';

CREATE OR REPLACE VIEW mart.v_batch_history AS
SELECT
    b.tenant_id,
    b.id                                                        AS batch_id,
    b.connection_id,
    c.name                                                      AS connection_name,
    -- The batch first, the connection second: a global connection has no country
    -- of its own and would otherwise blank out its whole history.
    COALESCE(b.country_code, c.country_code)                    AS country_code,
    c.platform_code,
    b.source_name,
    b.kind,
    b.status,
    b.rows_total,
    b.rows_inserted,
    b.rows_updated,
    b.rows_skipped,
    b.rows_failed,
    b.error,
    b.started_at,
    b.finished_at,
    round(EXTRACT(EPOCH FROM (b.finished_at - b.started_at))::numeric, 2) AS duration_seconds,
    (SELECT count(*) FROM raw.load_discrepancy d WHERE d.batch_id = b.id) AS discrepancy_count
FROM raw.load_batch b
JOIN core.connection c ON c.id = b.connection_id
WHERE b.tenant_id = core.current_tenant_id();
