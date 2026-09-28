-- =============================================================================
-- Data Effi - 069 - mart.v_connection_permissions también filtra por tenant.
--
-- QUÉ PASABA. 007 establece la regla: cada vista de mart filtra por
-- `core.current_tenant_id()` y la RLS es la segunda pared. La vista de 051 es
-- la única vista de datos DE UN comerciante que no lo hace (las otras sin filtro
-- - v_platform_orgs, v_capture_inbox, v_platform_catalogue, v_fx_rates - son de
-- catálogo o de plataforma a propósito). Hoy la aísla solo el WHERE tenant_id
-- que escribe la API y, debajo, la RLS de core.connection.
--
-- Esa segunda pared depende del dueño de la vista: una vista lee sus tablas con
-- los privilegios de su DUEÑO, y si ese dueño tiene BYPASSRLS (el rol
-- `postgres` de Supabase puede tenerlo) la RLS de core.connection no se aplica
-- a través de la vista. Entonces un SELECT sin el WHERE de la API devolvería los
-- permisos y el resultado de las sondas de las conexiones de TODOS los tenants.
--
-- QUÉ CAMBIA. El mismo predicado que la política de 007, dentro de la vista:
-- tenant en contexto, o contexto de servicio. Mismas columnas y mismo orden, así
-- que CREATE OR REPLACE basta. Los dos lectores (api/preflight.py y
-- api/routers/config.py) ya corren con el tenant fijado.
--
-- Depende de: 051. Idempotente.
-- =============================================================================

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
 AND probe.permission_code = pp.code
WHERE c.tenant_id = core.current_tenant_id()
   OR core.is_service_context();

COMMENT ON VIEW mart.v_connection_permissions IS
    'Lo que la pantalla "Gestionar conexión" muestra: cada permiso que pedimos,
     para qué sirve, y si la plataforma ya nos lo concedió. Filtra por el tenant
     en contexto (069), como el resto de mart.';

GRANT SELECT ON mart.v_connection_permissions TO norte_app;
