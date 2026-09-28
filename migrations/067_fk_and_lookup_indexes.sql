-- =============================================================================
-- Data Effi - 067 - Índices que faltaban en llaves foráneas y búsquedas de ingesta,
--                   y dos índices duplicados que solo cobraban escritura.
--
-- QUÉ PASABA
--
-- 1. REPROCESAR UN ARCHIVO RECORRÍA TODOS LOS MOVIMIENTOS. `clear_batch_rows`
--    (pipeline/store_pg.py) hace `DELETE FROM core.movement WHERE batch_id = $1`
--    y core.movement.batch_id no tenía índice: un seq scan de la tabla entera -
--    de todos los tenants - por cada reproceso.
--
-- 2. BORRAR UNA CONEXIÓN ESCALABA MAL. core.connection -> raw.load_batch es ON
--    DELETE CASCADE y raw.load_batch.connection_id no tenía índice propio (la
--    UNIQUE empieza por tenant_id). Y cada lote borrado dispara su ON DELETE SET
--    NULL sobre core.shipment.first_batch_id / last_batch_id, core.movement,
--    core.ad_spend, core.cs_interaction y raw.upload_job, ninguno indexado: un
--    seq scan de cada tabla POR LOTE. Una conexión con cientos de lotes podía
--    agotar el statement_timeout y el DELETE fallaba entero.
--
-- 3. ENLAZAR UN MOVIMIENTO A SU GUÍA POR EL NÚMERO DEL TRANSPORTADOR. El upsert
--    de movimientos busca
--        WHERE connection_id = $1
--          AND (tracking_number = $2 OR carrier_tracking_number = $2)
--    El índice existente de carrier_tracking_number empieza por tenant_id, que la
--    consulta no trae, así que la rama OR no podía usar índice y PostgreSQL
--    recorría todas las guías de la conexión POR CADA movimiento cargado. Con
--    (connection_id, carrier_tracking_number) las dos ramas quedan indexadas y el
--    planificador puede hacer un BitmapOr.
--
-- 4. core.cs_interaction.shipment_id (ON DELETE SET NULL desde core.shipment)
--    tampoco tenía índice.
--
-- 5. DOS ÍNDICES DUPLICADOS en core.shipment, la tabla más grande y la más
--    escrita:
--      * ix_shipment_conn_tracking (004) = el índice de la UNIQUE
--        (connection_id, tracking_number) de 002, columna por columna.
--      * ix_shipment_orders_browse (015) = ix_shipment_tenant_country_date (002),
--        mismas columnas y mismo orden.
--    No aportan ningún plan y cada INSERT/UPDATE de una guía los mantiene.
--
-- NOTA OPERATIVA. Son CREATE INDEX normales (no CONCURRENTLY) porque
-- scripts/migrate.py corre cada archivo como un solo bloque y CONCURRENTLY no
-- puede ir dentro de una transacción. Cada índice toma un lock SHARE sobre su
-- tabla mientras se construye (bloquea escrituras, no lecturas): aplicarla
-- fuera de horas de carga.
--
-- Depende de: 002, 004, 005, 008, 015. Idempotente.
-- =============================================================================

-- 1. Reproceso de un lote
CREATE INDEX IF NOT EXISTS ix_movement_batch
    ON core.movement (batch_id) WHERE batch_id IS NOT NULL;

-- 2. Borrado de una conexión y de sus lotes
CREATE INDEX IF NOT EXISTS ix_load_batch_connection
    ON raw.load_batch (connection_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_upload_job_connection
    ON raw.upload_job (connection_id);
CREATE INDEX IF NOT EXISTS ix_upload_job_batch
    ON raw.upload_job (batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_shipment_first_batch
    ON core.shipment (first_batch_id) WHERE first_batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_shipment_last_batch
    ON core.shipment (last_batch_id) WHERE last_batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_ad_spend_batch
    ON core.ad_spend (batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_cs_interaction_batch
    ON core.cs_interaction (batch_id) WHERE batch_id IS NOT NULL;

-- 3. Enlace movimiento -> guía por el número del transportador
CREATE INDEX IF NOT EXISTS ix_shipment_conn_carrier_tracking
    ON core.shipment (connection_id, carrier_tracking_number)
    WHERE carrier_tracking_number IS NOT NULL;

-- 4. Guía borrada -> interacciones de servicio
CREATE INDEX IF NOT EXISTS ix_cs_interaction_shipment
    ON core.cs_interaction (shipment_id) WHERE shipment_id IS NOT NULL;

-- 5. Duplicados. El índice de la UNIQUE y ix_shipment_tenant_country_date cubren
--    exactamente las mismas consultas.
DROP INDEX IF EXISTS core.ix_shipment_conn_tracking;
DROP INDEX IF EXISTS core.ix_shipment_orders_browse;
