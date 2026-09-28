-- =============================================================================
-- 060 - Dropi por API: dónde queda el cursor de cada conexión
-- =============================================================================
--
-- QUÉ GUARDA. Hasta qué fecha de creación ya se leyeron las órdenes de una
-- conexión que recibe datos por API (source_mode = 'api', migración 042), y qué
-- trajo la última pasada. El worker (job `sync_dropi`) lee de aquí la ventana de
-- la siguiente pasada: una ventana móvil hacia atrás (las órdenes contra entrega
-- cambian de estado durante semanas) que nunca empieza después del cursor, para
-- que un worker caído una semana no deje un hueco.
--
-- POR QUÉ UNA TABLA Y NO COLUMNAS EN core.connection. El cursor es estado del
-- conector, no configuración de la conexión: lo escribe el worker en cada pasada
-- y lo lee solo el worker. Mezclarlo con lo que edita una persona haría que un
-- PATCH de la pantalla de conexiones y una pasada del worker se pisaran.
--
-- LA CREDENCIAL NO VA AQUÍ. El token de integración de Dropi vive cifrado en
-- core.connection_credential (migración 051), con la misma bóveda que la
-- contraseña de Effi. Esta tabla no tiene nada secreto.
--
-- Depende de: 051 (core.enforce_credential_tenant). Idempotente.
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.connection_sync_state (
    connection_id    uuid PRIMARY KEY
                     REFERENCES core.connection(id) ON DELETE CASCADE,
    -- Denormalizado a propósito: las políticas RLS se apoyan en tenant_id.
    tenant_id        uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    -- El cursor: toda orden CREADA hasta esta fecha (inclusive) ya se leyó al
    -- menos una vez. NULL = nunca se sincronizó; la primera pasada hace backfill.
    synced_through   date,
    last_window_from date,
    last_window_to   date,
    last_orders_seen integer,
    last_pages       integer,
    -- Cuando la API dice cuántas órdenes hay y se leyeron menos, aquí queda el
    -- aviso en palabras. Nunca un cuerpo de respuesta crudo.
    last_warning     text,
    last_success_at  timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE core.connection_sync_state IS
    'Cursor de las conexiones por API (Dropi): hasta qué fecha de creación ya se
     leyeron las órdenes y qué trajo la última pasada. Lo escribe el worker.';

COMMENT ON COLUMN core.connection_sync_state.synced_through IS
    'Toda orden creada hasta esta fecha ya se leyó al menos una vez. La siguiente
     pasada nunca empieza después de este día.';

CREATE INDEX IF NOT EXISTS ix_connection_sync_state_tenant
    ON core.connection_sync_state (tenant_id);

-- El tenant del cursor es el de la conexión. Misma función que protege la
-- credencial (051): solo compara NEW.connection_id con NEW.tenant_id.
DROP TRIGGER IF EXISTS trg_sync_state_tenant ON core.connection_sync_state;
CREATE TRIGGER trg_sync_state_tenant
    BEFORE INSERT OR UPDATE ON core.connection_sync_state
    FOR EACH ROW EXECUTE FUNCTION core.enforce_credential_tenant();

ALTER TABLE core.connection_sync_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.connection_sync_state FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON core.connection_sync_state;
CREATE POLICY tenant_isolation ON core.connection_sync_state
    USING (tenant_id = core.current_tenant_id() OR core.is_service_context())
    WITH CHECK (tenant_id = core.current_tenant_id() OR core.is_service_context());

GRANT SELECT, INSERT, UPDATE, DELETE ON core.connection_sync_state TO norte_app;
