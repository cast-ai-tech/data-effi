-- =============================================================================
-- Data Effi - 012 - The full integration catalogue, and global connections.
--
-- TWO CHANGES THE OPERATOR ASKED FOR.
--
-- 1. MANUAL UPLOAD BECOMES GLOBAL. Every other integration is per country -
--    a Meta account, an Effi account and a Shopify store each belong to one
--    country. Manual upload does not: the file itself says which country it is
--    about (migration 010), so making the operator pick one first was asking
--    for information the system already had. `core.connection.country_code`
--    therefore becomes nullable, and a NULL means "wherever the file says".
--
-- 2. THE CATALOGUE GROWS. Ads, storefronts, CRMs, fulfilment, automation and
--    file sources. Each row declares what it needs to work, and - honestly -
--    whether it works TODAY. A platform marked 'planned' appears in the UI
--    greyed out with what it will need, which is more useful than pretending
--    the list is shorter than it is.
--
-- Depends on: 001-011. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Platform metadata: what it is, how it authenticates, whether it works yet.
-- -----------------------------------------------------------------------------
ALTER TABLE core.platform
    -- 'country': one connection per country (an ad account, a store, an ERP).
    -- 'global':  one connection for the whole workspace (manual upload, where
    --            the file declares its own country).
    ADD COLUMN IF NOT EXISTS scope text NOT NULL DEFAULT 'country'
        CHECK (scope IN ('country', 'global')),
    ADD COLUMN IF NOT EXISTS category text NOT NULL DEFAULT 'otros'
        CHECK (category IN ('pauta', 'tienda', 'crm', 'fulfillment',
                            'automatizacion', 'archivos', 'analitica', 'otros')),
    ADD COLUMN IF NOT EXISTS auth_type text NOT NULL DEFAULT 'file'
        CHECK (auth_type IN ('none', 'file', 'api_key', 'oauth2', 'session', 'webhook')),
    -- 'available' works today. 'beta' works with caveats. 'planned' is listed
    -- so the operator can see it is coming, and is refused if anyone tries.
    ADD COLUMN IF NOT EXISTS availability text NOT NULL DEFAULT 'available'
        CHECK (availability IN ('available', 'beta', 'planned')),
    ADD COLUMN IF NOT EXISTS direction text NOT NULL DEFAULT 'in'
        CHECK (direction IN ('in', 'out', 'both')),
    ADD COLUMN IF NOT EXISTS setup_hint text,
    ADD COLUMN IF NOT EXISTS sort_order smallint NOT NULL DEFAULT 100;

COMMENT ON COLUMN core.platform.availability IS
    'available = usable now. planned = listed but refused on creation, so the
     catalogue never promises something that does not work.';

-- -----------------------------------------------------------------------------
-- A connection may now be global (country_code NULL).
-- -----------------------------------------------------------------------------
ALTER TABLE core.connection ALTER COLUMN country_code DROP NOT NULL;

-- The uniqueness rule has to survive NULLs, which a plain UNIQUE does not.
ALTER TABLE core.connection
    DROP CONSTRAINT IF EXISTS connection_tenant_id_country_code_platform_code_name_key;

CREATE UNIQUE INDEX IF NOT EXISTS ux_connection_scoped
    ON core.connection (tenant_id, country_code, platform_code, name)
    WHERE country_code IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_connection_global
    ON core.connection (tenant_id, platform_code, name)
    WHERE country_code IS NULL;

-- A connection must match its platform's scope: a global platform cannot be
-- pinned to a country, and a country platform cannot float free.
CREATE OR REPLACE FUNCTION core.enforce_connection_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_scope        text;
    v_availability text;
    v_name         text;
BEGIN
    SELECT scope, availability, name
      INTO v_scope, v_availability, v_name
      FROM core.platform WHERE code = NEW.platform_code;

    IF v_availability = 'planned' THEN
        RAISE EXCEPTION
            '% todavía no está disponible. Aparece en el catálogo para que sepas '
            'que viene, pero aún no se puede conectar.', v_name
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_scope = 'global' AND NEW.country_code IS NOT NULL THEN
        RAISE EXCEPTION
            '% es una conexión global: no se ata a un país, el archivo dice de '
            'qué país es cada carga.', v_name
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_scope = 'country' AND NEW.country_code IS NULL THEN
        RAISE EXCEPTION
            '% necesita un país: cada cuenta pertenece a una sola operación.', v_name
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS tr_connection_scope ON core.connection;
CREATE TRIGGER tr_connection_scope
    BEFORE INSERT OR UPDATE ON core.connection
    FOR EACH ROW EXECUTE FUNCTION core.enforce_connection_scope();

-- -----------------------------------------------------------------------------
-- Webhook connections: n8n, Make, Zapier and anything else that can POST.
--
-- The secret is a SHA-256 like every other credential in this system. The
-- caller keeps the token; the database keeps only its hash, so a leaked backup
-- cannot be replayed against the ingestion endpoint.
-- -----------------------------------------------------------------------------
ALTER TABLE core.connection
    ADD COLUMN IF NOT EXISTS webhook_token_hash char(64),
    ADD COLUMN IF NOT EXISTS webhook_created_at timestamptz,
    ADD COLUMN IF NOT EXISTS default_kind text
        CHECK (default_kind IN ('shipments', 'movements', 'ads', 'cs'));

CREATE UNIQUE INDEX IF NOT EXISTS ux_connection_webhook_token
    ON core.connection (webhook_token_hash)
    WHERE webhook_token_hash IS NOT NULL;

COMMENT ON COLUMN core.connection.webhook_token_hash IS
    'SHA-256 of the webhook token. Shown once at creation and never again.';

-- =============================================================================
-- THE CATALOGUE
-- =============================================================================

-- Existing rows get their metadata.
UPDATE core.platform SET
    scope = 'global', category = 'archivos', auth_type = 'file',
    availability = 'available', sort_order = 1,
    setup_hint = 'Sube tus reportes en Excel o CSV. Data Effi detecta el país del archivo.'
WHERE code = 'manual_xlsx';

UPDATE core.platform SET
    category = 'fulfillment', auth_type = 'session', availability = 'available',
    sort_order = 10,
    setup_hint = 'Exporta los reportes de guías y movimientos, o autoriza el acceso con tu sesión (Tier 3).'
WHERE code = 'effi';

UPDATE core.platform SET
    category = 'fulfillment', auth_type = 'file', availability = 'available', sort_order = 11,
    setup_hint = 'Exporta el reporte de órdenes desde Dropi y súbelo.'
WHERE code = 'dropi';

UPDATE core.platform SET
    category = 'pauta', auth_type = 'oauth2', availability = 'planned', sort_order = 20,
    setup_hint = 'Necesita una app de Meta con permiso ads_read sobre tu cuenta publicitaria.'
WHERE code = 'meta_ads';

UPDATE core.platform SET
    category = 'pauta', auth_type = 'oauth2', availability = 'planned', sort_order = 21,
    setup_hint = 'Necesita acceso a la API de TikTok Ads sobre tu cuenta publicitaria.'
WHERE code = 'tiktok_ads';

UPDATE core.platform SET
    category = 'pauta', auth_type = 'oauth2', availability = 'planned', sort_order = 22,
    setup_hint = 'Necesita un developer token de Google Ads y acceso a la cuenta.'
WHERE code = 'google_ads';

UPDATE core.platform SET
    category = 'tienda', auth_type = 'api_key', availability = 'planned', sort_order = 30,
    setup_hint = 'Necesita un token de app privada de tu tienda Shopify.'
WHERE code = 'shopify';

UPDATE core.platform SET
    scope = 'global', category = 'archivos', auth_type = 'file',
    availability = 'available', sort_order = 5,
    setup_hint = 'Sube la hoja de confirmación de tu equipo de servicio al cliente.'
WHERE code = 'cs_sheet';

-- New platforms.
INSERT INTO core.platform
    (code, name, tier, data_domains, requires_consent, scope, category, auth_type,
     availability, direction, sort_order, setup_hint)
VALUES
    -- --- archivos y automatización: lo que funciona hoy ---
    ('webhook_generic', 'Webhook (n8n · Make · Zapier)', 1,
     ARRAY['shipments','movements','ads','cs'], false, 'global', 'automatizacion',
     'webhook', 'available', 'in', 2,
     'Data Effi te da una URL y un token. Cualquier automatización que sepa hacer un POST puede enviarte datos: n8n, Make, Zapier, o un script propio.'),

    ('google_sheets', 'Google Sheets', 1, ARRAY['shipments','movements','ads','cs'],
     false, 'global', 'archivos', 'api_key', 'beta', 'both', 3,
     'Publica la hoja como CSV (Archivo → Compartir → Publicar en la web) y pega la URL. Se relee en cada sincronización.'),

    ('google_drive', 'Google Drive', 1, ARRAY['shipments','movements'],
     false, 'global', 'archivos', 'oauth2', 'planned', 'in', 4,
     'Necesita autorizar una carpeta de Drive; Data Effi leerá los reportes nuevos que aparezcan ahí.'),

    ('email_inbox', 'Buzón de correo', 2, ARRAY['shipments','movements'],
     false, 'global', 'archivos', 'none', 'planned', 'in', 6,
     'Reenvía los reportes a un buzón propio de tu cuenta y se cargan solos.'),

    -- --- fulfillment COD de la región ---
    ('mastershop', 'Mastershop', 2, ARRAY['shipments','movements'], false,
     'country', 'fulfillment', 'file', 'planned', 'in', 12,
     'Exporta el reporte de órdenes desde Mastershop.'),

    ('hoko', 'Hoko', 2, ARRAY['shipments','movements'], false,
     'country', 'fulfillment', 'file', 'planned', 'in', 13,
     'Exporta el reporte de guías desde Hoko.'),

    -- --- tiendas ---
    ('woocommerce', 'WooCommerce', 1, ARRAY['shipments','catalog'], false,
     'country', 'tienda', 'api_key', 'planned', 'in', 31,
     'Necesita las claves de la REST API de WooCommerce.'),

    ('vtex', 'VTEX', 1, ARRAY['shipments','catalog'], false,
     'country', 'tienda', 'api_key', 'planned', 'in', 32,
     'Necesita App Key y App Token de tu cuenta VTEX.'),

    ('tiendanube', 'Tiendanube', 1, ARRAY['shipments','catalog'], false,
     'country', 'tienda', 'oauth2', 'planned', 'in', 33,
     'Necesita autorizar la app en tu tienda.'),

    ('mercadolibre', 'Mercado Libre', 1, ARRAY['shipments','catalog'], false,
     'country', 'tienda', 'oauth2', 'planned', 'in', 34,
     'Necesita autorizar la aplicación en tu cuenta de vendedor.'),

    -- --- CRM y marketing ---
    ('gohighlevel', 'GoHighLevel', 1, ARRAY['cs','ads'], false,
     'country', 'crm', 'api_key', 'planned', 'both', 40,
     'Necesita una API key de la sub-cuenta de GHL.'),

    ('activecampaign', 'ActiveCampaign', 1, ARRAY['cs'], false,
     'country', 'crm', 'api_key', 'planned', 'in', 41,
     'Necesita la URL de tu cuenta y una API key.'),

    ('whatsapp_cloud', 'WhatsApp Cloud API', 1, ARRAY['cs'], false,
     'country', 'crm', 'api_key', 'planned', 'both', 42,
     'Necesita el token permanente de tu app de WhatsApp Business.'),

    -- --- salida: llevarse los números a otro lado ---
    ('sheets_export', 'Exportar a Google Sheets', 1, ARRAY[]::text[], false,
     'global', 'archivos', 'api_key', 'planned', 'out', 7,
     'Escribe los KPI en una hoja tuya para que el equipo los vea sin entrar a Data Effi.'),

    ('webhook_out', 'Webhook de salida', 1, ARRAY[]::text[], false,
     'global', 'automatizacion', 'webhook', 'planned', 'out', 8,
     'Data Effi envía cada alerta a la URL que le des: n8n, Slack, WhatsApp, lo que uses.')
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    scope = EXCLUDED.scope,
    category = EXCLUDED.category,
    auth_type = EXCLUDED.auth_type,
    availability = EXCLUDED.availability,
    direction = EXCLUDED.direction,
    sort_order = EXCLUDED.sort_order,
    setup_hint = EXCLUDED.setup_hint;

-- Country availability: everything country-scoped is offered everywhere unless
-- it genuinely does not operate there. Effi and the LATAM COD platforms are the
-- exceptions and keep the rows migration 001 seeded.
INSERT INTO core.platform_country (platform_code, country_code)
SELECT p.code, c.code
FROM core.platform p
CROSS JOIN core.country c
WHERE p.scope = 'country'
  AND p.code NOT IN ('effi', 'dropi', 'mastershop', 'hoko')
ON CONFLICT DO NOTHING;

INSERT INTO core.platform_country (platform_code, country_code)
SELECT p.code, c.code
FROM core.platform p
CROSS JOIN core.country c
WHERE (p.code = 'mastershop' AND c.code IN ('CO', 'MX', 'PE', 'EC'))
   OR (p.code = 'hoko' AND c.code IN ('CO', 'EC'))
ON CONFLICT DO NOTHING;

-- =============================================================================
-- Views
-- =============================================================================

-- The catalogue as the settings screen shows it: everything, with its state.
CREATE OR REPLACE VIEW mart.v_platform_catalogue AS
SELECT
    p.code                                                      AS platform_code,
    p.name                                                      AS platform_name,
    p.tier,
    p.scope,
    p.category,
    p.auth_type,
    p.availability,
    p.direction,
    p.data_domains,
    p.requires_consent,
    p.setup_hint,
    p.docs_url,
    p.sort_order,
    -- Countries where it can be connected. Empty for global platforms.
    ARRAY(
        SELECT pc.country_code FROM core.platform_country pc
        WHERE pc.platform_code = p.code ORDER BY pc.country_code
    )                                                           AS available_countries
FROM core.platform p
WHERE p.is_active;

COMMENT ON VIEW mart.v_platform_catalogue IS
    'Every integration Data Effi knows about, including the ones that do not
     work yet. Hiding those would make the roadmap invisible.';

-- Available platforms per country, now scope-aware: a global platform shows up
-- for every active country without being tied to any of them.
CREATE OR REPLACE VIEW mart.v_available_platforms AS
WITH workspace AS (
    SELECT tenant_id, country_code
    FROM core.workspace_country
    WHERE tenant_id = core.current_tenant_id() AND is_active
),
offered AS (
    -- Country-scoped platforms, where they operate
    SELECT w.tenant_id, w.country_code, p.code AS platform_code
    FROM workspace w
    JOIN core.platform_country pc ON pc.country_code = w.country_code
    JOIN core.platform p ON p.code = pc.platform_code AND p.is_active AND p.scope = 'country'
    UNION
    -- Global platforms, offered against every active country
    SELECT w.tenant_id, w.country_code, p.code
    FROM workspace w
    CROSS JOIN core.platform p
    WHERE p.is_active AND p.scope = 'global'
)
SELECT
    o.tenant_id,
    o.country_code,
    p.code                                                      AS platform_code,
    p.name                                                      AS platform_name,
    p.tier,
    p.data_domains,
    p.requires_consent,
    p.docs_url,
    count(c.id)                                                 AS connection_count,
    count(c.id) FILTER (WHERE c.status = 'active')              AS active_connection_count,
    (count(c.id) FILTER (WHERE c.status = 'active') > 0)        AS is_connected,
    -- Appended, never inserted: CREATE OR REPLACE VIEW can only add columns at
    -- the end, and renaming one in place is what PostgreSQL refuses.
    p.scope,
    p.category,
    p.auth_type,
    p.availability,
    p.direction,
    p.setup_hint
FROM offered o
JOIN core.platform p ON p.code = o.platform_code
LEFT JOIN core.connection c
       ON c.tenant_id = o.tenant_id
      AND c.platform_code = p.code
      -- A global connection counts for every country; a country one only for its own.
      AND (c.country_code = o.country_code OR c.country_code IS NULL)
GROUP BY o.tenant_id, o.country_code, p.code, p.name, p.tier, p.scope, p.category,
         p.auth_type, p.availability, p.direction, p.data_domains,
         p.requires_consent, p.setup_hint, p.docs_url;

-- Connection health has to tolerate a NULL country now.
CREATE OR REPLACE VIEW mart.v_connection_health AS
SELECT
    c.tenant_id,
    c.id                                                        AS connection_id,
    c.name                                                      AS connection_name,
    c.country_code,
    c.platform_code,
    p.name                                                      AS platform_name,
    p.tier,
    c.status,
    c.consent_granted_at,
    c.last_sync_at,
    c.last_error,
    round(EXTRACT(EPOCH FROM (now() - c.last_sync_at)) / 3600.0, 1) AS hours_since_sync,
    b.last_batch_at,
    b.batches_7d,
    b.failed_batches_7d,
    CASE
        WHEN c.status = 'error'                           THEN 'error'
        WHEN c.status = 'disabled'                        THEN 'disabled'
        WHEN c.last_sync_at IS NULL                       THEN 'never_synced'
        WHEN c.last_sync_at < now() - interval '48 hours' THEN 'stale'
        ELSE 'ok'
    END                                                         AS health,
    -- Appended, not inserted: CREATE OR REPLACE VIEW can only add columns.
    p.scope,
    p.category,
    (c.webhook_token_hash IS NOT NULL)                          AS has_webhook
FROM core.connection c
JOIN core.platform p ON p.code = c.platform_code
LEFT JOIN LATERAL (
    SELECT
        max(lb.started_at)                              AS last_batch_at,
        count(*) FILTER (WHERE lb.started_at > now() - interval '7 days') AS batches_7d,
        count(*) FILTER (WHERE lb.started_at > now() - interval '7 days'
                           AND lb.status = 'failed')    AS failed_batches_7d
    FROM raw.load_batch lb
    WHERE lb.connection_id = c.id
) b ON true
WHERE c.tenant_id = core.current_tenant_id();

-- Domain status must count global connections for every country, or a manual
-- upload connection would leave every widget blocked.
CREATE OR REPLACE VIEW stg.v_country_domain_status AS
WITH declared AS (
    SELECT DISTINCT
        c.tenant_id,
        COALESCE(c.country_code, w.country_code) AS country_code,
        d.domain
    FROM core.connection c
    JOIN core.platform p ON p.code = c.platform_code
    CROSS JOIN LATERAL unnest(p.data_domains) AS d(domain)
    -- A global connection is declared against every country the tenant runs.
    LEFT JOIN core.workspace_country w
           ON w.tenant_id = c.tenant_id AND w.is_active AND c.country_code IS NULL
    WHERE c.status = 'active'
      AND (c.country_code IS NOT NULL OR w.country_code IS NOT NULL)
),
domains AS (
    SELECT wc.tenant_id, wc.country_code, d.domain
    FROM core.workspace_country wc
    CROSS JOIN (VALUES ('shipments'), ('movements'), ('ads'), ('cs'), ('catalog')) AS d(domain)
    WHERE wc.is_active
)
SELECT
    dm.tenant_id,
    dm.country_code,
    dm.domain,
    (dc.domain IS NOT NULL) AS has_connection,
    CASE dm.domain
        WHEN 'shipments' THEN EXISTS (SELECT 1 FROM core.shipment x
                                      WHERE x.tenant_id = dm.tenant_id AND x.country_code = dm.country_code)
        WHEN 'movements' THEN EXISTS (SELECT 1 FROM core.movement x
                                      WHERE x.tenant_id = dm.tenant_id AND x.country_code = dm.country_code)
        WHEN 'ads'       THEN EXISTS (SELECT 1 FROM core.ad_spend x
                                      WHERE x.tenant_id = dm.tenant_id AND x.country_code = dm.country_code)
        WHEN 'cs'        THEN EXISTS (SELECT 1 FROM core.cs_interaction x
                                      WHERE x.tenant_id = dm.tenant_id AND x.country_code = dm.country_code)
        WHEN 'catalog'   THEN EXISTS (SELECT 1 FROM core.product x WHERE x.tenant_id = dm.tenant_id)
        ELSE false
    END AS has_data
FROM domains dm
LEFT JOIN declared dc
       ON dc.tenant_id = dm.tenant_id
      AND dc.country_code = dm.country_code
      AND dc.domain = dm.domain;

GRANT SELECT ON mart.v_platform_catalogue TO norte_app;
