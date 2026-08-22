-- =============================================================================
-- Data Effi - 036 - Roles at the organisation level.
--
-- THE PROBLEM THIS SOLVES
-- Access to the holding was one boolean: `core.app_user.is_org_admin`. So the
-- only two answers to "let this person see the whole group" were "make them an
-- administrator of everything" or "add them to each company one by one". The
-- operator's real case is neither: a partner who must SEE the consolidated
-- numbers of every company and TOUCH nothing.
--
-- THE SHAPE
--   admin     manages companies and people. What is_org_admin meant.
--   analyst   reads the consolidated roll-up of every company in the org.
--   viewer    reads the consolidated roll-up. Nothing else, ever.
--
-- WHAT THIS DOES NOT GRANT, AND IT MATTERS
-- None of the three opens a company's internal data. `core.membership` still
-- decides who reads orders, customers and uploads, per company and per country.
-- An org `viewer` with no membership sees totals and cannot open a single
-- shipment behind them. The roll-up keeps reading each company in its own scoped
-- transaction (see api/routers/org.py) - no policy is widened by this migration.
--
-- WHY `is_org_admin` SURVIVES
-- Expand/contract. The database and the API deploy separately here (Render with
-- autoDeploy off), so between applying this and shipping the API there is a
-- window where the running code still reads that column. Dropping it now turns
-- that window into "no administrator can log in". A later migration removes it,
-- once the API reads core.org_membership in production. Until then the trigger
-- below keeps both in agreement, so neither source can go stale.
--
-- Depends on: 001-035. Idempotent.
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.org_membership (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES core.app_user(id) ON DELETE CASCADE,
    org_id     uuid NOT NULL REFERENCES core.org(id) ON DELETE CASCADE,
    role       text NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer')),
    is_active  boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, org_id)
);

COMMENT ON TABLE core.org_membership IS
    'What a person may do ACROSS the companies of one org. Never grants access to a company''s own data - core.membership does that.';
COMMENT ON COLUMN core.org_membership.role IS
    'admin manages companies and people; analyst and viewer read the consolidated roll-up.';

CREATE INDEX IF NOT EXISTS ix_org_membership_user ON core.org_membership (user_id)
    WHERE is_active;
CREATE INDEX IF NOT EXISTS ix_org_membership_org ON core.org_membership (org_id)
    WHERE is_active;

-- =============================================================================
-- Backfill: today's administrators become explicit rows.
--
-- Runs once. A person already holding a row is left exactly as they are, so
-- re-running this never demotes or promotes anybody.
-- =============================================================================
INSERT INTO core.org_membership (user_id, org_id, role)
SELECT u.id, u.org_id, 'admin'
FROM core.app_user u
WHERE u.is_org_admin
  AND u.org_id IS NOT NULL
ON CONFLICT (user_id, org_id) DO NOTHING;

-- =============================================================================
-- Keep the deprecated boolean in step, in both directions.
--
-- This exists ONLY for the deploy window described in the header, and dies with
-- the column. Without it, an admin created through the new table cannot log in
-- to the currently deployed API, and one created through the old column is
-- invisible to the new one.
-- =============================================================================
CREATE OR REPLACE FUNCTION core.sync_is_org_admin()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, pg_temp
AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        UPDATE core.app_user SET is_org_admin = false WHERE id = OLD.user_id;
        RETURN OLD;
    END IF;

    UPDATE core.app_user
    SET is_org_admin = (NEW.role = 'admin' AND NEW.is_active)
    WHERE id = NEW.user_id;
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_sync_is_org_admin ON core.org_membership;
CREATE TRIGGER trg_sync_is_org_admin
    AFTER INSERT OR UPDATE OR DELETE ON core.org_membership
    FOR EACH ROW EXECUTE FUNCTION core.sync_is_org_admin();

-- =============================================================================
-- The helper the API leans on, mirroring core.user_workspaces from 032.
-- =============================================================================
CREATE OR REPLACE FUNCTION core.user_org_role(p_user_id uuid)
RETURNS TABLE (
    org_id        uuid,
    org_slug      text,
    org_name      text,
    base_currency char(3),
    role          text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = core, pg_temp
AS $fn$
    SELECT o.id, o.slug, o.name, o.base_currency, m.role
    FROM core.org_membership m
    JOIN core.org o ON o.id = m.org_id
    WHERE m.user_id = p_user_id
      AND m.is_active
      AND o.is_active;
$fn$;

COMMENT ON FUNCTION core.user_org_role IS
    'The organisation a person belongs to and what they may do across it. Read at login, before any company is chosen.';

-- SECURITY DEFINER for the same reason as core.user_workspaces: this runs before
-- a tenant context exists. core.org_membership itself stays outside row-level
-- security, like core.org, core.tenant and core.membership - all of them are read
-- before the caller has chosen a company, and all are queried by user or org id,
-- never by an untrusted parameter.
REVOKE ALL ON FUNCTION core.user_org_role(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.user_org_role(uuid) TO norte_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON core.org_membership TO norte_app;
