-- =============================================================================
-- Norte - 007 - Row-Level Security: the second wall.
--
-- The first wall is application code: every query filters by the tenant in the
-- JWT, and every mart view filters by core.current_tenant_id(). This migration
-- assumes that wall will eventually be breached by a bug, and makes the database
-- refuse to serve another tenant's rows even when asked directly.
--
-- FORCE ROW LEVEL SECURITY is deliberate: without FORCE, the table owner (the
-- role the API connects as) bypasses every policy, which would make this whole
-- file decorative.
--
-- TWO CONTEXTS ARE ALLOWED THROUGH:
--   1. A tenant context - `SET norte.tenant_id = '<uuid>'`. Sees exactly that
--      tenant's rows.
--   2. A service context - `SET norte.service = 'on'`. Used by the worker and
--      the ingestion queue, which legitimately process every tenant.
-- With neither set, these tables return ZERO rows. Fail closed.
--
-- Why the service escape hatch is not a hole: the only untrusted SQL that ever
-- reaches this database is NL->SQL output, and that runs as `norte_readonly`,
-- which (a) has no GRANT on core or raw at all, and (b) is fed only statements
-- the validator has already confirmed are a single SELECT over mart views - SET
-- and set_config are rejected outright.
--
-- Depends on: 001-006. Idempotent.
-- =============================================================================

CREATE OR REPLACE FUNCTION core.is_service_context()
RETURNS boolean
LANGUAGE sql
STABLE
AS $fn$
    SELECT coalesce(current_setting('norte.service', true), '') = 'on';
$fn$;

COMMENT ON FUNCTION core.is_service_context IS
    'True inside the worker and the ingestion queue, which process every tenant.';

-- -----------------------------------------------------------------------------
-- Apply the same policy shape to every tenant-scoped table.
-- -----------------------------------------------------------------------------
DO $do$
DECLARE
    v_table text;
    v_tables text[] := ARRAY[
        'core.shipment',
        'core.movement',
        'core.ad_spend',
        'core.cs_interaction',
        'core.carrier',
        'core.geo',
        'core.product',
        'core.product_alias',
        'core.supplier',
        'core.store',
        'core.connection',
        'core.workspace_country',
        'raw.load_batch',
        'raw.source_row',
        'raw.load_discrepancy',
        'raw.upload_job',
        'raw.ai_cache',
        'raw.ai_usage'
    ];
BEGIN
    FOREACH v_table IN ARRAY v_tables LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', v_table);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', v_table);

        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s', v_table);
        EXECUTE format($sql$
            CREATE POLICY tenant_isolation ON %s
            USING (tenant_id = core.current_tenant_id() OR core.is_service_context())
            WITH CHECK (tenant_id = core.current_tenant_id() OR core.is_service_context())
        $sql$, v_table);
    END LOOP;
END;
$do$;

-- -----------------------------------------------------------------------------
-- Auth tables stay outside RLS on purpose.
--
-- core.app_user, core.tenant, core.refresh_token, core.invitation and
-- raw.auth_event are read BEFORE a tenant is known - that is what login is. A
-- policy keyed on the tenant would make logging in impossible. They are
-- protected the ordinary way: only the API role can reach them, and every query
-- against them is by email or token hash, never by tenant.
-- -----------------------------------------------------------------------------

-- =============================================================================
-- Roles.
--
-- Passwords are set by scripts/setup_roles.py from the environment. A password
-- in a migration file is a password in git history.
--
-- `norte_app` is the role the API and worker connect as, and it is deliberately
-- NOT a superuser and NOT the owner of these tables. PostgreSQL exempts both
-- from row-level security unconditionally - FORCE included - so an application
-- connecting as the superuser turns every policy above into decoration. This
-- was exactly the state this project was in before this migration.
-- =============================================================================
DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'norte_app') THEN
        CREATE ROLE norte_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
    -- Re-assert on re-run: someone may have granted these by hand.
    ALTER ROLE norte_app WITH NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'norte_readonly') THEN
        CREATE ROLE norte_readonly WITH LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS;
    END IF;
    ALTER ROLE norte_readonly WITH NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
END;
$do$;

-- The application role: full DML on the data schemas, no DDL, no ownership.
GRANT USAGE ON SCHEMA core, raw, stg, mart TO norte_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core, raw TO norte_app;
GRANT SELECT ON ALL TABLES IN SCHEMA stg, mart TO norte_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core, raw TO norte_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA core, raw TO norte_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA core, raw
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO norte_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA stg, mart GRANT SELECT ON TABLES TO norte_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA core, raw GRANT USAGE, SELECT ON SEQUENCES TO norte_app;

COMMENT ON ROLE norte_app IS
    'Application role for the API and worker. Subject to row-level security.';

-- Revoke everything, then grant back exactly one thing: SELECT on mart.
REVOKE ALL ON SCHEMA core, raw, stg FROM norte_readonly;
REVOKE ALL ON ALL TABLES IN SCHEMA core FROM norte_readonly;
REVOKE ALL ON ALL TABLES IN SCHEMA raw FROM norte_readonly;
REVOKE ALL ON ALL TABLES IN SCHEMA stg FROM norte_readonly;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA core FROM norte_readonly;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT USAGE ON SCHEMA mart TO norte_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA mart TO norte_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA mart GRANT SELECT ON TABLES TO norte_readonly;

-- The views are security-definer (the PostgreSQL default), so they read core
-- and raw as their owner. That is what lets the read-only role see aggregates
-- without holding a single grant on the underlying tables.

-- Its own guard rails, applied at role level so they survive a bad code path.
ALTER ROLE norte_readonly SET search_path = mart;
ALTER ROLE norte_readonly SET statement_timeout = '5s';
ALTER ROLE norte_readonly SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE norte_readonly SET default_transaction_read_only = on;

COMMENT ON ROLE norte_readonly IS
    'NL->SQL execution role. SELECT on mart only, 5s statement timeout, read-only.';
