-- =============================================================================
-- Data Effi - 046 - Which side of the business each partner is on.
--
-- The operator's org has two kinds of people inside one company: the ones who
-- sell (ecommerce) and the ones who supply (proveeduría). The admin tells us
-- which is which, per person and per company - the same person may sell in
-- one company and supply in another - so the field lives on core.membership,
-- next to the country scope and the share, and not on core.app_user, which is
-- one row per login regardless of company.
--
-- Nullable on purpose: every membership that exists today predates the
-- question, and "no dijo" is the honest value until the admin answers it.
-- `share_pct` (the partner's participation) is not touched.
--
-- Country assignment needs no new column: `core.membership.country_scope`
-- (migration 032) already holds the list; the screen simply never let the
-- admin pick more than one at a time.
--
-- Depends on: 045. Idempotent.
-- =============================================================================

ALTER TABLE core.membership
    ADD COLUMN IF NOT EXISTS business_model text
        CHECK (business_model IS NULL OR business_model IN ('ecommerce', 'proveeduria'));

COMMENT ON COLUMN core.membership.business_model IS
    'De qué lado del negocio está esta persona en esta sociedad: ecommerce
     (vende) o proveeduria (surte). NULL = el administrador aún no lo indicó.
     Informativo: no cambia lo que la persona puede ver.';

-- An invitation to someone without an account carries the grant until they
-- redeem it (country_scope and share_pct already travel this way).
ALTER TABLE core.invitation
    ADD COLUMN IF NOT EXISTS business_model text
        CHECK (business_model IS NULL OR business_model IN ('ecommerce', 'proveeduria'));
