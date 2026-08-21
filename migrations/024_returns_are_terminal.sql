-- =============================================================================
-- Data Effi - 024 - The delivery rate was 100% by construction.
--
-- `mart.v_global_summary` computes delivered / (delivered + returned). Effi's
-- "Devolución a origen" mapped to the `returning` status, and `returning`
-- carried is_returned = false. So `returned` was zero for every tenant, in
-- every country, always - and a divisor that equals the numerator produces
-- 100,00% no matter what the operation actually did.
--
-- It is not a rounding artefact. On the operator's real data the screen showed:
--
--     entrega global        100,00%      real 96,85% de las resueltas
--     SERVIENTREGA          100,00%      real 96,9% (448 entregadas, 14 devueltas)
--     devolución              0,00%      real  3,15%
--
-- while `mart.v_dropshipping_margin`, which divides by all shipments instead,
-- reported 44,89% for the same product on the same page. Two contradictory
-- delivery rates on one screen.
--
-- WHY `returning` BECOMES TERMINAL
-- All 23 returning guides in the real export have `settled_return_at` set: the
-- carrier already charged the return freight and closed the settlement. Nothing
-- further will happen to them. A guide on its way back is a lost sale, not an
-- order still in play, and counting it as "in transit" also inflated the
-- capital-in-street figure with money that is not coming back.
--
-- The 939 guides that are genuinely open are unaffected: they sit in created,
-- in_transit, in_office, out_for_delivery and delivery_issue, none of which
-- this migration touches.
--
-- Depends on: 023. Idempotent.
-- =============================================================================

UPDATE core.status_canon
   SET is_returned = true,
       is_terminal = true
 WHERE code = 'returning';

COMMENT ON TABLE core.status_canon IS
    'Escalera canónica de estados. is_terminal marca que la guía ya no se mueve;
     is_delivered / is_returned marcan CÓMO terminó. Una devolución cuenta como
     terminal desde la 024: el transportador ya cobró el flete de vuelta y la
     venta está perdida.';
