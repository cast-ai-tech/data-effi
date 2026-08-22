-- =============================================================================
-- Data Effi - 032 - Organizations, memberships and country scope.
--
-- THE PROBLEM THIS SOLVES
-- Until now a person WAS a tenant row: `core.app_user.tenant_id` was NOT NULL
-- and `ux_app_user_email_lower` made an email unique across the whole database.
-- So the same human could not be a partner in two companies, and nobody could
-- see more than one company at a time - which is exactly what an operator with
-- four partnerships (Colombia, Ecuador, Guatemala, Guatemala+Honduras) needs.
--
-- THE SHAPE
--   core.org          the holding. One per operator group.
--   core.tenant       a company inside it (`org_id`). Still THE isolation unit.
--   core.app_user     a person. Global identity, one password, no tenant.
--   core.membership   person x company: role, which countries, what share.
--
-- WHAT DELIBERATELY DID NOT CHANGE
-- Row-level security still keys on `tenant_id` and `norte.tenant_id`, untouched.
-- A consolidated view across companies is built by READING EACH COMPANY IN ITS
-- OWN SCOPED TRANSACTION and summing in the API - never by widening a policy.
-- That keeps the blast radius of a bug at one company instead of the holding.
--
-- ROLES
--   owner     everything inside its company, including inviting people.
--   analyst   reads, uploads, edits configuration. No invitations.
--   viewer    reads. Nothing else.
--   uploader  uploads files and sees the status of its own loads - and NOTHING
--             else. This is the "she loads the data but must not see the
--             numbers" case, and it is why roles stopped being a ladder:
--             uploader is not "below viewer", it is sideways.
--
-- COUNTRY SCOPE
-- `membership.country_scope` NULL means the whole company. A non-empty array
-- limits the person to those countries, enforced in the API for every endpoint
-- that takes `country` and by filtering the multi-country endpoints.
--
-- Depends on: 001-031. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- The holding.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.org (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          text NOT NULL UNIQUE,
    name          text NOT NULL,
    -- Everything consolidated is expressed here. Per-company numbers stay in
    -- their own currency; only the roll-up converts, using core.fx_rate.
    base_currency char(3) NOT NULL DEFAULT 'USD',
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE core.org IS
    'A group of companies under one operator. Never an isolation boundary - core.tenant is.';

-- -----------------------------------------------------------------------------
-- Tenants belong to an org, and carry the partnership description.
-- -----------------------------------------------------------------------------
ALTER TABLE core.tenant ADD COLUMN IF NOT EXISTS org_id uuid REFERENCES core.org(id);
ALTER TABLE core.tenant ADD COLUMN IF NOT EXISTS notes text;
CREATE INDEX IF NOT EXISTS ix_tenant_org ON core.tenant (org_id);

-- -----------------------------------------------------------------------------
-- People become global identities.
--
-- `tenant_id` survives as the DEFAULT company (which one opens on login), so
-- every existing query and token keeps working while the API migrates to
-- memberships. It is nullable now: an org admin belongs to no single company.
-- -----------------------------------------------------------------------------
ALTER TABLE core.app_user ADD COLUMN IF NOT EXISTS org_id uuid REFERENCES core.org(id);
ALTER TABLE core.app_user ADD COLUMN IF NOT EXISTS is_org_admin boolean NOT NULL DEFAULT false;
ALTER TABLE core.app_user ALTER COLUMN tenant_id DROP NOT NULL;

DO $do$
BEGIN
    -- The old CHECK does not know about 'uploader'.
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'core.app_user'::regclass AND conname = 'app_user_role_check'
    ) THEN
        ALTER TABLE core.app_user DROP CONSTRAINT app_user_role_check;
    END IF;
    ALTER TABLE core.app_user
        ADD CONSTRAINT app_user_role_check
        CHECK (role IN ('owner', 'analyst', 'viewer', 'uploader'));

    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'core.invitation'::regclass AND conname = 'invitation_role_check'
    ) THEN
        ALTER TABLE core.invitation DROP CONSTRAINT invitation_role_check;
    END IF;
    ALTER TABLE core.invitation
        ADD CONSTRAINT invitation_role_check
        CHECK (role IN ('owner', 'analyst', 'viewer', 'uploader'));
END;
$do$;

CREATE INDEX IF NOT EXISTS ix_app_user_org ON core.app_user (org_id);

-- -----------------------------------------------------------------------------
-- Membership: the person x company grant.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.membership (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES core.app_user(id) ON DELETE CASCADE,
    tenant_id     uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    role          text NOT NULL CHECK (role IN ('owner', 'analyst', 'viewer', 'uploader')),
    -- NULL = every country the company operates in. Never an empty array: that
    -- would mean "no access", which is what deleting the membership is for.
    country_scope char(2)[],
    -- The partner's stake, for showing "your share" on the roll-up. Reporting
    -- only: it never gates access to anything.
    share_pct     numeric(6,3) CHECK (share_pct IS NULL OR (share_pct > 0 AND share_pct <= 100)),
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, tenant_id),
    CONSTRAINT membership_scope_not_empty
        CHECK (country_scope IS NULL OR cardinality(country_scope) > 0)
);

CREATE INDEX IF NOT EXISTS ix_membership_user ON core.membership (user_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS ix_membership_tenant ON core.membership (tenant_id) WHERE is_active;

COMMENT ON TABLE core.membership IS
    'Which companies a person may enter, as what, and limited to which countries.';
COMMENT ON COLUMN core.membership.country_scope IS
    'NULL = the whole company. Otherwise the only countries this person may read.';
COMMENT ON COLUMN core.membership.share_pct IS
    'Partner stake, for reporting "your share". Never an access control input.';

-- -----------------------------------------------------------------------------
-- A refresh token remembers WHICH company the session was standing in.
--
-- Without this, refreshing at 3am silently teleports a partner from the company
-- they were reading back to their default one - and worse, hands them a token
-- minted with that company's role. The tenant is part of the session, so it
-- lives with the session.
-- -----------------------------------------------------------------------------
ALTER TABLE core.refresh_token
    ADD COLUMN IF NOT EXISTS tenant_id uuid REFERENCES core.tenant(id) ON DELETE CASCADE;

-- -----------------------------------------------------------------------------
-- Invitations carry the same grant, so accepting one reproduces it exactly.
-- -----------------------------------------------------------------------------
ALTER TABLE core.invitation ADD COLUMN IF NOT EXISTS country_scope char(2)[];
ALTER TABLE core.invitation ADD COLUMN IF NOT EXISTS share_pct numeric(6,3);

-- An invitation is per company: the same email may legitimately hold an open
-- invitation to two of them. The old assumption was one invitation per person.
CREATE UNIQUE INDEX IF NOT EXISTS ux_invitation_open
    ON core.invitation (tenant_id, lower(email))
    WHERE accepted_at IS NULL;

-- =============================================================================
-- Backfill: every existing tenant joins one org, every user gets a membership.
--
-- Runs once. On a database that already has orgs it changes nothing.
-- =============================================================================
DO $do$
DECLARE
    v_org uuid;
BEGIN
    SELECT id INTO v_org FROM core.org WHERE slug = 'principal';
    IF v_org IS NULL THEN
        INSERT INTO core.org (slug, name, base_currency)
        VALUES ('principal', 'Organización principal', 'USD')
        RETURNING id INTO v_org;
    END IF;

    UPDATE core.tenant SET org_id = v_org WHERE org_id IS NULL;
    UPDATE core.app_user SET org_id = v_org WHERE org_id IS NULL;

    INSERT INTO core.membership (user_id, tenant_id, role)
    SELECT u.id, u.tenant_id, u.role
    FROM core.app_user u
    WHERE u.tenant_id IS NOT NULL
    ON CONFLICT (user_id, tenant_id) DO NOTHING;

    -- The first owner of the deployment runs the holding. Anyone added later is
    -- promoted explicitly, never implicitly.
    UPDATE core.app_user u
    SET is_org_admin = true
    WHERE u.role = 'owner'
      AND NOT EXISTS (SELECT 1 FROM core.app_user WHERE is_org_admin)
      AND u.created_at = (SELECT min(created_at) FROM core.app_user WHERE role = 'owner');
END;
$do$;

-- =============================================================================
-- Helpers the API leans on.
-- =============================================================================

-- Every company a person may enter, with the grant attached. One round trip on
-- login, and the only place that decides what "my workspaces" means.
CREATE OR REPLACE FUNCTION core.user_workspaces(p_user_id uuid)
RETURNS TABLE (
    tenant_id     uuid,
    tenant_slug   text,
    tenant_name   text,
    org_id        uuid,
    role          text,
    country_scope char(2)[],
    share_pct     numeric,
    countries     char(2)[]
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = core, pg_temp
AS $fn$
    SELECT
        t.id,
        t.slug,
        t.name,
        t.org_id,
        m.role,
        m.country_scope,
        m.share_pct,
        coalesce(
            (SELECT array_agg(wc.country_code ORDER BY wc.country_code)
             FROM core.workspace_country wc
             WHERE wc.tenant_id = t.id AND wc.is_active),
            ARRAY[]::char(2)[]
        )
    FROM core.membership m
    JOIN core.tenant t ON t.id = m.tenant_id
    WHERE m.user_id = p_user_id
      AND m.is_active
      AND t.is_active
    ORDER BY t.name;
$fn$;

COMMENT ON FUNCTION core.user_workspaces IS
    'The companies a person may open, with role, country scope and stake.';

-- SECURITY DEFINER because core.workspace_country is under RLS and this runs
-- before any tenant context exists - login is exactly the moment we do not yet
-- know which company the caller is entering.
REVOKE ALL ON FUNCTION core.user_workspaces(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.user_workspaces(uuid) TO norte_app;

-- Every company in an org. The consolidated roll-up calls this, then opens one
-- scoped transaction per row it returns.
CREATE OR REPLACE FUNCTION core.org_tenants(p_org_id uuid)
RETURNS TABLE (tenant_id uuid, tenant_slug text, tenant_name text)
LANGUAGE sql
STABLE
AS $fn$
    SELECT t.id, t.slug, t.name
    FROM core.tenant t
    WHERE t.org_id = p_org_id AND t.is_active
    ORDER BY t.name;
$fn$;

GRANT EXECUTE ON FUNCTION core.org_tenants(uuid) TO norte_app;

-- core.org, core.membership and core.tenant stay outside row-level security for
-- the same reason core.app_user does: they are read BEFORE a tenant context
-- exists. Reaching them requires the API role, and every query against them is
-- keyed by user id or org id - never by an untrusted parameter.
