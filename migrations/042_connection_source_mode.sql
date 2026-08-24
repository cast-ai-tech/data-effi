-- =============================================================================
-- Data Effi - 042 - How a connection receives data, said explicitly.
--
-- THE PROBLEM. A connection's platform (Effi, Dropi) and HOW it receives data
-- (a file someone uploads, a browser session the worker replays, a webhook, a
-- published sheet) were the same fact. They are not. An operator who exports
-- Effi's report by hand and uploads it is using Effi's data with nobody's
-- session, and until now the database refused that connection outright:
-- `enforce_tier3_consent` demanded a consent timestamp because the PLATFORM
-- is tier 3, even though no session would ever be used. So the Effi files
-- went into "Carga manual" and the dashboard could not tell them from Dropi's.
--
-- THE FIX. `source_mode` on core.connection:
--
--   file     someone uploads the platform's export (default; needs nothing)
--   session  the worker fetches with the operator's session (tier 3: consent)
--   sheet    a Google Sheet published as CSV (migration 013)
--   webhook  an automation POSTs rows (migration 012)
--   api      an official API with credentials (none wired yet)
--
-- Consent is now required exactly when the mode is `session` - which is what
-- the consent was always about. The tier-3 sync job (worker/jobs.py) selects
-- on `source_mode = 'session'` so a file-mode Effi connection is never
-- "fetched" and marked in error for having no session.
--
-- BACKFILL. Existing rows are classified from what they already carry: a
-- consent stamp means session, a source_url means sheet, a webhook token means
-- webhook, anything else is file. Nothing an operator set is overwritten.
--
-- Depends on: 041. Idempotent.
-- =============================================================================

ALTER TABLE core.connection
    ADD COLUMN IF NOT EXISTS source_mode text NOT NULL DEFAULT 'file'
        CHECK (source_mode IN ('file', 'session', 'sheet', 'webhook', 'api'));

COMMENT ON COLUMN core.connection.source_mode IS
    'Cómo recibe datos: file (alguien sube el export) | session (el worker usa la
     sesión del operador, exige consentimiento) | sheet | webhook | api. La
     plataforma dice DE QUIÉN son los datos; esto dice CÓMO llegan.';

-- Backfill, once, from the evidence each row already carries. Only rows still
-- on the default are touched, so re-running never undoes an operator's edit.
UPDATE core.connection c
   SET source_mode = CASE
        WHEN c.consent_granted_at IS NOT NULL
             AND EXISTS (SELECT 1 FROM core.platform p
                         WHERE p.code = c.platform_code AND p.requires_consent) THEN 'session'
        WHEN c.source_url IS NOT NULL         THEN 'sheet'
        WHEN c.webhook_token_hash IS NOT NULL THEN 'webhook'
        ELSE 'file'
       END
 WHERE c.source_mode = 'file'
   AND (c.consent_granted_at IS NOT NULL
        OR c.source_url IS NOT NULL
        OR c.webhook_token_hash IS NOT NULL);

-- Consent is about the session, not about the platform.
CREATE OR REPLACE FUNCTION core.enforce_tier3_consent()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_requires boolean;
BEGIN
    SELECT requires_consent INTO v_requires
    FROM core.platform WHERE code = NEW.platform_code;

    IF NEW.source_mode = 'session'
       AND coalesce(v_requires, false)
       AND NEW.consent_granted_at IS NULL THEN
        RAISE EXCEPTION
            'Platform % requires explicit consent (consent_granted_at) before a session connection can be created',
            NEW.platform_code
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$fn$;

CREATE INDEX IF NOT EXISTS ix_connection_file_target
    ON core.connection (tenant_id, country_code, platform_code)
    WHERE source_mode = 'file' AND status = 'active';
