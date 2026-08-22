-- =============================================================================
-- Data Effi - 033 - Honduras.
--
-- The operator runs a partnership across Guatemala and Honduras, and the second
-- half of it could not be activated: `core.country` had seven rows and Honduras
-- was not one of them, so `POST /org/tenants` refused it as "país no soportado".
--
-- Three things are needed for a country to be usable end to end:
--   1. the row here (formatting, currency, timezone),
--   2. the aliases, so a file that says "HND" or "honduras" resolves to HN,
--   3. the platform availability, so a connection can be created for it.
--
-- FX: lempiras convert to USD through core.fx_rate like every other currency.
-- No rate is seeded - a made-up rate silently distorts the consolidated total,
-- and the roll-up already reports a company it could not convert instead of
-- guessing (see api/routers/org.py).
--
-- Depends on: 001-032. Idempotent.
-- =============================================================================

INSERT INTO core.country (code, name, currency_code, currency_symbol, decimal_places,
                          thousands_sep, decimal_sep, date_format, locale, timezone,
                          geo_level1_label)
VALUES
    ('HN', 'Honduras', 'HNL', 'L', 2, ',', '.', 'dd/MM/yyyy', 'es-HN', 'America/Tegucigalpa',
     'Departamento')
ON CONFLICT (code) DO NOTHING;

INSERT INTO core.country_alias (alias_norm, country_code) VALUES
    ('hn', 'HN'), ('hnd', 'HN'), ('honduras', 'HN'), ('republica de honduras', 'HN')
ON CONFLICT (alias_norm) DO NOTHING;

-- Same rule as migration 001: the country-specific COD platforms declare where
-- they operate, everything else is available everywhere.
INSERT INTO core.platform_country (platform_code, country_code)
SELECT p.code, 'HN'
FROM core.platform p
WHERE p.code NOT IN ('effi', 'dropi')
ON CONFLICT DO NOTHING;
