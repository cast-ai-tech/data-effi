-- =============================================================================
-- Data Effi - 019 - `dispatched_at` held the creation date.
--
-- THE BUG
-- `pipeline/profiles.py` mapped Effi's "Fecha de creación" to `dispatched_at`.
-- Read against the operator's own export, the three dates are:
--
--     Fecha de creación          2026-08-01 12:07:32   the ERP created the guide
--     Fecha de envío             2026-08-01 00:00:00   same day, date only
--     Fecha relación de despacho 2026-08-11 12:30:48   the goods actually left
--
-- So a column named "dispatched" carried a timestamp taken ten days before the
-- dispatch. Measured over the real 1,649 guides, creation->dispatch reads 0.52
-- days through `dispatched_at` and 6.30 days through `dispatched_batch_at`. A
-- factor of twelve, in the direction that flatters the operation.
--
-- WHY IT MATTERED MORE FROM TODAY
-- `mart.v_fulfillment_sla` escaped because it already used
-- `dispatched_batch_at`. But the operator has just asked to filter every tab by
-- dispatch date, and that filter would have read the misnamed column and shown
-- half a day of preparation time where there are six.
--
-- THE FIX
-- The ERP's creation timestamp is a real, useful fact - it is simply not the
-- dispatch. It gets its own column, and `dispatched_at` is repopulated from the
-- dispatch relation, so the name states what the value is.
--
-- Depends on: 018. Idempotent.
-- =============================================================================

ALTER TABLE core.shipment
    ADD COLUMN IF NOT EXISTS created_at_source timestamptz;

COMMENT ON COLUMN core.shipment.created_at_source IS
    'When the source ERP created the guide, to the second. Distinct from
     created_date, which is the calendar day the cohort belongs to. NOT a
     dispatch date - see migration 019.';

COMMENT ON COLUMN core.shipment.dispatched_at IS
    'When the goods actually left, from the dispatch relation. Until migration
     019 this column was fed the ERP creation timestamp, which understated
     preparation time twelvefold.';

-- Move the misfiled values across, then correct the column in place. Both
-- guarded so re-running changes nothing.
UPDATE core.shipment
   SET created_at_source = dispatched_at
 WHERE created_at_source IS NULL
   AND dispatched_at IS NOT NULL
   AND dispatched_batch_at IS NOT NULL
   -- The signature of the bug: a "dispatch" that happened before, or on, the
   -- day the guide was created. A real dispatch cannot precede its own guide.
   AND dispatched_at < dispatched_batch_at;

UPDATE core.shipment
   SET dispatched_at = dispatched_batch_at
 WHERE dispatched_batch_at IS NOT NULL
   AND (dispatched_at IS NULL OR dispatched_at < dispatched_batch_at);
