-- =============================================================================
-- Data Effi - 048 - Plans: a free month, then Master / Master Pro / Master
--                   Elite, or a custom deal with an advisor.
--
-- WHAT THE OPERATOR DECIDED. Registering creates an organisation with ONE
-- company on a 30-day free trial. After that, three fixed plans - Master
-- (1 company, USD 29), Master Pro (3 companies, USD 59), Master Elite
-- (6 companies, USD 99) - and "A la medida", negotiated with an advisor.
-- Billing is manual for now: the customer picks a plan, the subscription
-- goes to `pending`, and an advisor activates it (scripts/activate_plan.py).
-- When the free month ends with no active plan, the API answers 402 to every
-- data endpoint and the web sends the person to the plans screen.
--
-- TWO TABLES. `core.plan` is the catalogue (reference data, no tenant).
-- `core.org_subscription` is one row per organisation: what it is on, what it
-- asked for, and when the clock runs out. It hangs off core.org, not
-- core.tenant, because the plan limits how many companies an org may have.
--
-- Registration was "one owner per deployment" until now (auth.py refused a
-- second account). With plans it is open: every registration is its own
-- organisation; the API keeps slugs and e-mails unique.
--
-- Depends on: 047. Idempotent.
-- =============================================================================

CREATE TABLE IF NOT EXISTS core.plan (
    code        text PRIMARY KEY,
    name        text NOT NULL,
    price_usd   numeric(8, 2),                 -- NULL = negotiated
    max_tenants smallint,                      -- NULL = no limit (custom)
    is_custom   boolean NOT NULL DEFAULT false,
    sort_order  smallint NOT NULL,
    is_active   boolean NOT NULL DEFAULT true
);

INSERT INTO core.plan (code, name, price_usd, max_tenants, is_custom, sort_order) VALUES
    ('master',       'Master',       29,   1,    false, 1),
    ('master_pro',   'Master Pro',   59,   3,    false, 2),
    ('master_elite', 'Master Elite', 99,   6,    false, 3),
    ('custom',       'A la medida',  NULL, NULL, true,  4)
ON CONFLICT (code) DO UPDATE
    SET name = EXCLUDED.name, price_usd = EXCLUDED.price_usd,
        max_tenants = EXCLUDED.max_tenants, is_custom = EXCLUDED.is_custom,
        sort_order = EXCLUDED.sort_order;

COMMENT ON TABLE core.plan IS
    'Catálogo de planes. max_tenants = cuántas empresas puede tener la
     organización; NULL = sin límite (a la medida).';

CREATE TABLE IF NOT EXISTS core.org_subscription (
    org_id              uuid PRIMARY KEY REFERENCES core.org(id) ON DELETE CASCADE,
    status              text NOT NULL DEFAULT 'trial'
                        CHECK (status IN ('trial', 'pending', 'active', 'expired')),
    plan_code           text REFERENCES core.plan(code),
    requested_plan_code text REFERENCES core.plan(code),
    requested_at        timestamptz,
    trial_ends_at       timestamptz NOT NULL,
    current_period_end  timestamptz,
    activated_at        timestamptz,
    activated_by        uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    notes               text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE core.org_subscription IS
    'Una fila por organización. trial = mes gratis (1 empresa); pending = eligió
     plan y espera activación de un asesor; active = plan vigente hasta
     current_period_end (NULL = sin vencimiento); expired = cerrada por un asesor.
     La API bloquea (402) cuando el mes gratis venció sin plan activo o el
     período activo terminó.';

-- Every organisation that exists today gets its free month from the day it
-- was created, so nobody is locked out by the migration itself.
INSERT INTO core.org_subscription (org_id, status, trial_ends_at)
SELECT o.id, 'trial', o.created_at + interval '30 days'
FROM core.org o
WHERE NOT EXISTS (SELECT 1 FROM core.org_subscription s WHERE s.org_id = o.id);

-- Like core.org and core.membership: read before any tenant is known, so it
-- stays outside row-level security (header of 032).
GRANT SELECT, INSERT, UPDATE ON core.plan, core.org_subscription TO norte_app;
