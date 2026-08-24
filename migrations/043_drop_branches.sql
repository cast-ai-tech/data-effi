-- =============================================================================
-- Data Effi - 043 - Branches are gone. The ladder is org -> company -> country.
--
-- THE PROBLEM THIS SOLVES
-- Migration 035 put a physical place (`core.branch`) between a country and a
-- store. It was never filled in production and it made the organisation chart
-- read as four levels when the operator thinks in three: the holding, the
-- companies inside it, and the countries each company operates in. A store is
-- a brand, a connection is a data source - neither needs a warehouse above it.
--
-- THE SHAPE, AFTER THIS
--   core.org                 the holding
--     core.tenant            a company inside it - still THE isolation unit
--       core.workspace_country   a country that company operates in
--         core.store         a brand or storefront
--           core.connection  a data source
--
-- ORDER MATTERS
-- `core.store.branch_id` points at `core.branch` through a composite foreign
-- key, so the column goes first and the table second. Both steps are guarded:
-- on a database where 035 never ran (a fresh test database applies every file
-- in order, so it did) this is a no-op.
--
-- Depends on: 001-042. Idempotent.
-- =============================================================================

ALTER TABLE core.store DROP CONSTRAINT IF EXISTS store_branch_same_tenant_and_country;
DROP INDEX IF EXISTS core.ix_store_branch;
ALTER TABLE core.store DROP COLUMN IF EXISTS branch_id;

DROP TABLE IF EXISTS core.branch;
