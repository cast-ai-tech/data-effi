-- =============================================================================
-- Norte - 001 - Core schema: tenants, users, countries, platforms, connections
-- Target: PostgreSQL 15+ (Supabase compatible)
-- Idempotent: safe to re-run.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS raw;   -- landing zone: batches, source rows, audit
CREATE SCHEMA IF NOT EXISTS stg;   -- staging / normalization helpers
CREATE SCHEMA IF NOT EXISTS core;  -- canonical entities
CREATE SCHEMA IF NOT EXISTS mart;  -- read-only KPI views (only schema the API reads)

-- -----------------------------------------------------------------------------
-- Text normalization helper. No extension dependency: works on vanilla PG and on
-- Supabase without enabling `unaccent`.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.normalize_text(p_input text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $fn$
    SELECT NULLIF(
        regexp_replace(
            trim(
                lower(
                    translate(
                        coalesce(p_input, ''),
                        'ÁÀÄÂÃáàäâãÉÈËÊéèëêÍÌÏÎíìïîÓÒÖÔÕóòöôõÚÙÜÛúùüûÑñÇç',
                        'AAAAAaaaaaEEEEeeeeIIIIiiiiOOOOOoooooUUUUuuuuNnCc'
                    )
                )
            ),
            '\s+', ' ', 'g'
        ),
        ''
    );
$fn$;

COMMENT ON FUNCTION core.normalize_text IS
    'Lowercase, strip accents, collapse whitespace. Used for city/product matching.';

-- -----------------------------------------------------------------------------
-- Tenants
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.tenant (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         text NOT NULL UNIQUE,
    name         text NOT NULL,
    -- Per-tenant salt for customer PII hashing lives in the environment, never
    -- here. This column only records WHICH env key holds it.
    pii_salt_ref text NOT NULL DEFAULT 'PII_HASH_SALT',
    is_active    boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Users and auth. Argon2 hashes only - never plaintext, never recoverable.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.app_user (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    email         text NOT NULL,
    password_hash text NOT NULL,
    full_name     text,
    role          text NOT NULL CHECK (role IN ('owner', 'analyst', 'viewer')),
    is_active     boolean NOT NULL DEFAULT true,
    last_login_at timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_app_user_email_lower
    ON core.app_user (lower(email));

CREATE TABLE IF NOT EXISTS core.refresh_token (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES core.app_user(id) ON DELETE CASCADE,
    -- SHA-256 of the token. The token itself is never stored.
    token_hash text NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_refresh_token_user ON core.refresh_token (user_id);

CREATE TABLE IF NOT EXISTS core.invitation (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    email       text NOT NULL,
    role        text NOT NULL CHECK (role IN ('owner', 'analyst', 'viewer')),
    token_hash  text NOT NULL UNIQUE,
    invited_by  uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    expires_at  timestamptz NOT NULL,
    accepted_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Countries. Every display format comes from here. Nothing hardcoded in the UI.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.country (
    code             char(2) PRIMARY KEY,           -- ISO 3166-1 alpha-2
    name             text NOT NULL,
    currency_code    char(3) NOT NULL,              -- ISO 4217
    currency_symbol  text NOT NULL,
    decimal_places   smallint NOT NULL DEFAULT 0,
    thousands_sep    text NOT NULL DEFAULT '.',
    decimal_sep      text NOT NULL DEFAULT ',',
    date_format      text NOT NULL DEFAULT 'dd/MM/yyyy',
    locale           text NOT NULL,
    timezone         text NOT NULL,
    geo_level1_label text NOT NULL DEFAULT 'Departamento',
    is_supported     boolean NOT NULL DEFAULT true
);

INSERT INTO core.country (code, name, currency_code, currency_symbol, decimal_places,
                          thousands_sep, decimal_sep, date_format, locale, timezone,
                          geo_level1_label)
VALUES
    ('CO', 'Colombia',  'COP', '$',  0, '.', ',', 'dd/MM/yyyy', 'es-CO', 'America/Bogota',      'Departamento'),
    ('MX', 'México',    'MXN', '$',  2, ',', '.', 'dd/MM/yyyy', 'es-MX', 'America/Mexico_City', 'Estado'),
    ('PE', 'Perú',      'PEN', 'S/', 2, ',', '.', 'dd/MM/yyyy', 'es-PE', 'America/Lima',        'Departamento'),
    ('EC', 'Ecuador',   'USD', '$',  2, ',', '.', 'dd/MM/yyyy', 'es-EC', 'America/Guayaquil',   'Provincia'),
    ('CL', 'Chile',     'CLP', '$',  0, '.', ',', 'dd-MM-yyyy', 'es-CL', 'America/Santiago',    'Región'),
    ('PA', 'Panamá',    'USD', '$',  2, ',', '.', 'dd/MM/yyyy', 'es-PA', 'America/Panama',      'Provincia'),
    ('GT', 'Guatemala', 'GTQ', 'Q',  2, ',', '.', 'dd/MM/yyyy', 'es-GT', 'America/Guatemala',   'Departamento')
ON CONFLICT (code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Workspace: which countries a tenant actually operates in
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.workspace_country (
    tenant_id                 uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    country_code              char(2) NOT NULL REFERENCES core.country(code),
    is_active                 boolean NOT NULL DEFAULT true,
    -- Days a cohort needs before its delivery rate is considered mature.
    maturation_days           smallint NOT NULL DEFAULT 21,
    -- Worker writes a PROPOSAL here (measured p90). It never applies it alone.
    maturation_days_suggested smallint,
    maturation_suggested_at   timestamptz,
    activated_at              timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, country_code)
);

-- -----------------------------------------------------------------------------
-- Platform catalog: what Norte knows how to ingest, and at which tier.
--   tier 1 = official API / documented export
--   tier 2 = scheduled file drop (email inbox, manual upload)
--   tier 3 = authenticated session fetch - REQUIRES EXPLICIT USER CONSENT
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.platform (
    code             text PRIMARY KEY,
    name             text NOT NULL,
    tier             smallint NOT NULL CHECK (tier IN (1, 2, 3)),
    -- What this platform contributes: shipments | movements | ads | cs | catalog
    data_domains     text[] NOT NULL,
    requires_consent boolean NOT NULL DEFAULT false,
    docs_url         text,
    is_active        boolean NOT NULL DEFAULT true
);

INSERT INTO core.platform (code, name, tier, data_domains, requires_consent, docs_url) VALUES
    ('effi',        'Effi (fulfillment COD)',  3, ARRAY['shipments','movements'], true,  'docs/tier3-politica.md'),
    ('dropi',       'Dropi',                   2, ARRAY['shipments','movements'], false, NULL),
    ('manual_xlsx', 'Carga manual Excel/CSV',  2, ARRAY['shipments','movements'], false, NULL),
    ('shopify',     'Shopify',                 1, ARRAY['shipments','catalog'],   false, NULL),
    ('meta_ads',    'Meta Ads',                1, ARRAY['ads'],                   false, NULL),
    ('tiktok_ads',  'TikTok Ads',              1, ARRAY['ads'],                   false, NULL),
    ('google_ads',  'Google Ads',              1, ARRAY['ads'],                   false, NULL),
    ('cs_sheet',    'Confirmación CS (hoja)',  2, ARRAY['cs'],                    false, NULL)
ON CONFLICT (code) DO NOTHING;

-- Which platforms are actually available in each country
CREATE TABLE IF NOT EXISTS core.platform_country (
    platform_code text NOT NULL REFERENCES core.platform(code) ON DELETE CASCADE,
    country_code  char(2) NOT NULL REFERENCES core.country(code) ON DELETE CASCADE,
    notes         text,
    PRIMARY KEY (platform_code, country_code)
);

INSERT INTO core.platform_country (platform_code, country_code)
SELECT p.code, c.code
FROM core.platform p
CROSS JOIN core.country c
WHERE (p.code = 'effi'  AND c.code IN ('CO', 'EC', 'PA'))
   OR (p.code = 'dropi' AND c.code IN ('CO', 'MX', 'PE', 'EC', 'CL', 'PA', 'GT'))
   OR (p.code NOT IN ('effi', 'dropi'))
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- Stores (a tenant may run several shops inside one country)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.store (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    country_code char(2) NOT NULL REFERENCES core.country(code),
    name         text NOT NULL,
    external_ref text,
    is_active    boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, country_code, name)
);

-- -----------------------------------------------------------------------------
-- Connections: one configured data source for one tenant+country+platform.
-- NO CREDENTIALS ARE STORED HERE. `secret_ref` names an environment key.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.connection (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    country_code          char(2) NOT NULL REFERENCES core.country(code),
    platform_code         text NOT NULL REFERENCES core.platform(code),
    store_id              uuid REFERENCES core.store(id) ON DELETE SET NULL,
    name                  text NOT NULL,
    -- Name of the env var holding this connection's credentials. Never a value.
    secret_ref            text,
    -- Mandatory for tier-3 platforms. Enforced by trigger (a CHECK cannot query).
    consent_granted_at    timestamptz,
    consent_granted_by    uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    status                text NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'active', 'error', 'disabled')),
    last_sync_at          timestamptz,
    last_error            text,
    sync_interval_minutes integer NOT NULL DEFAULT 720,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, country_code, platform_code, name)
);

CREATE OR REPLACE FUNCTION core.enforce_tier3_consent()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_requires boolean;
BEGIN
    SELECT requires_consent INTO v_requires
    FROM core.platform WHERE code = NEW.platform_code;

    IF coalesce(v_requires, false) AND NEW.consent_granted_at IS NULL THEN
        RAISE EXCEPTION
            'Platform % requires explicit consent (consent_granted_at) before a connection can be created',
            NEW.platform_code
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS tr_connection_tier3_consent ON core.connection;
CREATE TRIGGER tr_connection_tier3_consent
    BEFORE INSERT OR UPDATE ON core.connection
    FOR EACH ROW EXECUTE FUNCTION core.enforce_tier3_consent();

CREATE INDEX IF NOT EXISTS ix_connection_tenant ON core.connection (tenant_id, country_code);

-- =============================================================================
-- RAW: ingestion bookkeeping
-- =============================================================================

CREATE TABLE IF NOT EXISTS raw.load_batch (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    connection_id uuid NOT NULL REFERENCES core.connection(id) ON DELETE CASCADE,
    source_name   text NOT NULL,               -- original filename or fetch label
    kind          text NOT NULL CHECK (kind IN ('shipments', 'movements', 'ads', 'cs')),
    -- SHA-256 of the raw file bytes. THE idempotency key: same bytes = same batch.
    content_hash  char(64) NOT NULL,
    status        text NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'ok', 'failed', 'skipped')),
    rows_total    integer NOT NULL DEFAULT 0,
    rows_inserted integer NOT NULL DEFAULT 0,
    rows_updated  integer NOT NULL DEFAULT 0,
    rows_skipped  integer NOT NULL DEFAULT 0,
    rows_failed   integer NOT NULL DEFAULT 0,
    report        jsonb NOT NULL DEFAULT '{}'::jsonb,
    error         text,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    -- Two concurrent uploads of the same bytes: exactly one wins this constraint.
    UNIQUE (tenant_id, connection_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ix_load_batch_tenant_started
    ON raw.load_batch (tenant_id, started_at DESC);

-- Raw rows kept for audit / reprocessing. Never exposed through mart.
CREATE TABLE IF NOT EXISTS raw.source_row (
    id         bigserial PRIMARY KEY,
    batch_id   uuid NOT NULL REFERENCES raw.load_batch(id) ON DELETE CASCADE,
    tenant_id  uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    row_number integer NOT NULL,
    payload    jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_source_row_batch ON raw.source_row (batch_id);

-- Money fields that changed value between loads. New wins, but leave a trail.
CREATE TABLE IF NOT EXISTS raw.load_discrepancy (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    batch_id    uuid NOT NULL REFERENCES raw.load_batch(id) ON DELETE CASCADE,
    entity      text NOT NULL CHECK (entity IN ('shipment', 'movement')),
    entity_key  text NOT NULL,          -- tracking_number or movement external ref
    field_name  text NOT NULL,
    old_value   text,
    new_value   text,
    detected_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_load_discrepancy_batch ON raw.load_discrepancy (batch_id);

-- Worker job bookkeeping
CREATE TABLE IF NOT EXISTS raw.job_run (
    id          bigserial PRIMARY KEY,
    job_name    text NOT NULL,
    tenant_id   uuid REFERENCES core.tenant(id) ON DELETE CASCADE,
    status      text NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'ok', 'failed', 'skipped')),
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    result      jsonb NOT NULL DEFAULT '{}'::jsonb,
    error       text
);
CREATE INDEX IF NOT EXISTS ix_job_run_name_started ON raw.job_run (job_name, started_at DESC);
