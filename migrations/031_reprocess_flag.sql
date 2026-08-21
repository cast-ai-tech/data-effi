-- =============================================================================
-- Data Effi - 031 - Letting the operator re-run a file they already uploaded.
--
-- `raw.load_batch` refuses the same bytes twice, and that guard is what keeps an
-- accidental double upload from doubling somebody's revenue. It should stay.
--
-- But the ENGINE changes. Contact encryption arrived in 015; migration 023 found
-- that fees were double-counted; the movement date turned out to be inverted.
-- After each of those, the same file legitimately produces different rows - and
-- the operator had no way to say so. The protection became the thing standing
-- between them and correct data: "Ya estaba cargado. No se insertó nada, que es
-- lo correcto" is a true sentence and the wrong outcome.
--
-- So the request carries the intent. Re-running is safe by construction, which
-- is the only reason it can be offered at all: status advances and never
-- regresses, money keeps the newest value and records a discrepancy instead of
-- adding, static fields fill gaps without overwriting. A file processed twice
-- converges to the same state as a file processed once.
--
-- Depends on: 030. Idempotent.
-- =============================================================================

ALTER TABLE raw.upload_job
    ADD COLUMN IF NOT EXISTS reprocess boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN raw.upload_job.reprocess IS
    'El operador pidió volver a procesar un archivo ya cargado, normalmente
     porque el motor cambió desde la carga anterior. Reabre el lote existente en
     vez de crear uno nuevo.';
