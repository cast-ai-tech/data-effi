-- =============================================================================
-- Data Effi - 051 - The credential vault: how a merchant connects Effi alone.
--
-- THE PROBLEM. Effi publishes no API. The only way in is the merchant's own
-- login, and until now this system stored that login as the NAME of a server
-- environment variable (`connection.secret_ref`). That is the safest possible
-- place to put a secret and the reason the product cannot grow: every new
-- company needs someone with SSH to edit the server and redeploy. A merchant
-- cannot connect their own Effi account at three in the morning. The whole
-- self-service story dies at that one line.
--
-- THE FIX, AND ITS PRICE. Credentials move into the database, encrypted with a
-- key that is NOT in the database (pipeline/vault.py, Fernet, same reasoning as
-- pipeline/crypto.py for customer PII). Say the trade plainly:
--
--   env var  -> a stolen database dump yields nothing at all
--   vault    -> a stolen dump yields ciphertext, useless without the key,
--               which lives in the server environment and never in a row
--
-- The vault is strictly worse than the env var and strictly better than every
-- other way of letting a merchant type their own password. `secret_ref` stays:
-- a connection may still name an env var, and when it does, that wins. The
-- vault is the fallback, not the replacement.
--
-- WHAT LANDS HERE.
--
-- 1. core.platform_permission - the contract, written down. Effi's roles are
--    granular, so a merchant can (and should) create a DEDICATED user with the
--    least privilege that still works, instead of handing over the owner
--    account. This table is what the "Gestionar conexión" screen renders, and
--    what the preflight probes one by one.
--
--    NOTE WHAT IS NOT IN IT: no Crear, no Modificar, no Anular. Other tools in
--    this market ask for write permissions because they also place orders and
--    answer chats. Data Effi only ever reads a report. Asking for a permission
--    we will never exercise would be asking a merchant to trust us for nothing.
--
-- 2. core.connection_credential - one row per connection. Username in the
--    clear (the merchant must be able to see WHICH user is connected), password
--    and session encrypted, both as bytea. Never a plaintext password column,
--    not even briefly.
--
-- 3. core.connection_permission_probe - what the preflight found. A failed
--    fetch that says "falta un permiso en Effi" is a merchant fixing it in two
--    minutes; a failed fetch that says "HTTP 403" is a support ticket.
--
-- 4. `credential_status` on core.connection - the one word the dashboard shows:
--    none | ok | invalid | expired | insufficient_permissions | locked.
--
-- CONSENT STILL RULES. Nothing here weakens migration 042: a `session`
-- connection without `consent_granted_at` is still refused by the engine. A
-- credential cannot exist without a connection, and that connection had to pass
-- the consent trigger to be born.
--
-- Depends on: 050. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. The permission contract
--
-- `actions` is what we ask FOR: only 'consultar' and 'ver_reportes' appear,
-- because this product reads and never writes. A CHECK enforces that promise at
-- the engine, so a future migration cannot quietly start asking for 'crear'
-- without someone deliberately dropping this constraint.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.platform_permission (
    platform_code text NOT NULL REFERENCES core.platform(code) ON DELETE CASCADE,
    code          text NOT NULL,
    name          text NOT NULL,
    actions       text[] NOT NULL,
    -- What breaks without it, in the merchant's words. Rendered under the name.
    why           text NOT NULL,
    -- required: the connection cannot work at all without it.
    -- optional:  a feature degrades, the rest keeps working.
    requirement   text NOT NULL DEFAULT 'required'
                  CHECK (requirement IN ('required', 'optional')),
    -- Effi grants some permissions only to an administrator user. Saying so up
    -- front stops the merchant from creating a limited user, failing, and
    -- blaming us.
    admin_only    boolean NOT NULL DEFAULT false,
    sort_order    smallint NOT NULL DEFAULT 100,
    PRIMARY KEY (platform_code, code),
    CONSTRAINT platform_permission_read_only
        CHECK (actions <@ ARRAY['consultar', 'ver_reportes']::text[])
);

COMMENT ON TABLE core.platform_permission IS
    'Los permisos que Data Effi necesita en la plataforma de origen, y para qué
     sirve cada uno. Solo lectura por diseño: el CHECK impide que alguien pida
     crear, modificar o anular sin quitar la restricción a propósito.';

COMMENT ON COLUMN core.platform_permission.why IS
    'La consecuencia de NO darlo, en palabras del comerciante. Es lo que se
     muestra debajo del nombre en la pantalla de conexión.';

-- -----------------------------------------------------------------------------
-- What Effi must let us read.
--
-- Ordered the way the screen reads: first what the product cannot live without,
-- then what merely turns a feature off.
-- -----------------------------------------------------------------------------
INSERT INTO core.platform_permission
    (platform_code, code, name, actions, why, requirement, admin_only, sort_order)
VALUES
    ('effi', 'guias_transporte', 'Guías de transporte',
     ARRAY['consultar', 'ver_reportes'],
     'Es el corazón de todo. Sin este permiso no hay guías, y sin guías no hay '
     'tablero: ni entregas, ni devoluciones, ni plata.',
     'required', false, 10),

    ('effi', 'novedades_guias', 'Novedades de guías de transporte',
     ARRAY['consultar', 'ver_reportes'],
     'Es el POR QUÉ de cada entrega fallida. Sin esto sabes cuánto pierdes pero '
     'no por qué, que es lo único que se puede corregir.',
     'required', false, 20),

    ('effi', 'trazabilidad_dinero', 'Trazabilidad de dinero Effi',
     ARRAY['consultar', 'ver_reportes'],
     'Es la plata que de verdad llegó a tu cuenta. Sin esto el tablero muestra '
     'lo que deberías haber cobrado, no lo que cobraste.',
     'required', true, 30),

    ('effi', 'gestion_novedades', 'Gestión de novedades de guías de transporte',
     ARRAY['consultar', 'ver_reportes'],
     'Muestra qué se hizo con cada novedad y cuánto se demoró. Sin esto se '
     'pierde el informe de gestión, pero las novedades se siguen viendo.',
     'optional', false, 40),

    ('effi', 'articulos', 'Artículos',
     ARRAY['consultar'],
     'Da el nombre y el costo de cada producto. Sin esto la rentabilidad por '
     'producto queda en blanco.',
     'optional', false, 50),

    ('effi', 'clientes', 'Clientes',
     ARRAY['consultar'],
     'Permite agrupar guías por comprador: recompra, clientes que siempre '
     'rechazan, historial. Sin esto cada guía queda suelta.',
     'optional', false, 60),

    ('effi', 'notas_remision', 'Notas de remisión de venta',
     ARRAY['consultar'],
     'Cruza la guía con la venta que la originó. Sin esto no se puede conciliar '
     'una guía contra su factura.',
     'optional', false, 70),

    ('effi', 'seguimientos_comerciales', 'Seguimientos comerciales',
     ARRAY['consultar'],
     'Añade el contacto comercial previo a la venta. Es contexto extra; nada '
     'del tablero principal depende de él.',
     'optional', false, 80),

    ('effi', 'mensajes_chat', 'Mensajes de Chat',
     ARRAY['consultar'],
     'Deja ver la conversación con el comprador junto a su guía. Sin esto el '
     'historial del cliente queda incompleto.',
     'optional', false, 90),

    ('effi', 'mensajes_sms', 'Mensajes de texto SMS',
     ARRAY['consultar'],
     'Igual que el chat, para los SMS que Effi envía por ti.',
     'optional', false, 100),

    ('effi', 'consola_atencion', 'Consola de atención',
     ARRAY['consultar'],
     'Añade lo que registró tu equipo de servicio al cliente. Solo alimenta el '
     'historial; ninguna métrica lo necesita.',
     'optional', false, 110)
ON CONFLICT (platform_code, code) DO UPDATE SET
    name        = EXCLUDED.name,
    actions     = EXCLUDED.actions,
    why         = EXCLUDED.why,
    requirement = EXCLUDED.requirement,
    admin_only  = EXCLUDED.admin_only,
    sort_order  = EXCLUDED.sort_order;

-- -----------------------------------------------------------------------------
-- 2. The vault
--
-- One row per connection, cascading with it: deleting a connection destroys its
-- credential in the same statement, with no application code involved.
--
-- `username` is deliberately in the clear. The merchant has to be able to read
-- "conectado como reportes@mitienda.co" and recognise it, and a username is not
-- a secret - the password is. Encrypting it would buy nothing and cost the one
-- thing the screen needs to say.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.connection_credential (
    connection_id      uuid PRIMARY KEY
                       REFERENCES core.connection(id) ON DELETE CASCADE,
    -- Denormalised on purpose: RLS policies key on tenant_id, and a policy that
    -- has to join to find it is a policy that can be forgotten.
    tenant_id          uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    username           text NOT NULL,
    -- Fernet ciphertext. Never null, never plaintext, never logged.
    secret_enc         bytea NOT NULL,
    -- The live session, when the connector has one. Encrypted the same way and
    -- treated as disposable: losing it costs one login, not a support call.
    session_enc        bytea,
    session_expires_at timestamptz,
    last_login_at      timestamptz,
    -- The reason the last login failed, in the merchant's words. NEVER the
    -- password, never a raw response body, never a header.
    last_login_error   text,
    rotated_at         timestamptz NOT NULL DEFAULT now(),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE core.connection_credential IS
    'Bóveda: usuario en claro, contraseña y sesión cifradas con Fernet. La llave
     vive en la variable de entorno CONNECTION_VAULT_KEY, nunca en una fila. Un
     respaldo robado entrega ciphertext. Ver pipeline/vault.py.';

COMMENT ON COLUMN core.connection_credential.secret_enc IS
    'Contraseña cifrada. Si alguna vez ves texto legible aquí, es un incidente.';

COMMENT ON COLUMN core.connection_credential.last_login_error IS
    'Mensaje para el comerciante. Prohibido guardar aquí la respuesta cruda de
     la plataforma: arrastra cookies y tokens a la base de datos.';

CREATE INDEX IF NOT EXISTS ix_connection_credential_tenant
    ON core.connection_credential (tenant_id);

-- A credential that outlives its session gets refreshed by the worker; this is
-- the index that lets it find them without scanning the table.
CREATE INDEX IF NOT EXISTS ix_connection_credential_expiring
    ON core.connection_credential (session_expires_at)
    WHERE session_expires_at IS NOT NULL;

-- The tenant on the credential must be the tenant on the connection. Getting
-- this wrong would hand one merchant's Effi password to another, so it is
-- checked by the engine rather than trusted to the API.
CREATE OR REPLACE FUNCTION core.enforce_credential_tenant()
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
            'La credencial pertenece a otra empresa que la conexión. Rechazada.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_credential_tenant ON core.connection_credential;
CREATE TRIGGER trg_credential_tenant
    BEFORE INSERT OR UPDATE ON core.connection_credential
    FOR EACH ROW EXECUTE FUNCTION core.enforce_credential_tenant();

-- -----------------------------------------------------------------------------
-- 3. What the preflight found
--
-- One row per (connection, permission). `unknown` is the honest default: we
-- have not tried yet, which is a different thing from "it failed".
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.connection_permission_probe (
    connection_id   uuid NOT NULL REFERENCES core.connection(id) ON DELETE CASCADE,
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    permission_code text NOT NULL,
    status          text NOT NULL DEFAULT 'unknown'
                    CHECK (status IN ('unknown', 'granted', 'denied', 'unreachable')),
    detail          text,
    checked_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (connection_id, permission_code)
);

COMMENT ON TABLE core.connection_permission_probe IS
    'Resultado de probar cada permiso contra la plataforma real. Convierte un
     "HTTP 403" en "te falta el permiso Novedades de guías en Effi", que el
     comerciante arregla solo en dos minutos.';

CREATE INDEX IF NOT EXISTS ix_permission_probe_tenant
    ON core.connection_permission_probe (tenant_id);

-- -----------------------------------------------------------------------------
-- 4. The one word the dashboard shows
-- -----------------------------------------------------------------------------
ALTER TABLE core.connection
    ADD COLUMN IF NOT EXISTS credential_status text NOT NULL DEFAULT 'none'
        CHECK (credential_status IN ('none', 'ok', 'invalid', 'expired',
                                     'insufficient_permissions', 'locked'));

COMMENT ON COLUMN core.connection.credential_status IS
    'none = nadie ha conectado una cuenta | ok = entró bien la última vez |
     invalid = usuario o contraseña incorrectos | expired = hay que reingresar |
     insufficient_permissions = entró pero le falta un permiso |
     locked = la plataforma bloqueó la cuenta, NO reintentar solo.';

-- `locked` exists so the worker has somewhere to stop. A platform that locks an
-- account after N failed logins turns a blind retry loop into a merchant who
-- cannot log in to their own Effi. See connectors/effi/auth.py.

-- -----------------------------------------------------------------------------
-- 5. Row-level security, same shape as every other tenant table (migration 007)
-- -----------------------------------------------------------------------------
DO $do$
DECLARE
    v_table text;
    v_tables text[] := ARRAY[
        'core.connection_credential',
        'core.connection_permission_probe'
    ];
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

-- The permission catalogue is not tenant data - it is the same contract for
-- everyone - so it stays readable without a policy, like core.platform.
GRANT SELECT ON core.platform_permission TO norte_app;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON core.connection_credential, core.connection_permission_probe TO norte_app;

-- -----------------------------------------------------------------------------
-- 6. What the screen reads
--
-- One row per (connection, permission): the contract joined to what the probe
-- found. LEFT JOIN, so a permission never probed shows as 'unknown' instead of
-- vanishing from the list the merchant is supposed to tick off.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_connection_permissions AS
SELECT
    c.id                        AS connection_id,
    c.tenant_id,
    c.platform_code,
    pp.code                     AS permission_code,
    pp.name                     AS permission_name,
    pp.actions,
    pp.why,
    pp.requirement,
    pp.admin_only,
    pp.sort_order,
    coalesce(probe.status, 'unknown') AS status,
    probe.detail,
    probe.checked_at
FROM core.connection c
JOIN core.platform_permission pp
  ON pp.platform_code = c.platform_code
LEFT JOIN core.connection_permission_probe probe
  ON probe.connection_id = c.id
 AND probe.permission_code = pp.code;

COMMENT ON VIEW mart.v_connection_permissions IS
    'Lo que la pantalla "Gestionar conexión" muestra: cada permiso que pedimos,
     para qué sirve, y si la plataforma ya nos lo concedió.';

GRANT SELECT ON mart.v_connection_permissions TO norte_app;
