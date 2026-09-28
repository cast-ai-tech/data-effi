-- =============================================================================
-- Data Effi - 068 - Los roles de la API de Supabase no tocan los esquemas propios.
--
-- POR QUÉ. El proyecto de Supabase es COMPARTIDO con otra aplicación que vive en
-- `public` y habla con la base a través de PostgREST / pg_graphql con los roles
-- `anon`, `authenticated` y `service_role`. Ninguna migración de este repo les
-- concede nada sobre core/raw/stg/mart, y PostgreSQL no da USAGE a PUBLIC sobre
-- un esquema nuevo, así que hoy no deberían poder leer nada. Pero esa garantía
-- depende de que nadie haya hecho un GRANT a mano ni añadido estos esquemas a
-- "Exposed schemas" en el panel. Y `service_role` tiene BYPASSRLS en Supabase:
-- con un solo GRANT, la clave de servicio de la OTRA aplicación leería guías,
-- movimientos y datos de compradores de todos los comerciantes, sin RLS.
--
-- Esta migración deja por escrito el estado correcto y lo re-afirma: esos tres
-- roles no tienen USAGE sobre los esquemas de Data Effi ni privilegio alguno
-- sobre sus objetos. Solo REVOKE: no toca `public`, no toca a la otra
-- aplicación, y en una base sin esos roles (desarrollo local, CI) no hace nada.
--
-- También fuerza la RLS de core.dashboard_widget_pref (057 la habilitó sin
-- FORCE, a diferencia de todas las demás tablas por tenant; ver 007).
--
-- EXECUTE sobre funciones lo siguen heredando de PUBLIC (default de PostgreSQL
-- que 018/032 cuentan con él); sin USAGE sobre el esquema no pueden invocarlas.
--
-- LÍMITE. Un REVOKE solo retira lo que concedió el rol que lo ejecuta. Si un
-- GRANT lo hizo otro rol (p. ej. supabase_admin desde el panel), PostgreSQL
-- avisa con un WARNING y lo deja. La consulta de verificación está en el reporte
-- de auditoría; debe devolver cero filas.
--
-- Depende de: 007, 057. Idempotente.
-- =============================================================================

DO $do$
DECLARE
    v_role text;
BEGIN
    FOREACH v_role IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA core, raw, stg, mart FROM %I', v_role);
            EXECUTE format(
                'REVOKE ALL ON ALL TABLES IN SCHEMA core, raw, stg, mart FROM %I', v_role);
            EXECUTE format(
                'REVOKE ALL ON ALL SEQUENCES IN SCHEMA core, raw, stg, mart FROM %I', v_role);
            EXECUTE format(
                'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA core, raw, stg, mart FROM %I', v_role);
        END IF;
    END LOOP;
END;
$do$;

ALTER TABLE core.dashboard_widget_pref FORCE ROW LEVEL SECURITY;
