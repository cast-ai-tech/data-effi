-- =============================================================================
-- Data Effi - 053 - Quien opera la plataforma, y qué NO puede ver.
--
-- EL AGUJERO QUE ESTO TAPA. La migración 052 guardó las capturas del contrato
-- de login con un `tenant_id` y RLS por empresa. Suena prudente y estaba mal
-- por dos razones distintas:
--
--   1. EL DATO NO ES DE NADIE. El contrato de login describe cómo funciona
--      Effi - la ruta del formulario, cómo se llama el campo de la clave - y es
--      idéntico para todos los comerciantes. No es información de un cliente:
--      es información de catálogo, como `core.platform`.
--
--   2. LE LLEGABA A QUIEN NO ERA. `GET /captures` filtraba por el tenant de
--      quien preguntaba, así que la captura quedaba encerrada en la empresa
--      del cliente que la pidió. Quien tiene que cablear el conector - el que
--      opera la plataforma - no la veía. Con veinte clientes habría veinte
--      copias aisladas y ningún sitio donde leerlas.
--
-- QUÉ CAMBIA.
--
-- 1. `core.app_user.is_platform_admin`. El primer rol que existe POR ENCIMA de
--    una organización. Se concede solo desde el servidor
--    (scripts/grant_platform_admin.py): no hay pantalla ni endpoint que lo
--    otorgue, porque un rol que se puede conceder desde la web es un rol que un
--    fallo del registro puede regalar.
--
-- 2. Las capturas dejan de ser de un tenant. `tenant_id` pasa a ser nullable y
--    se renombra su sentido: ya no aísla, solo recuerda desde qué invitación
--    llegó. La RLS por empresa se retira de las dos tablas.
--
-- POR QUÉ RETIRAR RLS AQUÍ NO ES UN DESCUIDO
-- ------------------------------------------
-- RLS por tenant protege datos DE un tenant. Estas tablas no tienen ninguno:
-- rutas, nombres de campos y un código de invitación hasheado. Es el mismo
-- trato que ya reciben `core.org`, `core.membership` y `core.plan` (ver el
-- encabezado de 007 y de 032), que viven fuera de RLS porque se leen antes de
-- saber de qué empresa se habla.
--
-- Lo que las protege ahora es el rol: `require_platform_admin` en la API. Y lo
-- que impide que un secreto entre sigue siendo el trigger de 052, intacto.
--
-- LO QUE ESTE ROL NO PUEDE HACER, POR DECISIÓN
-- --------------------------------------------
-- No ve guías, ni movimientos, ni compradores, ni plata de ningún comerciante.
-- Ninguna política de RLS de `core.shipment` y compañía lo menciona, y esta
-- migración no las toca. Si esa cuenta se filtra, no se lleva los datos de
-- ningún cliente - que es exactamente el punto de elegir el alcance mínimo.
--
-- Depends on: 052. Idempotente.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. El rol
-- -----------------------------------------------------------------------------
ALTER TABLE core.app_user
    ADD COLUMN IF NOT EXISTS is_platform_admin boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN core.app_user.is_platform_admin IS
    'Opera la plataforma: ve capturas de conexión, el catálogo y la lista de
     organizaciones con su plan. NO ve datos de ningún comerciante - ninguna
     política de RLS lo menciona. Se concede solo con
     scripts/grant_platform_admin.py, nunca desde la web.';

-- Son poquísimos y se buscan en cada arranque de la pantalla de plataforma.
CREATE INDEX IF NOT EXISTS ix_app_user_platform_admin
    ON core.app_user (id) WHERE is_platform_admin;

-- -----------------------------------------------------------------------------
-- 2. Las capturas dejan de pertenecer a una empresa
-- -----------------------------------------------------------------------------
ALTER TABLE raw.capture              ALTER COLUMN tenant_id DROP NOT NULL;
ALTER TABLE core.capture_token       ALTER COLUMN tenant_id DROP NOT NULL;

COMMENT ON COLUMN raw.capture.tenant_id IS
    'De qué espacio salió la invitación, si salió de alguno. NO aísla nada: una
     captura describe cómo funciona la plataforma de origen, no los datos de un
     comerciante. Ver el encabezado de esta migración.';

-- Fuera la RLS por empresa: no hay dato de empresa que aislar, y con el
-- tenant_id ahora nullable una política keyed en él escondería las filas de
-- todo el mundo, incluido quien tiene que leerlas.
DO $do$
DECLARE
    v_table text;
    v_tables text[] := ARRAY['core.capture_token', 'raw.capture'];
BEGIN
    FOREACH v_table IN ARRAY v_tables LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s', v_table);
        EXECUTE format('ALTER TABLE %s NO FORCE ROW LEVEL SECURITY', v_table);
        EXECUTE format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', v_table);
    END LOOP;
END;
$do$;

-- El trigger que rechaza secretos SIGUE EN PIE. Era la defensa importante y no
-- tiene nada que ver con el aislamiento por empresa.
-- (core.reject_capture_secrets, migración 052.)

-- -----------------------------------------------------------------------------
-- 3. La vista, sin el tenant como llave
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
    c.contract ->> 'base'       AS base_url,
    c.contract ->> 'ruta'       AS login_path
FROM raw.capture c
JOIN core.platform p ON p.code = c.platform_code
LEFT JOIN core.capture_token t ON t.id = c.token_id;

-- -----------------------------------------------------------------------------
-- 4. Lo que el operador SÍ puede ver de cada organización
--
-- Nombre, plan, cuántas empresas tiene y si sincroniza. Ni una cifra de
-- negocio: ni ventas, ni guías, ni plata. Es lo que hace falta para saber a
-- quién hay que cobrarle y a quién se le rompió una conexión, y nada más.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_platform_orgs AS
SELECT
    o.id                                        AS org_id,
    o.name                                      AS org_name,
    o.slug,
    o.created_at,
    coalesce(s.status, 'trial')                 AS subscription_status,
    s.plan_code,
    pl.name                                     AS plan_name,
    s.trial_ends_at,
    s.current_period_end,
    (SELECT count(*) FROM core.tenant t WHERE t.org_id = o.id)     AS tenant_count,
    (SELECT count(*) FROM core.app_user u WHERE u.org_id = o.id)   AS user_count,
    -- Salud operativa, no negocio: cuántas conexiones hay y cuántas fallan.
    (SELECT count(*) FROM core.connection c
       JOIN core.tenant t ON t.id = c.tenant_id
      WHERE t.org_id = o.id)                                       AS connection_count,
    (SELECT count(*) FROM core.connection c
       JOIN core.tenant t ON t.id = c.tenant_id
      WHERE t.org_id = o.id AND c.status = 'error')                AS connection_errors
FROM core.org o
LEFT JOIN core.org_subscription s ON s.org_id = o.id
LEFT JOIN core.plan pl ON pl.code = s.plan_code;

COMMENT ON VIEW mart.v_platform_orgs IS
    'Para quien opera la plataforma: quién existe, en qué plan está y si algo
     se le rompió. Deliberadamente SIN cifras de negocio - ni ventas, ni guías,
     ni dinero de nadie.';

GRANT SELECT ON mart.v_platform_orgs TO norte_app;
GRANT SELECT ON mart.v_capture_inbox TO norte_app;
