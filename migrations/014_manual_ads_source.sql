-- =============================================================================
-- Data Effi - 014 - Ad spend you can load today.
--
-- Migration 012 listed Meta, TikTok and Google Ads as 'planned', which is
-- honest: none of them has an OAuth app yet. But that left ad spend with no
-- path into the platform at all, and therefore CPA, ROAS and contribution-after
-- -media unreachable for everybody - including the demo.
--
-- Every one of those platforms exports a CSV from its own reporting screen. So
-- the answer is not to pretend the API integrations exist; it is to accept the
-- export, the same way Data Effi already accepts Effi's and Dropi's. When the
-- APIs arrive they will fill the same table and every widget keeps working.
--
-- Depends on: 012. Idempotent.
-- =============================================================================

INSERT INTO core.platform
    (code, name, tier, data_domains, requires_consent, scope, category, auth_type,
     availability, direction, sort_order, setup_hint)
VALUES
    ('ads_manual', 'Pauta (carga manual)', 2, ARRAY['ads'], false,
     'country', 'pauta', 'file', 'available', 'in', 19,
     'Exporta el reporte de tu plataforma de pauta (Meta, TikTok, Google) y súbelo. '
     'Necesita al menos: fecha, campaña e inversión. Sirve mientras llegan las APIs.')
ON CONFLICT (code) DO UPDATE SET
    availability = EXCLUDED.availability,
    setup_hint = EXCLUDED.setup_hint,
    sort_order = EXCLUDED.sort_order;

INSERT INTO core.platform_country (platform_code, country_code)
SELECT 'ads_manual', code FROM core.country
ON CONFLICT DO NOTHING;

-- Column aliases for an ad-spend report, in the shapes the three big platforms
-- write them. Kept in SQL for the same reason the status aliases are: the
-- ingestion engine mirrors this list in Python and a test compares the two.
CREATE TABLE IF NOT EXISTS core.ads_column_alias (
    alias_norm text PRIMARY KEY,
    field_name text NOT NULL
);

INSERT INTO core.ads_column_alias (alias_norm, field_name) VALUES
    -- date
    ('dia', 'spend_date'), ('fecha', 'spend_date'), ('day', 'spend_date'),
    ('date', 'spend_date'), ('reporting starts', 'spend_date'),
    ('inicio del informe', 'spend_date'), ('fecha de inicio', 'spend_date'),
    -- campaign
    ('campana', 'campaign_name'), ('campaign', 'campaign_name'),
    ('nombre de la campana', 'campaign_name'), ('campaign name', 'campaign_name'),
    ('nombre del conjunto de anuncios', 'campaign_name'),
    -- money
    ('importe gastado', 'spend'), ('importe gastado (usd)', 'spend'),
    ('amount spent', 'spend'), ('amount spent (usd)', 'spend'),
    ('inversion', 'spend'), ('gasto', 'spend'), ('costo', 'spend'),
    ('cost', 'spend'), ('spend', 'spend'), ('total gastado', 'spend'),
    -- volume
    ('impresiones', 'impressions'), ('impressions', 'impressions'),
    ('clics', 'clicks'), ('clicks', 'clicks'), ('link clicks', 'clicks'),
    ('clics en el enlace', 'clicks'),
    ('resultados', 'conversions'), ('results', 'conversions'),
    ('conversiones', 'conversions'), ('conversions', 'conversions'),
    ('compras', 'conversions'), ('purchases', 'conversions'),
    -- optional
    ('moneda', 'currency_code'), ('currency', 'currency_code'),
    ('producto', 'product_name'), ('product', 'product_name')
ON CONFLICT (alias_norm) DO NOTHING;

COMMENT ON TABLE core.ads_column_alias IS
    'Header spellings an ad-spend export uses, across Meta, TikTok and Google.
     Mirrored in pipeline/mapping.py; a test asserts the two agree.';
