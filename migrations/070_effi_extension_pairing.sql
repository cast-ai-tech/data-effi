-- =============================================================================
-- Data Effi - 070 - Conectar Effi con la extensión: la sesión la hace el
-- comerciante, el servidor solo la usa.
--
-- EL MURO. El login de Effi (/ingreso) lleva un reCAPTCHA v2 invisible. El POST
-- de entrar viaja con una respuesta que solo emite Google, así que el servidor
-- no puede entrar con usuario y contraseña (connectors/effi/auth.py lo detecta
-- como CaptchaRequired y no manda la contraseña).
--
-- LA SALIDA QUE ELIGIÓ EL USUARIO. El comerciante entra a Effi en SU navegador,
-- resuelve el captcha él, y la extensión `extension/effi-connector` envía la
-- cookie de sesión (`ci_session`) a Data Effi con un código de emparejamiento
-- de un solo uso. Desde ahí el worker descarga los reportes con esa sesión.
--
-- LO QUE CAMBIA AQUÍ.
--
-- 1. core.connection_credential aprende un segundo modo, `browser_session`:
--    no hay contraseña (secret_enc NULL), solo la sesión cifrada. El modo
--    `password` sigue exactamente igual; un CHECK impide que una fila de
--    contraseña pierda su contraseña.
--
-- 2. `session_user_agent`: el User-Agent del navegador que creó la sesión. No
--    es secreto. Se guarda porque algunas instalaciones de CodeIgniter atan la
--    sesión al navegador; si Effi lo hace, sin él la sesión no sirve desde el
--    servidor (ver EFFI_REPLAY_BROWSER_USER_AGENT en session_fetcher.py).
--
-- 3. credential_status gana `session_expired`: la sesión enviada desde la
--    extensión murió. Es distinto de `expired` (reingresar contraseña) porque
--    lo que hay que hacer es otra cosa: volver a enviarla desde la extensión.
--    Es terminal para el worker igual que `invalid`: reintentar una sesión
--    muerta no la revive.
--
-- 4. core.connection_pairing: el código que el dueño genera en la app y pega
--    en la extensión. Diez minutos, un solo uso, solo el SHA-256 en la base.
--    Cada fila es además la bitácora: quién lo generó, cuándo se canjeó, desde
--    qué IP y con qué resultado. Nunca la cookie.
--
-- Nada de esto toca el esquema `public`. Depends on: 051. Idempotente.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1 y 2. La bóveda aprende el modo sesión-de-navegador
-- -----------------------------------------------------------------------------
ALTER TABLE core.connection_credential
    ADD COLUMN IF NOT EXISTS auth_mode text NOT NULL DEFAULT 'password';

ALTER TABLE core.connection_credential
    ADD COLUMN IF NOT EXISTS session_user_agent text;

ALTER TABLE core.connection_credential
    ALTER COLUMN secret_enc DROP NOT NULL;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'core.connection_credential'::regclass
          AND conname = 'connection_credential_auth_mode_check'
    ) THEN
        ALTER TABLE core.connection_credential
            ADD CONSTRAINT connection_credential_auth_mode_check
            CHECK (auth_mode IN ('password', 'browser_session'));
    END IF;

    -- Una credencial de contraseña SIN contraseña es la fila que se lee como
    -- "conectada" en todas las pantallas y falla en cada sincronización.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'core.connection_credential'::regclass
          AND conname = 'connection_credential_password_has_secret'
    ) THEN
        ALTER TABLE core.connection_credential
            ADD CONSTRAINT connection_credential_password_has_secret
            CHECK (auth_mode <> 'password' OR secret_enc IS NOT NULL);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'core.connection_credential'::regclass
          AND conname = 'connection_credential_user_agent_len'
    ) THEN
        ALTER TABLE core.connection_credential
            ADD CONSTRAINT connection_credential_user_agent_len
            CHECK (session_user_agent IS NULL OR length(session_user_agent) <= 512);
    END IF;
END;
$do$;

COMMENT ON COLUMN core.connection_credential.auth_mode IS
    'password = el servidor entra con usuario y contraseña cifrados |
     browser_session = el comerciante entró en su navegador y la extensión
     envió la sesión; no hay contraseña guardada.';

COMMENT ON COLUMN core.connection_credential.session_user_agent IS
    'User-Agent del navegador que creó la sesión enviada por la extensión. No
     es secreto; se guarda por si Effi ata la sesión al navegador.';

-- -----------------------------------------------------------------------------
-- 3. `session_expired` en la palabra que muestra el tablero
--
-- El CHECK de 051 se creó en línea con la columna, así que su nombre lo puso
-- Postgres. Se busca por la columna que vigila en vez de adivinarlo.
-- -----------------------------------------------------------------------------
DO $do$
DECLARE
    v_name text;
BEGIN
    FOR v_name IN
        SELECT con.conname
          FROM pg_constraint con
         WHERE con.conrelid = 'core.connection'::regclass
           AND con.contype = 'c'
           AND pg_get_constraintdef(con.oid) LIKE '%credential_status%'
    LOOP
        EXECUTE format('ALTER TABLE core.connection DROP CONSTRAINT %I', v_name);
    END LOOP;

    ALTER TABLE core.connection
        ADD CONSTRAINT connection_credential_status_check
        CHECK (credential_status IN ('none', 'ok', 'invalid', 'expired',
                                     'insufficient_permissions', 'locked',
                                     'session_expired'));
END;
$do$;

COMMENT ON COLUMN core.connection.credential_status IS
    'none = nadie ha conectado una cuenta | ok = entró bien la última vez |
     invalid = usuario o contraseña incorrectos | expired = hay que reingresar |
     insufficient_permissions = entró pero le falta un permiso |
     locked = la plataforma bloqueó la cuenta, NO reintentar solo |
     session_expired = venció la sesión enviada desde la extensión; hay que
     volver a enviarla, NO reintentar solo.';

-- -----------------------------------------------------------------------------
-- 4. El código de emparejamiento
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.connection_pairing (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    connection_id       uuid NOT NULL REFERENCES core.connection(id) ON DELETE CASCADE,
    -- SHA-256 hex del código normalizado. Nunca el código.
    code_hash           char(64) NOT NULL UNIQUE,
    created_by          uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    expires_at          timestamptz NOT NULL,
    -- Un código nuevo para la misma conexión deja muertos los anteriores.
    revoked_at          timestamptz,
    redeemed_at         timestamptz,
    redeemed_ip         inet,
    -- El User-Agent de la extensión que lo canjeó: bitácora, no secreto.
    redeemed_user_agent text CHECK (redeemed_user_agent IS NULL
                                    OR length(redeemed_user_agent) <= 512),
    -- Qué pasó al probar la sesión recibida. NULL mientras no se canjee.
    outcome             text CHECK (outcome IS NULL OR outcome IN
                                    ('connected', 'insufficient_permissions',
                                     'session_rejected', 'unverified')),
    outcome_detail      text CHECK (outcome_detail IS NULL
                                    OR length(outcome_detail) <= 1000),
    CONSTRAINT connection_pairing_expiry_after_creation
        CHECK (expires_at > created_at)
);

COMMENT ON TABLE core.connection_pairing IS
    'Códigos de un solo uso para que la extensión de Effi entregue la sesión del
     navegador. Solo se guarda el SHA-256. Cada fila es también la bitácora del
     canje: quién lo pidió, cuándo, desde dónde y cómo terminó. Nunca guarda la
     cookie: esa va cifrada a core.connection_credential.';

CREATE INDEX IF NOT EXISTS ix_connection_pairing_connection
    ON core.connection_pairing (connection_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_connection_pairing_tenant
    ON core.connection_pairing (tenant_id);

-- La empresa del código tiene que ser la de la conexión. El canje escribe la
-- sesión en la conexión que dice el código; si alguien lograra un código con
-- tenant cruzado, esto lo para en el motor.
CREATE OR REPLACE FUNCTION core.enforce_pairing_tenant()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_tenant uuid;
BEGIN
    SELECT tenant_id INTO v_tenant
    FROM core.connection WHERE id = NEW.connection_id;

    IF v_tenant IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION
            'El código de emparejamiento es de otra empresa que la conexión. Rechazado.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_pairing_tenant ON core.connection_pairing;
CREATE TRIGGER trg_pairing_tenant
    BEFORE INSERT OR UPDATE ON core.connection_pairing
    FOR EACH ROW EXECUTE FUNCTION core.enforce_pairing_tenant();

ALTER TABLE core.connection_pairing ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.connection_pairing FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON core.connection_pairing;
CREATE POLICY tenant_isolation ON core.connection_pairing
    USING (tenant_id = core.current_tenant_id() OR core.is_service_context())
    WITH CHECK (tenant_id = core.current_tenant_id() OR core.is_service_context());

GRANT SELECT, INSERT, UPDATE ON core.connection_pairing TO norte_app;
