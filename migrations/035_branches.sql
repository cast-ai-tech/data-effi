-- =============================================================================
-- Data Effi - 035 - Branches.
--
-- THE PROBLEM THIS SOLVES
-- The scope ladder was company -> country -> store -> connection. An operator
-- with three warehouses in Colombia had nowhere to say so: `core.store` is a
-- brand or a storefront, it carries no address, no manager and no cost centre,
-- and two stores shipping out of the same warehouse had no way to be related.
--
-- THE SHAPE
--   core.tenant     the company
--     core.workspace_country   a country it operates in
--       core.branch            a physical place inside that country   <- NEW
--         core.store           a brand or storefront                  <- now optional child
--           core.connection    a data source
--
-- WHY `store.branch_id` IS NULLABLE
-- A store does not need a branch: a pure dropshipping brand ships from the
-- supplier and has no premises at all. Forcing every store through an invented
-- "Principal" branch would make the roll-up count places that do not exist.
--
-- WHY THE COMPOSITE FOREIGN KEYS
-- Two of them, and neither is decoration:
--   branch -> workspace_country   a branch cannot exist in a country the company
--                                 does not operate in. Without this, deactivating
--                                 a country leaves branches orphaned in it.
--   store  -> branch              (branch_id, tenant_id, country_code) instead of
--                                 just (branch_id), so a store CANNOT point at a
--                                 branch of another company or another country.
--                                 A plain FK on branch_id would allow exactly the
--                                 cross-tenant leak that row-level security is
--                                 there to prevent, and it would be invisible.
-- That second one needs `UNIQUE (id, tenant_id, country_code)` on branch, which
-- is redundant against the primary key but is what PostgreSQL requires to point
-- a composite foreign key at it. It costs one index and removes the need for a
-- trigger.
--
-- ROW-LEVEL SECURITY
-- `core.branch` is tenant data and gets the same treatment as `core.store`:
-- ENABLE, FORCE, and the identical `tenant_isolation` policy from migration 007.
-- It is not in the "read before a tenant is known" group - nothing reads a
-- branch during login.
--
-- Depends on: 001-034. Idempotent.
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.branch (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    country_code char(2) NOT NULL REFERENCES core.country(code),
    name         text NOT NULL,
    -- The operator's own code for the place: cost centre, branch number,
    -- whatever their accountant already uses. Free text on purpose.
    cost_center  text,
    address      text,
    city         text,
    manager_name text,
    phone        text,
    -- A branch that holds stock, as opposed to an office or a point of sale.
    is_warehouse boolean NOT NULL DEFAULT false,
    is_active    boolean NOT NULL DEFAULT true,
    notes        text,
    created_at   timestamptz NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, country_code, name),

    CONSTRAINT branch_country_is_operated
        FOREIGN KEY (tenant_id, country_code)
        REFERENCES core.workspace_country (tenant_id, country_code)
        ON DELETE CASCADE,

    -- Referenced by core.store below. See the header.
    CONSTRAINT branch_id_tenant_country_key UNIQUE (id, tenant_id, country_code)
);

COMMENT ON TABLE core.branch IS
    'A physical place a company operates from inside one country: warehouse, office or point of sale.';
COMMENT ON COLUMN core.branch.cost_center IS
    'The operator''s own identifier for the place. Never interpreted by the application.';
COMMENT ON COLUMN core.branch.is_warehouse IS
    'True when stock lives here. An office or a point of sale is false.';

CREATE INDEX IF NOT EXISTS ix_branch_tenant ON core.branch (tenant_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS ix_branch_country ON core.branch (tenant_id, country_code);

-- -----------------------------------------------------------------------------
-- Stores may belong to a branch.
-- -----------------------------------------------------------------------------
ALTER TABLE core.store ADD COLUMN IF NOT EXISTS branch_id uuid;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'core.store'::regclass
          AND conname = 'store_branch_same_tenant_and_country'
    ) THEN
        -- SET NULL names the column ON PURPOSE. A plain `ON DELETE SET NULL`
        -- over a composite foreign key nulls EVERY column in it, and both
        -- tenant_id and country_code are NOT NULL - so deleting a branch would
        -- fail with a constraint violation on rows that merely referenced it.
        -- Naming the column (PostgreSQL 15+) nulls only the link.
        ALTER TABLE core.store
            ADD CONSTRAINT store_branch_same_tenant_and_country
            FOREIGN KEY (branch_id, tenant_id, country_code)
            REFERENCES core.branch (id, tenant_id, country_code)
            ON DELETE SET NULL (branch_id);
    END IF;
END;
$do$;

CREATE INDEX IF NOT EXISTS ix_store_branch ON core.store (branch_id)
    WHERE branch_id IS NOT NULL;

COMMENT ON COLUMN core.store.branch_id IS
    'The place this store ships from, when there is one. NULL for a store with no premises.';

-- -----------------------------------------------------------------------------
-- Row-level security, identical to every other tenant table (migration 007).
-- -----------------------------------------------------------------------------
ALTER TABLE core.branch ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.branch FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON core.branch;
CREATE POLICY tenant_isolation ON core.branch
    USING (tenant_id = core.current_tenant_id() OR core.is_service_context())
    WITH CHECK (tenant_id = core.current_tenant_id() OR core.is_service_context());

-- ALTER DEFAULT PRIVILEGES in 007 already covers tables created later in this
-- schema, but only for the role that ran it. Granted explicitly so this
-- migration does not depend on who applies it.
GRANT SELECT, INSERT, UPDATE, DELETE ON core.branch TO norte_app;
