-- =============================================================================
-- Data Effi - 034 - Costa Rica.
--
-- Distrilatam operates in Colombia, Ecuador, Guatemala and Costa Rica. The first
-- three were already in `core.country`; Costa Rica was not, so activating it on
-- a company failed with "país no soportado" - the same wall Honduras hit in 033,
-- which is the exact template for this file.
--
-- Three things are needed for a country to be usable end to end:
--   1. the row here (formatting, currency, timezone),
--   2. the aliases, so a file that says "CRI" or "costa rica" resolves to CR,
--   3. the platform availability, so a connection can be created for it.
--
-- ZERO DECIMALS, LIKE COLOMBIA AND CHILE. The colón has céntimos, but they do
-- not circulate: Costa Rican commerce quotes whole numbers (₡12.500, never
-- ₡12.500,00). Rendering two decimals that are always ",00" adds noise to every
-- figure on the dashboard. If they are ever needed this is a one-row UPDATE, not
-- a migration:
--     UPDATE core.country SET decimal_places = 2 WHERE code = 'CR';
--
-- FX: colones convert to USD through core.fx_rate like every other currency. No
-- rate is seeded - a made-up rate silently distorts the consolidated total, and
-- the roll-up already reports a company it could not convert instead of guessing
-- (see api/routers/org.py).
--
-- Depends on: 001-033. Idempotent.
-- =============================================================================

INSERT INTO core.country (code, name, currency_code, currency_symbol, decimal_places,
                          thousands_sep, decimal_sep, date_format, locale, timezone,
                          geo_level1_label)
VALUES
    ('CR', 'Costa Rica', 'CRC', '₡', 0, '.', ',', 'dd/MM/yyyy', 'es-CR',
     'America/Costa_Rica', 'Provincia')
ON CONFLICT (code) DO NOTHING;

INSERT INTO core.country_alias (alias_norm, country_code) VALUES
    ('cr', 'CR'), ('cri', 'CR'), ('costa rica', 'CR'), ('republica de costa rica', 'CR')
ON CONFLICT (alias_norm) DO NOTHING;

-- Same rule as migration 001: the country-specific COD platforms declare where
-- they operate, everything else is available everywhere.
INSERT INTO core.platform_country (platform_code, country_code)
SELECT p.code, 'CR'
FROM core.platform p
WHERE p.code NOT IN ('effi', 'dropi')
ON CONFLICT DO NOTHING;
