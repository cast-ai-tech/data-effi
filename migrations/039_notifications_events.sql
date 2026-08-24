-- =============================================================================
-- Data Effi - 039 - Notifications, events, and the carrier-by-zone view.
--
-- THREE TABLES, ONE IDEA: the platform should tell the operator what happened
-- instead of waiting to be asked.
--
--   raw.notification        what the detectors found, kept. Until now an alert
--                           was computed when the copilot panel opened and
--                           forgotten when it closed: no history, no "read",
--                           no way to know it was ever shown.
--   raw.notification_state  read / dismissed, PER USER. Two people share a
--                           company; the one who read the alert at 7 am must
--                           not silence it for the one who logs in at 9.
--   raw.event               a short-lived feed the browser long-polls so the
--                           screen updates itself when a load finishes or a
--                           job runs. Seven days of retention, because the
--                           only reader is a cursor that moves forward.
--
-- FINGERPRINT. `sha256(code | country | subject)`. The same product below its
-- break-even is detected after EVERY load; without the fingerprint the operator
-- would get the same alert four times a day. The window is enforced in Python
-- (ai/alerts.py) because "three days" is a judgement, not a constraint.
--
-- NONE OF THIS IS READABLE BY norte_readonly. The NL->SQL copilot runs as that
-- role, and notification text quotes product names and alert wording that the
-- model already trusts. Same reasoning as raw.ai_memory in migration 011.
--
-- THE VIEW. `mart.v_carrier_by_zone` answers "which carrier delivers best in
-- THIS city" - the decision the operator makes every time a zone turns red.
-- Ninety days, because a carrier's coverage changes faster than a product's
-- margin does.
--
-- TIMEZONE. `core.country.timezone` already exists since 001 and every country
-- row carries an IANA name. The ALTER below is a no-op guard for a database
-- restored from a schema that predates it; the UPDATE only fills blanks and
-- never overwrites a value an operator may have set.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Timezone guard
-- -----------------------------------------------------------------------------
ALTER TABLE core.country
    ADD COLUMN IF NOT EXISTS timezone text NOT NULL DEFAULT 'America/Bogota';

UPDATE core.country
   SET timezone = CASE code
        WHEN 'CO' THEN 'America/Bogota'
        WHEN 'MX' THEN 'America/Mexico_City'
        WHEN 'PE' THEN 'America/Lima'
        WHEN 'EC' THEN 'America/Guayaquil'
        WHEN 'CL' THEN 'America/Santiago'
        WHEN 'PA' THEN 'America/Panama'
        WHEN 'GT' THEN 'America/Guatemala'
        WHEN 'HN' THEN 'America/Tegucigalpa'
        WHEN 'CR' THEN 'America/Costa_Rica'
        WHEN 'DO' THEN 'America/Santo_Domingo'
        WHEN 'VE' THEN 'America/Caracas'
        ELSE 'America/Bogota'
       END
 WHERE coalesce(timezone, '') = '';

-- -----------------------------------------------------------------------------
-- raw.notification
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.notification (
    id              bigserial PRIMARY KEY,
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    country_code    char(2) REFERENCES core.country(code),
    kind            text NOT NULL CHECK (kind IN ('urgent', 'digest', 'system')),
    code            text NOT NULL,
    severity        text NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    title           text NOT NULL,
    finding         text NOT NULL,
    action          text NOT NULL,
    impact_amount   numeric(14, 2),
    impact_currency char(3),
    deep_link       text,
    fingerprint     char(64) NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_notification_tenant_created
    ON raw.notification (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_notification_fingerprint
    ON raw.notification (tenant_id, fingerprint, created_at DESC);

COMMENT ON TABLE raw.notification IS
    'What the detectors found, kept. One row per finding per dedup window; read state lives in raw.notification_state.';
COMMENT ON COLUMN raw.notification.fingerprint IS
    'sha256(code|country|subject). The same finding inside the window is not inserted twice.';

-- -----------------------------------------------------------------------------
-- raw.notification_state - per user, so one reader does not silence another
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.notification_state (
    notification_id bigint NOT NULL REFERENCES raw.notification(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES core.app_user(id) ON DELETE CASCADE,
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    read_at         timestamptz,
    dismissed_at    timestamptz,
    PRIMARY KEY (notification_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_notification_state_user
    ON raw.notification_state (tenant_id, user_id);

-- -----------------------------------------------------------------------------
-- raw.event - the feed the browser long-polls
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.event (
    id           bigserial PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    type         text NOT NULL,
    country_code char(2),
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_event_tenant_id ON raw.event (tenant_id, id);
CREATE INDEX IF NOT EXISTS ix_event_created ON raw.event (created_at);

COMMENT ON TABLE raw.event IS
    'Short-lived change feed: upload_job.updated, batch.finished, notification.created, job_run.finished, fx.refreshed. Seven days of retention.';

-- -----------------------------------------------------------------------------
-- Row-level security, identical to every other tenant table (migration 007).
-- -----------------------------------------------------------------------------
DO $do$
DECLARE
    v_table text;
    v_tables text[] := ARRAY[
        'raw.notification',
        'raw.notification_state',
        'raw.event'
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

-- -----------------------------------------------------------------------------
-- Grants. The API reads and writes all three; retention DELETEs run from the
-- worker under the same role. Sequences need USAGE for the bigserial defaults.
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON raw.notification, raw.notification_state, raw.event
    TO norte_app;
GRANT USAGE, SELECT ON SEQUENCE raw.notification_id_seq, raw.event_id_seq TO norte_app;

-- Deliberately NOT readable by norte_readonly: notification text is alert
-- wording and product names the copilot must not be able to quote back.
REVOKE ALL ON raw.notification, raw.notification_state, raw.event FROM norte_readonly;

-- -----------------------------------------------------------------------------
-- mart.v_carrier_by_zone - which carrier delivers best in each city
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW mart.v_carrier_by_zone AS
SELECT
    e.tenant_id,
    e.country_code,
    COALESCE(g.level1_name, 'Sin dato')                         AS level1_name,
    COALESCE(g.city_name, 'Sin dato')                           AS city_name,
    e.carrier_id,
    COALESCE(c.name, 'Sin transportadora')                      AS carrier_name,
    count(*)                                                    AS shipments,
    count(*) FILTER (WHERE e.is_terminal)                       AS terminal,
    round(count(*) FILTER (WHERE e.is_delivered)::numeric
        / NULLIF(count(*) FILTER (WHERE e.is_terminal), 0) * 100, 2) AS delivery_rate_pct,
    round(avg(e.days_to_deliver)::numeric, 1)                   AS avg_days_to_deliver,
    round(avg(e.freight_amount)::numeric, 2)                    AS avg_freight,
    sum(e.revenue_amount) FILTER (WHERE e.is_delivered)::numeric(14, 2)
                                                                AS delivered_value,
    min(e.currency_code)                                        AS currency_code
FROM stg.v_shipment_economics e
LEFT JOIN core.geo g ON g.id = e.geo_id
LEFT JOIN core.carrier c ON c.id = e.carrier_id
WHERE e.tenant_id = core.current_tenant_id()
  AND e.created_date >= CURRENT_DATE - 90
GROUP BY e.tenant_id, e.country_code, g.level1_name, g.city_name, e.carrier_id, c.name;

COMMENT ON VIEW mart.v_carrier_by_zone IS
    'Delivery rate per carrier per city, last 90 days. The operational answer to "who should carry to this zone".';

GRANT SELECT ON mart.v_carrier_by_zone TO norte_app;
GRANT SELECT ON mart.v_carrier_by_zone TO norte_readonly;

-- The widget that shows the view, placed right after the carrier table on the
-- logistics tab (carrier_table = 10, fulfillment_sla = 15).
INSERT INTO core.widget_catalog
    (widget_code, tab, title, description, required_domains, optional_domains,
     blocked_message, sort_order) VALUES
    ('carrier_by_zone', 'logistica', 'Transportadora por zona',
     'Quién entrega mejor en cada ciudad, últimos 90 días.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Necesitas al menos una conexión de guías.', 12)
ON CONFLICT (widget_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- The widget that renders mart.v_carrier_by_zone on the logistics tab. Placed
-- right after the carrier table it refines: the table says how each carrier
-- does overall, this one says which carrier to use where.
-- -----------------------------------------------------------------------------
INSERT INTO core.widget_catalog
    (widget_code, tab, title, description, required_domains, optional_domains,
     blocked_message, sort_order) VALUES
    ('carrier_by_zone', 'logistica', 'Transportadora por zona',
     'Qué transportadora entrega mejor en cada provincia y ciudad, con su flete.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Necesitas al menos una conexión de guías.', 22)
ON CONFLICT (widget_code) DO UPDATE SET
    tab = EXCLUDED.tab, title = EXCLUDED.title, description = EXCLUDED.description,
    required_domains = EXCLUDED.required_domains, sort_order = EXCLUDED.sort_order;
