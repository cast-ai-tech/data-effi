-- =============================================================================
-- Data Effi - 017 - The rest of the customer's contact data, most recent first.
--
-- Migration 015 gave `mart.v_customer_metrics` the customer's latest name and
-- phone. It stopped there because the customers TABLE only ever needed a label.
-- The detail card needs more: the operator opening one customer is usually
-- about to send them a parcel, and a parcel needs an address.
--
-- WHY THE VIEW AND NOT THE ROUTER
-- "The most recent order that has one wins" is a rule about the data, and the
-- reason for it is a fact about customers: someone who corrected their address
-- must be seen corrected, or the next parcel goes to the old one. That rule
-- already lives here for name and phone. Splitting it - two fields resolved in
-- SQL, two in Python - is how the two halves drift apart.
--
-- EACH FIELD IS RESOLVED INDEPENDENTLY, on purpose. A customer whose newest
-- guide carries an address but no document should show the address from that
-- guide and the document from whichever older one had it. Taking "the newest
-- guide" as a whole row would blank a field that is perfectly well known.
--
-- WHY THIS DOES NOT MAKE THE LIST LEAKIER
-- The columns are ciphertext, and the customers table names its columns
-- explicitly - it does not SELECT *. A page of 200 customers still decrypts
-- exactly two fields per row. Decrypting one address for one customer the
-- operator deliberately opened is proportionate; decrypting two hundred
-- because they scrolled a table is not.
--
-- `last_city_name` and `last_province_name` are NOT ciphertext and are NOT
-- gated: a city is not an identity, and `main_city` has been visible to every
-- role since 015. They are here because a street line without a city is not an
-- address, and the point of this migration is a usable one. The street line
-- and the document stay encrypted, because those do identify a household.
--
-- Depends on: 015. Idempotent.
-- =============================================================================

-- CREATE OR REPLACE, not DROP: replacing keeps the GRANT to norte_app and keeps
-- the deliberate absence of one to norte_readonly. The existing columns keep
-- their exact order and types - the four new ones are appended, which is the
-- only shape PostgreSQL accepts for a replace.
CREATE OR REPLACE VIEW mart.v_customer_metrics AS
WITH per_customer AS (
    SELECT
        o.customer_hash,
        o.country_code,
        count(*)                                                AS orders,
        count(*) FILTER (WHERE o.is_delivered)                  AS delivered,
        count(*) FILTER (WHERE o.is_returned)                   AS returned,
        count(*) FILTER (WHERE NOT o.is_terminal)               AS open_orders,
        sum(o.revenue_amount)::numeric(14,2)                    AS revenue,
        sum(o.contribution)::numeric(14,2)                      AS contribution,
        sum(o.freight_amount + o.cogs_amount + o.fee_amount)
            FILTER (WHERE o.is_returned)::numeric(14,2)         AS returned_cost,
        min(o.created_date)                                     AS first_order_date,
        max(o.created_date)                                     AS last_order_date,
        max(o.currency_code)                                    AS currency_code,
        count(DISTINCT o.product_name)                          AS distinct_products,
        mode() WITHIN GROUP (ORDER BY o.city_name)              AS main_city,
        (array_agg(o.customer_name_enc ORDER BY o.created_date DESC)
            FILTER (WHERE o.customer_name_enc IS NOT NULL))[1]  AS last_name_enc,
        (array_agg(o.customer_phone_enc ORDER BY o.created_date DESC)
            FILTER (WHERE o.customer_phone_enc IS NOT NULL))[1] AS last_phone_enc,

        -- New in 017. Same rule, one field at a time.
        (array_agg(o.customer_document_enc ORDER BY o.created_date DESC)
            FILTER (WHERE o.customer_document_enc IS NOT NULL))[1] AS last_document_enc,
        (array_agg(o.customer_address_enc ORDER BY o.created_date DESC)
            FILTER (WHERE o.customer_address_enc IS NOT NULL))[1]  AS last_address_enc,
        -- Where the last parcel actually went, which is not the same question
        -- as `main_city` (where most of them went). The address needs this one.
        (array_agg(o.city_name ORDER BY o.created_date DESC)
            FILTER (WHERE o.city_name IS NOT NULL))[1]          AS last_city_name,
        (array_agg(o.province_name ORDER BY o.created_date DESC)
            FILTER (WHERE o.province_name IS NOT NULL))[1]      AS last_province_name
    FROM mart.v_orders o
    WHERE o.customer_hash IS NOT NULL
    GROUP BY o.customer_hash, o.country_code
)
SELECT
    pc.customer_hash,
    -- A short, stable label for a customer nobody has decrypted yet.
    ('#' || upper(left(pc.customer_hash, 6)))                   AS customer_ref,
    pc.country_code,
    pc.currency_code,
    pc.orders,
    pc.delivered,
    pc.returned,
    pc.open_orders,
    pc.revenue,
    pc.contribution,
    pc.returned_cost,
    pc.distinct_products,
    pc.main_city,
    pc.first_order_date,
    pc.last_order_date,
    (CURRENT_DATE - pc.last_order_date)                         AS days_since_last_order,

    -- Delivery rate over CLOSED orders only, for the same reason the global one
    -- is split: an open order is not a failed one.
    round(pc.delivered::numeric
          / NULLIF(pc.delivered + pc.returned, 0) * 100, 1)     AS delivery_rate_pct,
    round(pc.contribution / NULLIF(pc.orders, 0), 2)            AS contribution_per_order,

    -- A grade, not a score out of a hundred: with two orders there is no
    -- statistical basis for a number, and 'nuevo' says that honestly.
    CASE
        WHEN pc.delivered + pc.returned < 2                        THEN 'nuevo'
        WHEN pc.returned = 0 AND pc.delivered >= 3                 THEN 'excelente'
        WHEN pc.delivered::numeric
             / NULLIF(pc.delivered + pc.returned, 0) >= 0.8        THEN 'bueno'
        WHEN pc.delivered::numeric
             / NULLIF(pc.delivered + pc.returned, 0) >= 0.5        THEN 'regular'
        ELSE 'riesgo'
    END                                                         AS customer_grade,
    pc.last_name_enc,
    pc.last_phone_enc,

    -- Appended by 017.
    pc.last_document_enc,
    pc.last_address_enc,
    pc.last_city_name,
    pc.last_province_name
FROM per_customer pc;

COMMENT ON VIEW mart.v_customer_metrics IS
    'One row per customer per country, grouped by the deterministic
     customer_hash - the same phone is the same customer across every upload.
     ONE ROW PER COUNTRY is not an accident: each country has its own currency,
     so summing a customer across countries would add COP to USD and report a
     number that means nothing. Contact columns are ciphertext, decrypted by
     the API for owner/analyst, and each resolves to the most recent guide that
     carried that particular field.';

COMMENT ON COLUMN mart.v_customer_metrics.last_address_enc IS
    'Fernet ciphertext of the most recent address on file. Read by the customer
     detail card only - never by the customers table, which would decrypt one
     address per row for a page nobody asked to see addresses on.';

COMMENT ON COLUMN mart.v_customer_metrics.last_city_name IS
    'City of the most recent guide. Not the same as main_city (the most
     frequent one): this is where the last parcel went, and it is the half of
     the address that makes the street line usable.';

GRANT SELECT ON mart.v_customer_metrics TO norte_app;
