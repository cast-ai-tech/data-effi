-- =============================================================================
-- Data Effi - 030 - Money that belongs to no guide was invisible.
--
-- `stg.v_movement_by_shipment` filters `WHERE shipment_id IS NOT NULL`, and it
-- is the only aggregation of `core.movement` in the schema. Anything the
-- carrier's wallet does that is not tied to one guide therefore appeared in no
-- metric at all. In the operator's real data that is 14,236.52 USD across eight
-- movements:
--
--     Retiro a cuenta bancaria      3 mov   10,360.00   money leaving the wallet
--     Retención en la fuente        2 mov    3,873.52   tax withheld at source
--     Comisión por retiro           3 mov        3.00   a real cost
--
-- None of it is wrong to exclude from CONTRIBUTION - a bank transfer is not a
-- cost of selling, and contribution is a per-guide measure. The problem is that
-- it was excluded from EVERYTHING, so the wallet could never be reconciled: an
-- operator adding up what the platform showed would come up 14,236.52 short of
-- their Effi balance and have no way to find the difference.
--
-- This view is deliberately separate from the guide economics rather than mixed
-- into them. Two different questions - "does this guide make money?" and "where
-- did the wallet balance go?" - and merging them is what would corrupt the
-- first one.
--
-- Depends on: 029. Idempotent.
-- =============================================================================

CREATE OR REPLACE VIEW mart.v_wallet_movements AS
SELECT
    m.country_code,
    date_trunc('month', m.movement_date)::date                  AS month,
    mt.code                                                     AS movement_type_code,
    mt.label                                                    AS movement_label,
    mt.category,
    mt.sign,
    count(*)                                                    AS movements,
    sum(m.amount)::numeric(14, 2)                               AS total_amount,
    min(m.movement_date)                                        AS first_movement_date,
    max(m.movement_date)                                        AS last_movement_date,
    min(m.currency_code)                                        AS currency_code
FROM core.movement m
JOIN core.movement_type mt ON mt.code = m.movement_type_code
WHERE m.tenant_id = core.current_tenant_id()
  -- Wallet-level only. A movement that belongs to a guide is already counted in
  -- that guide's economics, and counting it twice is exactly the class of bug
  -- migration 023 had to undo.
  AND m.shipment_id IS NULL
GROUP BY m.country_code, date_trunc('month', m.movement_date), mt.code, mt.label,
         mt.category, mt.sign;

COMMENT ON VIEW mart.v_wallet_movements IS
    'Movimientos de la billetera que no pertenecen a ninguna guía: retiros,
     retenciones y sus comisiones. Están fuera de la contribución a propósito -
     un retiro no es un costo de vender - pero sin ellos el saldo de Effi no
     cuadra con lo que muestra la plataforma.';

INSERT INTO core.widget_catalog
    (widget_code, tab, title, description, required_domains, optional_domains,
     blocked_message, sort_order, is_active)
VALUES
    ('wallet_movements', 'finanzas', 'Movimientos de billetera',
     'Retiros a tu banco, retenciones y comisiones: el dinero que se mueve sin '
     'pertenecer a una guía. Es lo que explica la diferencia entre tu saldo en '
     'Effi y la contribución de tus envíos.',
     ARRAY['movements'], ARRAY[]::text[],
     'Sube tu reporte de movimientos de dinero para ver esta sección.', 6, true)
ON CONFLICT (widget_code) DO UPDATE SET
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    sort_order = EXCLUDED.sort_order,
    is_active = EXCLUDED.is_active;

GRANT SELECT ON mart.v_wallet_movements TO norte_app;

-- Not granted to norte_readonly: the copilot's reach is the explicit list in
-- migration 025, and widening it is a decision, not a side effect of adding a
-- view. Add it there deliberately if the operator wants to ask about withdrawals.
