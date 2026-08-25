-- =============================================================================
-- Data Effi - 047 - What a real Dropi export says (Guatemala, 2026-08-24).
--
-- Two files from the operator's Dropi account, 658 orders and 712 wallet
-- entries. Everything here exists because those files needed it.
--
-- 1. STATUSES. Dropi's `ESTATUS` column had eight distinct values. Five were
--    already known (ENTREGADO, DEVOLUCION, INCIDENCIA EN RUTA, EN RUTA,
--    CANCELADO); three fell to `created` with a warning:
--        RECOLECTADO                    -> picked_up   (the carrier has it)
--        GUIA_GENERADA                  -> created     (label printed, not moving)
--        PREPARADO PARA TRANSPORTADORA  -> confirmed   (packed, waiting for pickup)
--    `guia_generada` keeps its underscore: core.normalize_text only strips
--    accents and case, and the alias must match what the file literally says.
--
-- 2. THE WALLET IS A LEDGER, NOT A P&L. Dropi settles NET: the wallet shows
--    "ENTRADA POR GANANCIA" (the profit of a delivered order, after product,
--    freight and commission) and "SALIDA DE COBRO DE DEVOLUCION" (the return
--    freight charged). Both amounts are ALREADY inside the order file - VALOR
--    DE COMPRA, TOTAL EN PRECIOS DE PROVEEDOR, PRECIO FLETE, COSTO DEVOLUCION
--    FLETE - which is where the guide's economics come from. Booking the wallet
--    entry as revenue or freight would count the same money twice (the reason
--    migration 008 gave withdrawals the `transfer` category). So the wallet's
--    two settlement entries get their own `transfer` types: they link money to
--    the guide, they are visible in the order's timeline, and every KPI
--    ignores them. Withdrawals map to the existing `withdrawal`; a refund by
--    guarantee ("DEVOLUCION DE DINERO POR GARANTIA") is money that is NOT in
--    the order file, so it stays an `adjustment_in`.
--
-- Depends on: 046. Idempotent.
-- =============================================================================

INSERT INTO core.status_alias (platform_code, alias_norm, status_code) VALUES
    ('dropi', 'recolectado',                    'picked_up'),
    ('dropi', 'guia_generada',                  'created'),
    ('dropi', 'guia generada',                  'created'),
    ('dropi', 'preparado para transportadora',  'confirmed'),
    ('dropi', 'en ruta',                        'in_transit'),
    ('dropi', 'entregado',                      'delivered'),
    ('dropi', 'devolucion',                     'returning'),
    ('dropi', 'incidencia en ruta',             'delivery_issue'),
    ('dropi', 'cancelado',                      'cancelled')
ON CONFLICT (platform_code, alias_norm) DO NOTHING;

INSERT INTO core.movement_type (code, label, sign, category) VALUES
    ('settlement_in',  'Liquidación a billetera (ganancia de la orden)',  1, 'transfer'),
    ('settlement_out', 'Cargo en billetera (devolución u orden nueva)',  -1, 'transfer')
ON CONFLICT (code) DO NOTHING;

COMMENT ON TABLE core.movement_type IS
    'Tipos de movimiento de dinero. category = transfer significa que el dinero
     ya se contó en otra parte (retiros, liquidaciones netas de Dropi): se guarda
     para el rastro y ningún KPI lo suma.';
