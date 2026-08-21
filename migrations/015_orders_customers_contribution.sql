-- =============================================================================
-- Data Effi - 015 - Orders, customers, and an honest contribution number.
--
-- Three things the operator asked for, and one they did not ask for but need.
--
-- 1. CONTRIBUTION, SPLIT. The headline contribution mixed matured guides with
--    guides still in the street. A guide that shipped yesterday has already
--    charged freight, product and platform fee, and has collected nothing -
--    not because it lost money, but because it has not arrived yet. Summing
--    both cohorts produced -3,376 on an operation whose delivered guides made
--    +8,666. The split reports what CLOSED (realised) apart from what is still
--    OUT (committed), and names the second for what it is: working capital in
--    the street.
--
-- 2. ORDERS. One row per guide, with the customer legible, plus everything the
--    detail card needs.
--
-- 3. CUSTOMERS. Grouped by `customer_hash`, which is deterministic, so the same
--    phone number is the same customer across every upload and every country.
--
-- ON THE ENCRYPTED COLUMNS
-- The operator chose reversible encryption over plaintext for contact data.
-- The ciphertext lives here; the key does NOT - it is an environment variable
-- read by pipeline/crypto.py, so encryption and decryption happen in Python and
-- the key never appears in a statement, a query plan, or pg_stat_statements.
-- These columns are bytea and are useless to anything that cannot call Fernet.
--
-- `customer_hash` stays exactly as it was, and every metric keeps grouping by
-- it. Identity and display are deliberately separate: the hash is
-- deterministic and irreversible, the ciphertext is randomised and reversible.
--
-- Depends on: 014. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Contact data, encrypted at rest
-- -----------------------------------------------------------------------------
ALTER TABLE core.shipment
    ADD COLUMN IF NOT EXISTS customer_name_enc     bytea,
    ADD COLUMN IF NOT EXISTS customer_phone_enc    bytea,
    ADD COLUMN IF NOT EXISTS customer_document_enc bytea,
    ADD COLUMN IF NOT EXISTS customer_address_enc  bytea,
    ADD COLUMN IF NOT EXISTS customer_city_name    text;

COMMENT ON COLUMN core.shipment.customer_phone_enc IS
    'Fernet ciphertext. The key is an env var read by pipeline/crypto.py and is
     never sent to the database. Randomised: the same phone encrypts differently
     each time, so identical values cannot be counted. Group by customer_hash,
     never by this column.';

COMMENT ON COLUMN core.shipment.customer_hash IS
    'Salted SHA-256 of the customer identity. Deterministic and irreversible:
     the join key for every customer metric. Not for display - the encrypted
     columns exist for that.';

-- The customer views group by this on every query.
CREATE INDEX IF NOT EXISTS ix_shipment_customer_hash
    ON core.shipment (tenant_id, customer_hash)
    WHERE customer_hash IS NOT NULL;

-- The orders table is read newest-first, filtered by country.
CREATE INDEX IF NOT EXISTS ix_shipment_orders_browse
    ON core.shipment (tenant_id, country_code, created_date DESC);

-- -----------------------------------------------------------------------------
-- Contribution, split by whether the guide has finished
-- -----------------------------------------------------------------------------
-- realised_*  - guides that reached a terminal state. Money that is settled.
-- committed_* - guides still moving. Costs already incurred, revenue not yet
--               collected. `capital_in_street` is that exposure as a positive
--               number, because "how much of my money is out there" is a
--               question about capital, not a loss.
CREATE OR REPLACE VIEW mart.v_contribution_split AS
WITH e AS (
    SELECT
        s.country_code,
        s.is_terminal,
        s.is_delivered,
        s.is_returned,
        s.revenue_amount,
        s.freight_amount,
        s.cogs_amount,
        s.fee_amount
    FROM stg.v_shipment_economics s
    WHERE s.tenant_id = core.current_tenant_id()
)
SELECT
    e.country_code,
    co.currency_code,

    count(*)                                                    AS shipments,
    count(*) FILTER (WHERE e.is_terminal)                       AS closed_shipments,
    count(*) FILTER (WHERE NOT e.is_terminal)                   AS open_shipments,

    -- What actually happened.
    sum(e.revenue_amount)  FILTER (WHERE e.is_terminal)::numeric(14,2)
                                                                AS realised_revenue,
    sum(e.freight_amount + e.cogs_amount + e.fee_amount)
        FILTER (WHERE e.is_terminal)::numeric(14,2)             AS realised_cost,
    sum(e.revenue_amount - e.freight_amount - e.cogs_amount - e.fee_amount)
        FILTER (WHERE e.is_terminal)::numeric(14,2)             AS realised_contribution,
    round(
        sum(e.revenue_amount - e.freight_amount - e.cogs_amount - e.fee_amount)
            FILTER (WHERE e.is_terminal)
        / NULLIF(sum(e.revenue_amount) FILTER (WHERE e.is_terminal), 0) * 100, 2
    )                                                           AS realised_margin_pct,

    -- What is still out there.
    sum(e.freight_amount + e.cogs_amount + e.fee_amount)
        FILTER (WHERE NOT e.is_terminal)::numeric(14,2)         AS capital_in_street,
    sum(e.revenue_amount)
        FILTER (WHERE NOT e.is_terminal)::numeric(14,2)         AS committed_revenue,

    -- The number the dashboard used to show alone, kept so nothing that reads
    -- it breaks - but now next to the two halves that explain it.
    sum(e.revenue_amount - e.freight_amount - e.cogs_amount - e.fee_amount)
        ::numeric(14,2)                                         AS net_contribution,

    -- How much of the operation has finished. Below ~60% the realised figure is
    -- a small sample and the UI says so instead of implying a trend.
    round(count(*) FILTER (WHERE e.is_terminal)::numeric
          / NULLIF(count(*), 0) * 100, 1)                       AS maturity_pct
FROM e
JOIN core.country co ON co.code = e.country_code
GROUP BY e.country_code, co.currency_code;

COMMENT ON VIEW mart.v_contribution_split IS
    'Contribution separated into what closed and what is still in the street.
     A single net number reads as a loss whenever a young cohort is large, which
     is a statement about timing, not profitability.';

-- -----------------------------------------------------------------------------
-- Orders: one row per guide
-- -----------------------------------------------------------------------------
-- The encrypted columns pass through as bytea. The API decrypts them, gated by
-- role, so a viewer never receives contact data at all.
CREATE OR REPLACE VIEW mart.v_orders AS
SELECT
    s.id                                                        AS shipment_id,
    s.country_code,
    s.tracking_number,
    s.carrier_tracking_number,
    s.created_date,
    s.delivered_at,
    s.status_code,
    sc.label                                                    AS status_label,
    sc.bucket                                                   AS status_bucket,
    sc.is_terminal,
    sc.is_delivered,
    sc.is_returned,

    s.customer_hash,
    s.customer_name_enc,
    s.customer_phone_enc,
    s.customer_address_enc,
    s.customer_document_enc,
    COALESCE(s.customer_city_name, g.city_name)                 AS city_name,
    g.level1_name                                               AS province_name,

    p.name                                                      AS product_name,
    s.quantity,
    ca.name                                                     AS carrier_name,

    ec.revenue_amount,
    ec.freight_amount,
    ec.cogs_amount,
    ec.fee_amount,
    (ec.revenue_amount - ec.freight_amount - ec.cogs_amount - ec.fee_amount)
        ::numeric(14,2)                                         AS contribution,
    co.currency_code,

    ec.days_open,
    ec.movement_count
FROM core.shipment s
JOIN core.status_canon sc     ON sc.code = s.status_code
JOIN core.country co          ON co.code = s.country_code
JOIN stg.v_shipment_economics ec ON ec.shipment_id = s.id
LEFT JOIN core.geo g          ON g.id = s.geo_id
LEFT JOIN core.product p      ON p.id = s.product_id
LEFT JOIN core.carrier ca     ON ca.id = s.carrier_id
WHERE s.tenant_id = core.current_tenant_id();

COMMENT ON VIEW mart.v_orders IS
    'One row per guide for the orders table and detail card. Contact columns are
     ciphertext; only the API decrypts them, and only for owner/analyst.';

-- -----------------------------------------------------------------------------
-- Customers: grouped by the deterministic hash
-- -----------------------------------------------------------------------------
-- `last_name_enc` carries the most recent spelling of the name so the list can
-- be labelled without a second query. Picking the latest guide's value matters:
-- a customer who corrected their name should show the correction.
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
            FILTER (WHERE o.customer_phone_enc IS NOT NULL))[1] AS last_phone_enc
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
    pc.last_phone_enc
FROM per_customer pc;

COMMENT ON VIEW mart.v_customer_metrics IS
    'One row per customer per country, grouped by the deterministic
     customer_hash - the same phone is the same customer across every upload.
     Contact columns are ciphertext, decrypted by the API for owner/analyst.';

-- -----------------------------------------------------------------------------
-- Traceability: every event of one guide, in order
-- -----------------------------------------------------------------------------
-- The detail card needs a timeline, and a COD guide's history lives in two
-- places: the status it reached, and the money that moved. This unions them so
-- the card renders one chronological list instead of two tables the reader has
-- to interleave by eye.
CREATE OR REPLACE VIEW mart.v_order_timeline AS
SELECT
    s.id                                    AS shipment_id,
    s.tracking_number,
    'estado'::text                          AS event_kind,
    sc.label                                AS event_label,
    NULL::numeric                           AS amount,
    NULL::text                              AS currency_code,
    COALESCE(s.delivered_at::date, s.created_date) AS event_date,
    sc.sort_order                           AS event_order,
    NULL::text                              AS reference
FROM core.shipment s
JOIN core.status_canon sc ON sc.code = s.status_code
WHERE s.tenant_id = core.current_tenant_id()

UNION ALL

SELECT
    m.shipment_id,
    s.tracking_number,
    'dinero'::text,
    mt.label,
    m.amount,
    m.currency_code,
    m.movement_date,
    1000,
    m.external_ref
FROM core.movement m
JOIN core.shipment s     ON s.id = m.shipment_id
JOIN core.movement_type mt ON mt.code = m.movement_type_code
WHERE m.tenant_id = core.current_tenant_id()
  AND m.shipment_id IS NOT NULL;

COMMENT ON VIEW mart.v_order_timeline IS
    'Status and money events of one guide in a single chronological stream, for
     the detail card. Order by event_date, then event_order.';

-- -----------------------------------------------------------------------------
-- Widgets
-- -----------------------------------------------------------------------------
-- The catalogue's tab list is a CHECK constraint, so a new section has to be
-- admitted before a widget can name it. 'clientes' is a real new section: it
-- answers questions about people, not about guides or money.
ALTER TABLE core.widget_catalog DROP CONSTRAINT IF EXISTS widget_catalog_tab_check;
ALTER TABLE core.widget_catalog ADD CONSTRAINT widget_catalog_tab_check
    CHECK (tab = ANY (ARRAY['finanzas','logistica','efectividad','servicio',
                            'global','clientes']));

INSERT INTO core.widget_catalog
    (widget_code, tab, title, description, required_domains, optional_domains,
     blocked_message, sort_order, is_active)
VALUES
    ('contribution_split', 'finanzas', 'Contribución: cerrada vs. en calle',
     'Separa lo que ya se cobró de lo que sigue en la calle, para que una '
     'cohorte joven no se lea como pérdida.',
     ARRAY['shipments'], ARRAY['movements'],
     'Sube tu reporte de guías para ver esta separación.', 4, true),
    ('capital_in_street', 'finanzas', 'Capital en la calle',
     'Flete, producto y comisión ya pagados de guías que aún no cierran. Es '
     'tu capital de trabajo comprometido ahora mismo.',
     ARRAY['shipments'], ARRAY['movements'],
     'Sube tu reporte de guías para saber cuánto capital tienes en la calle.', 5, true),
    ('customer_quality', 'clientes', 'Calidad de clientes',
     'Clientes agrupados por teléfono: cuántos pedidos, qué tanto te devuelven '
     'y cuánto aportan.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Sube tu reporte de guías para agrupar clientes por teléfono.', 40, true)
ON CONFLICT (widget_code) DO UPDATE SET
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    sort_order = EXCLUDED.sort_order,
    is_active = EXCLUDED.is_active;

GRANT SELECT ON mart.v_contribution_split, mart.v_orders,
                mart.v_customer_metrics, mart.v_order_timeline
    TO norte_app;

-- Deliberately NOT granted to norte_readonly. That role backs the NL->SQL
-- copilot, and a generated query must never reach row-level customer data -
-- ciphertext included, because a row is still a record of a real person.
