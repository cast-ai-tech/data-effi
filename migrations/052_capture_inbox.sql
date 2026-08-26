-- =============================================================================
-- Data Effi - 052 - El buzón de capturas: que el contrato llegue solo.
--
-- EL PROBLEMA. La herramienta de tools/effi-capture ya saca el contrato de
-- login sin leer contraseñas, pero termina con "copia este texto y pégalo en el
-- chat". Ese último paso manual cuesta más de lo que parece:
--
--   · Se pierde. El texto va a un WhatsApp entre otros veinte mensajes.
--   · Se corta. La gente pega la mitad, o el chat lo recorta.
--   · No se sabe si llegó. Quien captura no sabe si sirvió; quien espera no
--     sabe si ya pasó. Los dos preguntan.
--   · No se puede repetir barato. La gracia de la extensión es capturar varias
--     veces; con envío manual, cada intento es otro mensaje que alguien lee.
--
-- LA SOLUCIÓN. Un buzón: la extensión hace POST del contrato a la API con un
-- código de invitación, y llega solo, fechado, sin que nadie copie nada.
--
-- POR QUÉ ESTO PUEDE SER UN ENDPOINT PÚBLICO
-- ------------------------------------------
-- Porque lo que viaja no es secreto. Son rutas y NOMBRES de campos - la misma
-- información que cualquiera ve abriendo Effi con F12. No hay contraseña, no
-- hay cookie, no hay dato de ningún comprador. Si esta tabla se filtrara entera,
-- lo que se sabría es cómo se llama el campo de usuario de Effi.
--
-- Y AUN ASÍ, EL SERVIDOR NO SE FÍA DEL CLIENTE. La extensión promete no mandar
-- valores; `core.reject_capture_secrets` lo comprueba en el motor y rechaza la
-- fila si el JSON trae algo que huela a credencial. Una extensión modificada, o
-- una versión futura con un descuido, se estrella contra el trigger en vez de
-- escribir una contraseña en una tabla que nadie considera sensible.
--
-- DOS TABLAS.
--
-- 1. core.capture_token - la invitación. Se genera desde la app, se mete en el
--    .zip, y caduca. Guarda solo el SHA-256, como el token de webhook (012) y
--    como un refresh token: un volcado de la base no entrega nada reutilizable.
--
-- 2. raw.capture - lo que llegó. Vive en `raw` porque es exactamente eso: algo
--    de fuera, sin procesar, que un humano va a leer y decidir.
--
-- Depends on: 051. Idempotente.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. La invitación
--
-- `max_uses` existe porque capturar bien suele costar dos o tres intentos - la
-- primera vez casi siempre falta un reporte por exportar. Un token de un solo
-- uso obligaría a pedir otro código a mitad de camino, que es justo la fricción
-- que este buzón viene a quitar.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.capture_token (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    -- Nunca el token: solo su hash. Igual que core.connection.webhook_token_hash.
    token_hash   char(64) NOT NULL UNIQUE,
    -- Para quién es, en palabras de quien lo generó ("Juan, de Distrilatam").
    -- Sirve para reconocer la captura cuando lleguen tres el mismo día.
    label        text NOT NULL,
    platform_code text NOT NULL DEFAULT 'effi' REFERENCES core.platform(code),
    max_uses     smallint NOT NULL DEFAULT 10 CHECK (max_uses BETWEEN 1 AND 50),
    uses         smallint NOT NULL DEFAULT 0,
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz,
    created_by   uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE core.capture_token IS
    'Invitación para que alguien de fuera mande una captura del login de una
     plataforma. Caduca, se puede revocar, y solo guarda el hash del código.';

COMMENT ON COLUMN core.capture_token.max_uses IS
    'Capturar bien cuesta dos o tres intentos. Un solo uso obligaría a pedir
     otro código a mitad de camino.';

CREATE INDEX IF NOT EXISTS ix_capture_token_tenant
    ON core.capture_token (tenant_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- 2. Lo que llegó
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.capture (
    id            bigserial PRIMARY KEY,
    tenant_id     uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    token_id      uuid REFERENCES core.capture_token(id) ON DELETE SET NULL,
    platform_code text NOT NULL REFERENCES core.platform(code),
    -- El contrato tal cual llegó. Ver el trigger de abajo: no puede traer
    -- valores, solo nombres y rutas.
    contract      jsonb NOT NULL,
    -- De dónde vino. Solo para distinguir dos capturas del mismo día, nunca
    -- para identificar a nadie.
    source        text NOT NULL DEFAULT 'extension'
                  CHECK (source IN ('extension', 'analizador', 'manual')),
    -- Lo que la extensión dedujo: si encontró el login, cuántas descargas vio.
    -- Copiado fuera del jsonb para poder ordenar por utilidad sin abrirlo.
    found_login   boolean NOT NULL DEFAULT false,
    export_count  smallint NOT NULL DEFAULT 0,
    reviewed_at   timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE raw.capture IS
    'Contratos de login que mandó alguien desde la extensión. Solo rutas y
     nombres de campos: el trigger core.reject_capture_secrets rechaza cualquier
     cosa que parezca una credencial.';

CREATE INDEX IF NOT EXISTS ix_capture_tenant_created
    ON raw.capture (tenant_id, created_at DESC);

-- Las útiles primero: una captura sin login no sirve para nada.
CREATE INDEX IF NOT EXISTS ix_capture_pending
    ON raw.capture (tenant_id, created_at DESC)
    WHERE reviewed_at IS NULL AND found_login;

-- -----------------------------------------------------------------------------
-- 3. El servidor no se fía del cliente
--
-- La extensión no manda valores. Este trigger comprueba que sea verdad, porque
-- "el cliente promete" no es una garantía cuando el cliente es un .zip que
-- cualquiera puede editar antes de instalar.
--
-- Se mira por CLAVE, no por valor: un contrato legítimo trae `campoClave` con
-- el texto "clave" dentro, y buscar la palabra en los valores lo rechazaría.
-- Lo que no puede existir es una clave llamada `password`, `secret`, `cookie` o
-- similar, porque el formato del contrato no tiene ninguna.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.reject_capture_secrets()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_key text;
BEGIN
    FOR v_key IN SELECT jsonb_object_keys(NEW.contract) LOOP
        IF lower(v_key) ~ '(password|contrasena|secret|cookie|authorization|token_value|session_value)' THEN
            RAISE EXCEPTION
                'La captura trae un campo prohibido (%). Solo se aceptan rutas y '
                'nombres de campos, nunca valores.', v_key
                USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;

    -- Un contrato es pequeño: rutas y una lista de nombres. Cualquier cosa que
    -- pase de 16 KB no es un contrato, es alguien usando esto de buzón.
    IF length(NEW.contract::text) > 16384 THEN
        RAISE EXCEPTION 'La captura es demasiado grande para ser un contrato'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_capture_no_secrets ON raw.capture;
CREATE TRIGGER trg_capture_no_secrets
    BEFORE INSERT OR UPDATE ON raw.capture
    FOR EACH ROW EXECUTE FUNCTION core.reject_capture_secrets();

-- -----------------------------------------------------------------------------
-- 4. RLS, igual que el resto (migración 007)
-- -----------------------------------------------------------------------------
DO $do$
DECLARE
    v_table text;
    v_tables text[] := ARRAY['core.capture_token', 'raw.capture'];
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

GRANT SELECT, INSERT, UPDATE, DELETE ON core.capture_token TO norte_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON raw.capture TO norte_app;
GRANT USAGE, SELECT ON SEQUENCE raw.capture_id_seq TO norte_app;

-- -----------------------------------------------------------------------------
-- 5. Lo que la pantalla lee
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_capture_inbox AS
SELECT
    c.id,
    c.tenant_id,
    c.platform_code,
    p.name                      AS platform_name,
    t.label                     AS invited_label,
    c.source,
    c.found_login,
    c.export_count,
    c.contract,
    c.created_at,
    c.reviewed_at,
    (c.reviewed_at IS NULL)     AS is_new,
    -- Lo que hace útil una captura, resumido para la lista.
    c.contract ->> 'base'       AS base_url,
    c.contract ->> 'ruta'       AS login_path
FROM raw.capture c
JOIN core.platform p ON p.code = c.platform_code
LEFT JOIN core.capture_token t ON t.id = c.token_id;

COMMENT ON VIEW mart.v_capture_inbox IS
    'Las capturas recibidas, listas para revisar. Sin secretos: la tabla de
     origen no puede contenerlos.';

GRANT SELECT ON mart.v_capture_inbox TO norte_app;
