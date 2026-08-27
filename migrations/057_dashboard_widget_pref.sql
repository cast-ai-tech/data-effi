-- =============================================================================
-- 057 - Cada persona acomoda su propio tablero
-- =============================================================================
--
-- QUÉ GUARDA. En qué orden quiere sus tarjetas y cuánto ancho ocupa cada una
-- (una columna o dos). El catálogo - mart.v_country_dashboard_layout - sigue
-- diciendo qué tarjetas EXISTEN para un país y en qué estado están; esta tabla
-- solo dice cómo las quiere ver ESTA persona. Se combinan en /kpis/layout con un
-- LEFT JOIN, así que sin nada guardado el tablero es exactamente el de siempre.
--
-- POR USUARIO, NO POR EMPRESA. Dos personas de la misma empresa miran cosas
-- distintas: quien despacha vive en Logística y quien cobra vive en Dinero. Que
-- una reordene el tablero de la otra sería una sorpresa, no una función.
--
-- POR PAÍS. La misma persona opera Ecuador y Guatemala con problemas distintos;
-- el orden que le sirve en uno no tiene por qué servirle en el otro.
--
-- NO HAY FK A widget_code A PROPÓSITO. El catálogo de widgets es código, no
-- datos: una preferencia sobre una tarjeta que hoy no se renderiza simplemente
-- no se une a nada, y vuelve a aplicarse sola el día que esa tarjeta exista. Con
-- una FK, renombrar un widget rompería el guardado de todo el mundo.
--
-- Depende de: 001 (tenant, app_user), 003 (v_country_dashboard_layout).
-- Idempotente.
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.dashboard_widget_pref (
    tenant_id    uuid        NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    user_id      uuid        NOT NULL REFERENCES core.app_user(id) ON DELETE CASCADE,
    country_code char(2)     NOT NULL,
    widget_code  text        NOT NULL,
    sort_order   integer     NOT NULL,
    -- Una columna o dos. Más anchos harían falta el día que el tablero tenga
    -- más de dos columnas; hasta entonces, aceptarlos sería inventar estados.
    width        smallint    NOT NULL DEFAULT 1,
    hidden       boolean     NOT NULL DEFAULT false,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, country_code, widget_code),
    CONSTRAINT dashboard_widget_pref_width_1_or_2 CHECK (width IN (1, 2))
);

ALTER TABLE core.dashboard_widget_pref ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON core.dashboard_widget_pref;
CREATE POLICY tenant_isolation ON core.dashboard_widget_pref
    USING (tenant_id = core.current_tenant_id() OR core.is_service_context())
    WITH CHECK (tenant_id = core.current_tenant_id() OR core.is_service_context());

CREATE INDEX IF NOT EXISTS ix_dashboard_widget_pref_lookup
    ON core.dashboard_widget_pref (user_id, country_code);

GRANT SELECT, INSERT, UPDATE, DELETE ON core.dashboard_widget_pref TO norte_app;

COMMENT ON TABLE core.dashboard_widget_pref IS
    'Cómo acomodó cada persona su tablero, por país: orden, ancho (1 o 2
     columnas) y si la ocultó. El catálogo decide qué tarjetas existen.';
