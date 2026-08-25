-- =============================================================================
-- Data Effi - 050 - What kind of company is this?
--
-- The operator's words: "un check que defina si es Tienda o Proveedor",
-- because the same data reads differently for a dropshipping store (the
-- supplier ships, the store only collects), a store with its own stock (it
-- bought the goods and ships them), a mixed one, or a supplier (it ships
-- other people's orders and gets paid by them).
--
-- One column on the company, chosen when the company is created and editable
-- in Configuración. Existing companies get NULL - nobody guesses their model
-- for them; the settings screen asks. The per-member `business_model` of 046
-- stays what it was: the point of view of a PERSON inside the company.
--
-- Depends on: 049. Idempotent.
-- =============================================================================

ALTER TABLE core.tenant ADD COLUMN IF NOT EXISTS company_type text;

ALTER TABLE core.tenant DROP CONSTRAINT IF EXISTS tenant_company_type_check;
ALTER TABLE core.tenant ADD CONSTRAINT tenant_company_type_check
    CHECK (company_type IS NULL
           OR company_type IN ('dropshipping', 'own_stock', 'mixed', 'supplier'));

COMMENT ON COLUMN core.tenant.company_type IS
    'dropshipping = tienda que vende productos de proveedores; own_stock = tienda con
     mercancía propia; mixed = tienda mixta; supplier = proveedor que despacha a tiendas.
     NULL = todavía no definido.';

-- The login helper (032) now carries the type too, so the company picker and
-- the settings screen know it without a second query. Return type changes,
-- so drop + create, same grants as 032.
DROP FUNCTION IF EXISTS core.user_workspaces(uuid);
CREATE FUNCTION core.user_workspaces(p_user_id uuid)
RETURNS TABLE (
    tenant_id     uuid,
    tenant_slug   text,
    tenant_name   text,
    org_id        uuid,
    role          text,
    country_scope char(2)[],
    share_pct     numeric,
    countries     char(2)[],
    company_type  text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = core, pg_temp
AS $fn$
    SELECT
        t.id,
        t.slug,
        t.name,
        t.org_id,
        m.role,
        m.country_scope,
        m.share_pct,
        coalesce(
            (SELECT array_agg(wc.country_code ORDER BY wc.country_code)
             FROM core.workspace_country wc
             WHERE wc.tenant_id = t.id AND wc.is_active),
            ARRAY[]::char(2)[]
        ),
        t.company_type
    FROM core.membership m
    JOIN core.tenant t ON t.id = m.tenant_id
    WHERE m.user_id = p_user_id
      AND m.is_active
      AND t.is_active
    ORDER BY t.name;
$fn$;

COMMENT ON FUNCTION core.user_workspaces IS
    'The companies a person may open, with role, country scope, stake and company type.';

REVOKE ALL ON FUNCTION core.user_workspaces(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.user_workspaces(uuid) TO norte_app;
