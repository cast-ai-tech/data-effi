-- =============================================================================
-- Data Effi - 038 - República Dominicana, Venezuela, y la tabla de conversión.
--
-- TWO COUNTRIES
-- Same three steps as Honduras (033) and Costa Rica (034): the row, the aliases,
-- and the platform availability.
--
-- Venezuela is formatted like Colombia - thousands with '.', decimals with ',' -
-- and quoted with two decimals, because the bolívar after the 2021 redenomination
-- is used with céntimos in electronic payments even though cash rounds.
--
-- ABOUT THE BOLÍVAR, SAID OUT LOUD
-- VES moves faster than a daily rate can follow, and the official rate and the
-- one the street uses are not the same number. Data Effi does not pretend
-- otherwise: it stores whatever the configured provider returns, stamps it with
-- the date it was fetched, and the conversion view below reports that date next
-- to every figure. An operator who needs the parallel rate can overwrite the row
-- - `core.fx_rate.source` exists to tell 'api' apart from 'manual'.
--
-- ONE VIEW FOR CONVERSIONS
-- `core.fx_rate` stores exactly one direction: X -> USD. Storing X -> COP as well
-- would be a second copy of the same fact, and two copies drift: the day the COP
-- rate is written and the GTQ one is not, a quetzal is worth two different
-- amounts depending on which row you read.
--
-- So the peso column is DERIVED here, on the fly:
--
--     X -> COP  =  (X -> USD) / (COP -> USD)
--
-- and it is NULL whenever either half is missing, rather than falling back to
-- something plausible. Migration 033 already made the rule explicit: a made-up
-- rate silently distorts every total that uses it.
--
-- Depends on: 001-037. Idempotent.
-- =============================================================================

INSERT INTO core.country (code, name, currency_code, currency_symbol, decimal_places,
                          thousands_sep, decimal_sep, date_format, locale, timezone,
                          geo_level1_label)
VALUES
    ('DO', 'República Dominicana', 'DOP', 'RD$', 2, ',', '.', 'dd/MM/yyyy', 'es-DO',
     'America/Santo_Domingo', 'Provincia'),
    ('VE', 'Venezuela', 'VES', 'Bs.', 2, '.', ',', 'dd/MM/yyyy', 'es-VE',
     'America/Caracas', 'Estado')
ON CONFLICT (code) DO NOTHING;

INSERT INTO core.country_alias (alias_norm, country_code) VALUES
    ('do', 'DO'), ('dom', 'DO'), ('rd', 'DO'),
    ('republica dominicana', 'DO'), ('dominicana', 'DO'), ('santo domingo', 'DO'),
    ('ve', 'VE'), ('ven', 'VE'), ('venezuela', 'VE'),
    ('republica bolivariana de venezuela', 'VE')
ON CONFLICT (alias_norm) DO NOTHING;

-- Same rule as migration 001: the country-specific COD platforms declare where
-- they operate, everything else is available everywhere.
INSERT INTO core.platform_country (platform_code, country_code)
SELECT p.code, c.code
FROM core.platform p
CROSS JOIN (VALUES ('DO'), ('VE')) AS c(code)
WHERE p.code NOT IN ('effi', 'dropi')
ON CONFLICT DO NOTHING;

-- =============================================================================
-- mart.v_fx_rates - every supported currency against the dollar AND the peso.
--
-- One row per currency in core.country, so a country added tomorrow appears here
-- without touching this view. The currency of a country with no rate yet shows
-- up with NULLs and `has_rate = false`, which is the honest answer and the one
-- the screen renders as "sin tasa".
-- =============================================================================
CREATE OR REPLACE VIEW mart.v_fx_rates AS
WITH monedas AS (
    SELECT
        c.currency_code,
        min(c.currency_symbol)                                    AS currency_symbol,
        min(c.decimal_places)                                     AS decimal_places,
        array_agg(c.code ORDER BY c.code)                         AS country_codes,
        array_agg(c.name ORDER BY c.code)                         AS country_names
    FROM core.country c
    WHERE c.is_supported
    GROUP BY c.currency_code
),
ultima AS (
    SELECT DISTINCT ON (base_currency)
           base_currency, rate, rate_date, source
    FROM core.fx_rate
    WHERE quote_currency = 'USD'
    ORDER BY base_currency, rate_date DESC
),
peso AS (
    SELECT rate AS cop_to_usd FROM ultima WHERE base_currency = 'COP'
)
SELECT
    m.currency_code,
    m.currency_symbol,
    m.decimal_places,
    m.country_codes,
    m.country_names,
    -- Cuánto vale UNA unidad de esta moneda.
    u.rate                                              AS to_usd,
    CASE
        WHEN u.rate IS NULL OR p.cop_to_usd IS NULL THEN NULL
        ELSE u.rate / p.cop_to_usd
    END                                                 AS to_cop,
    -- Y el camino de vuelta, que es como la gente cotiza: "el dólar está a X".
    CASE WHEN u.rate IS NULL OR u.rate = 0 THEN NULL ELSE 1 / u.rate END
                                                        AS per_usd,
    u.rate_date,
    u.source,
    (u.rate IS NOT NULL)                                AS has_rate,
    -- Una tasa de hace más de tres días ya no describe el día de hoy. No se
    -- oculta: se marca, y la pantalla lo dice.
    CASE
        WHEN u.rate_date IS NULL THEN NULL
        ELSE (CURRENT_DATE - u.rate_date) > 3
    END                                                 AS is_stale
FROM monedas m
LEFT JOIN ultima u ON u.base_currency = m.currency_code
LEFT JOIN peso p ON true
ORDER BY m.currency_code;

COMMENT ON VIEW mart.v_fx_rates IS
    'Cada moneda soportada contra el dólar y contra el peso colombiano. El peso se deriva de la tasa a USD; nunca se guarda dos veces.';

GRANT SELECT ON mart.v_fx_rates TO norte_app;
