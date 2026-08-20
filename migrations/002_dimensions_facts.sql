-- =============================================================================
-- Norte - 002 - Dimensions, canonical statuses, facts (shipments, movements,
--               ad spend, CS, FX) and the dashboard widget catalog.
-- Depends on: 001_core_schema.sql
-- Idempotent: safe to re-run.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Canonical shipment status.
--   sort_order drives the "status only moves forward" merge rule.
--   is_terminal freezes a shipment: once terminal, later files cannot regress it.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.status_canon (
    code        text PRIMARY KEY,
    label       text NOT NULL,
    sort_order  smallint NOT NULL UNIQUE,
    is_terminal boolean NOT NULL DEFAULT false,
    is_delivered boolean NOT NULL DEFAULT false,
    is_returned boolean NOT NULL DEFAULT false,
    -- Buckets used by dashboards: pipeline | delivered | returned | dead
    bucket      text NOT NULL CHECK (bucket IN ('pipeline', 'delivered', 'returned', 'dead'))
);

INSERT INTO core.status_canon (code, label, sort_order, is_terminal, is_delivered, is_returned, bucket) VALUES
    ('created',          'Generada',        10, false, false, false, 'pipeline'),
    ('confirmed',        'Confirmada',      20, false, false, false, 'pipeline'),
    ('picked_up',        'Recogida',        30, false, false, false, 'pipeline'),
    ('in_transit',       'En tránsito',     40, false, false, false, 'pipeline'),
    ('out_for_delivery', 'En reparto',      50, false, false, false, 'pipeline'),
    ('delivery_issue',   'Novedad',         55, false, false, false, 'pipeline'),
    ('delivered',        'Entregada',       60, true,  true,  false, 'delivered'),
    ('returning',        'En devolución',   70, false, false, false, 'returned'),
    ('returned',         'Devuelta',        80, true,  false, true,  'returned'),
    ('cancelled',        'Cancelada',       90, true,  false, false, 'dead'),
    ('lost',             'Extraviada',      95, true,  false, false, 'dead')
ON CONFLICT (code) DO NOTHING;

-- Raw status text (per platform) mapped to a canonical code.
CREATE TABLE IF NOT EXISTS core.status_alias (
    id            bigserial PRIMARY KEY,
    platform_code text NOT NULL REFERENCES core.platform(code) ON DELETE CASCADE,
    alias_norm    text NOT NULL,   -- already passed through core.normalize_text
    status_code   text NOT NULL REFERENCES core.status_canon(code),
    UNIQUE (platform_code, alias_norm)
);

INSERT INTO core.status_alias (platform_code, alias_norm, status_code) VALUES
    ('effi', 'generada',                 'created'),
    ('effi', 'guia generada',            'created'),
    ('effi', 'pendiente',                'created'),
    ('effi', 'confirmado',               'confirmed'),
    ('effi', 'confirmada',               'confirmed'),
    ('effi', 'recogido',                 'picked_up'),
    ('effi', 'recogida',                 'picked_up'),
    ('effi', 'en transito',              'in_transit'),
    ('effi', 'en ruta',                  'in_transit'),
    ('effi', 'en reparto',               'out_for_delivery'),
    ('effi', 'en distribucion',          'out_for_delivery'),
    ('effi', 'novedad',                  'delivery_issue'),
    ('effi', 'con novedad',              'delivery_issue'),
    ('effi', 'entregado',                'delivered'),
    ('effi', 'entregada',                'delivered'),
    ('effi', 'en devolucion',            'returning'),
    ('effi', 'devolucion en transito',   'returning'),
    ('effi', 'devuelto',                 'returned'),
    ('effi', 'devuelta',                 'returned'),
    ('effi', 'cancelado',                'cancelled'),
    ('effi', 'cancelada',                'cancelled'),
    ('effi', 'anulada',                  'cancelled'),
    ('effi', 'extraviado',               'lost'),
    ('effi', 'perdida',                  'lost'),
    ('dropi', 'pendiente',               'created'),
    ('dropi', 'guia generada',           'created'),
    ('dropi', 'preparando',              'confirmed'),
    ('dropi', 'en transito',             'in_transit'),
    ('dropi', 'en reparto',              'out_for_delivery'),
    ('dropi', 'novedad',                 'delivery_issue'),
    ('dropi', 'entregado',               'delivered'),
    ('dropi', 'devolucion',              'returning'),
    ('dropi', 'devuelto',                'returned'),
    ('dropi', 'cancelado',               'cancelled'),
    ('manual_xlsx', 'generada',          'created'),
    ('manual_xlsx', 'en transito',       'in_transit'),
    ('manual_xlsx', 'en reparto',        'out_for_delivery'),
    ('manual_xlsx', 'novedad',           'delivery_issue'),
    ('manual_xlsx', 'entregado',         'delivered'),
    ('manual_xlsx', 'entregada',         'delivered'),
    ('manual_xlsx', 'devuelto',          'returned'),
    ('manual_xlsx', 'devuelta',          'returned'),
    ('manual_xlsx', 'cancelado',         'cancelled')
ON CONFLICT (platform_code, alias_norm) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Money movement types. sign: +1 money in, -1 money out.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.movement_type (
    code     text PRIMARY KEY,
    label    text NOT NULL,
    sign     smallint NOT NULL CHECK (sign IN (-1, 1)),
    category text NOT NULL CHECK (category IN ('revenue', 'freight', 'cogs', 'fee', 'adjustment', 'other'))
);

INSERT INTO core.movement_type (code, label, sign, category) VALUES
    ('cod_collected',   'Recaudo contraentrega', 1,  'revenue'),
    ('freight_out',     'Flete de envío',       -1,  'freight'),
    ('freight_return',  'Flete de devolución',  -1,  'freight'),
    ('product_cost',    'Costo de producto',    -1,  'cogs'),
    ('platform_fee',    'Comisión plataforma',  -1,  'fee'),
    ('insurance',       'Seguro',               -1,  'fee'),
    ('adjustment_in',   'Ajuste a favor',        1,  'adjustment'),
    ('adjustment_out',  'Ajuste en contra',     -1,  'adjustment')
ON CONFLICT (code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Dimensions (tenant-scoped, resolved get-or-create during ingestion)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.carrier (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    country_code char(2) NOT NULL REFERENCES core.country(code),
    name         text NOT NULL,
    name_norm    text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, country_code, name_norm)
);

CREATE TABLE IF NOT EXISTS core.geo (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    country_code    char(2) NOT NULL REFERENCES core.country(code),
    level1_name     text,            -- departamento / estado / región
    level1_norm     text,
    city_name       text NOT NULL,
    city_normalized text NOT NULL,   -- core.normalize_text(city_name)
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, country_code, level1_norm, city_normalized)
);
CREATE INDEX IF NOT EXISTS ix_geo_city ON core.geo (tenant_id, country_code, city_normalized);

CREATE TABLE IF NOT EXISTS core.supplier (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    country_code char(2) REFERENCES core.country(code),
    name         text NOT NULL,
    name_norm    text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name_norm)
);

CREATE TABLE IF NOT EXISTS core.product (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    sku         text,
    name        text NOT NULL,
    name_norm   text NOT NULL,
    supplier_id uuid REFERENCES core.supplier(id) ON DELETE SET NULL,
    category    text,
    unit_cost   numeric(14, 2),
    currency_code char(3),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name_norm)
);

-- Every spelling a product arrives under maps back to one canonical product.
CREATE TABLE IF NOT EXISTS core.product_alias (
    id         bigserial PRIMARY KEY,
    tenant_id  uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    alias_norm text NOT NULL,
    product_id uuid NOT NULL REFERENCES core.product(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, alias_norm)
);

-- -----------------------------------------------------------------------------
-- FACT: shipment (one delivery guide)
-- Natural key: (connection_id, tracking_number) - the ON CONFLICT target.
-- PII rule: no phone, no document, no address line. Only customer_hash.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.shipment (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    connection_id       uuid NOT NULL REFERENCES core.connection(id) ON DELETE CASCADE,
    country_code        char(2) NOT NULL REFERENCES core.country(code),
    store_id            uuid REFERENCES core.store(id) ON DELETE SET NULL,

    tracking_number     text NOT NULL,
    external_order_id   text,

    -- SHA-256(salt || normalized phone/document). Never reversible, never PII.
    customer_hash       char(64),

    carrier_id          uuid REFERENCES core.carrier(id) ON DELETE SET NULL,
    geo_id              uuid REFERENCES core.geo(id) ON DELETE SET NULL,
    product_id          uuid REFERENCES core.product(id) ON DELETE SET NULL,
    quantity            integer NOT NULL DEFAULT 1,

    status_code         text NOT NULL REFERENCES core.status_canon(code),
    status_raw          text,

    created_date        date NOT NULL,        -- guide creation date (cohort date)
    dispatched_at       timestamptz,
    delivered_at        timestamptz,
    returned_at         timestamptz,
    last_status_at      timestamptz,

    currency_code       char(3) NOT NULL,
    declared_value      numeric(14, 2),       -- amount to collect on delivery
    cod_collected       numeric(14, 2),       -- amount actually collected
    freight_cost        numeric(14, 2),
    return_freight_cost numeric(14, 2),
    product_cost        numeric(14, 2),
    platform_fee        numeric(14, 2),

    first_batch_id      uuid REFERENCES raw.load_batch(id) ON DELETE SET NULL,
    last_batch_id       uuid REFERENCES raw.load_batch(id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    UNIQUE (connection_id, tracking_number)
);
CREATE INDEX IF NOT EXISTS ix_shipment_tenant_country_date
    ON core.shipment (tenant_id, country_code, created_date DESC);
CREATE INDEX IF NOT EXISTS ix_shipment_status   ON core.shipment (tenant_id, status_code);
CREATE INDEX IF NOT EXISTS ix_shipment_carrier  ON core.shipment (carrier_id);
CREATE INDEX IF NOT EXISTS ix_shipment_product  ON core.shipment (product_id);
CREATE INDEX IF NOT EXISTS ix_shipment_geo      ON core.shipment (geo_id);
CREATE INDEX IF NOT EXISTS ix_shipment_tracking ON core.shipment (tenant_id, tracking_number);

-- -----------------------------------------------------------------------------
-- FACT: movement (money in / out, may arrive before its shipment)
-- Orphans keep shipment_id NULL and are relinked later by relink_orphans().
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.movement (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    connection_id       uuid NOT NULL REFERENCES core.connection(id) ON DELETE CASCADE,
    country_code        char(2) NOT NULL REFERENCES core.country(code),
    shipment_id         uuid REFERENCES core.shipment(id) ON DELETE SET NULL,
    -- Kept even after linking: it is how an orphan finds its shipment later.
    tracking_number_raw text,
    movement_type_code  text NOT NULL REFERENCES core.movement_type(code),
    movement_date       date NOT NULL,
    amount              numeric(14, 2) NOT NULL,
    currency_code       char(3) NOT NULL,
    external_ref        text,
    description         text,
    -- SHA-256 of the identifying fields. Makes re-ingestion idempotent.
    dedupe_key          char(64) NOT NULL,
    batch_id            uuid REFERENCES raw.load_batch(id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (connection_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS ix_movement_shipment ON core.movement (shipment_id);
CREATE INDEX IF NOT EXISTS ix_movement_orphan
    ON core.movement (tenant_id, tracking_number_raw) WHERE shipment_id IS NULL;
CREATE INDEX IF NOT EXISTS ix_movement_date
    ON core.movement (tenant_id, country_code, movement_date DESC);

-- -----------------------------------------------------------------------------
-- FACT: ad spend (only present when an ads connection exists - drives CPA/ROAS
-- widget availability)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.ad_spend (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    connection_id uuid NOT NULL REFERENCES core.connection(id) ON DELETE CASCADE,
    country_code  char(2) NOT NULL REFERENCES core.country(code),
    store_id      uuid REFERENCES core.store(id) ON DELETE SET NULL,
    product_id    uuid REFERENCES core.product(id) ON DELETE SET NULL,
    spend_date    date NOT NULL,
    campaign_name text NOT NULL DEFAULT '',
    spend         numeric(14, 2) NOT NULL DEFAULT 0,
    impressions   bigint NOT NULL DEFAULT 0,
    clicks        bigint NOT NULL DEFAULT 0,
    conversions   integer NOT NULL DEFAULT 0,
    currency_code char(3) NOT NULL,
    dedupe_key    char(64) NOT NULL,
    batch_id      uuid REFERENCES raw.load_batch(id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (connection_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS ix_ad_spend_date
    ON core.ad_spend (tenant_id, country_code, spend_date DESC);

-- -----------------------------------------------------------------------------
-- FACT: customer-service confirmation attempts
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.cs_interaction (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    connection_id       uuid NOT NULL REFERENCES core.connection(id) ON DELETE CASCADE,
    country_code        char(2) NOT NULL REFERENCES core.country(code),
    shipment_id         uuid REFERENCES core.shipment(id) ON DELETE SET NULL,
    tracking_number_raw text,
    interaction_date    date NOT NULL,
    outcome             text NOT NULL CHECK (outcome IN
                        ('confirmed', 'rejected', 'no_answer', 'pending', 'rescheduled')),
    attempts            smallint NOT NULL DEFAULT 1,
    agent_label         text,
    dedupe_key          char(64) NOT NULL,
    batch_id            uuid REFERENCES raw.load_batch(id) ON DELETE SET NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (connection_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS ix_cs_interaction_date
    ON core.cs_interaction (tenant_id, country_code, interaction_date DESC);

-- -----------------------------------------------------------------------------
-- FX rates (worker refreshes daily; falls back to last known rate)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.fx_rate (
    rate_date      date NOT NULL,
    base_currency  char(3) NOT NULL,
    quote_currency char(3) NOT NULL,
    rate           numeric(18, 8) NOT NULL CHECK (rate > 0),
    source         text NOT NULL DEFAULT 'manual',
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (rate_date, base_currency, quote_currency)
);

-- -----------------------------------------------------------------------------
-- Widget catalog. The dashboard is COMPOSED from this + which data domains the
-- tenant actually has connected. The frontend never decides availability.
--   state resolution lives in mart.v_country_dashboard_layout (003).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.widget_catalog (
    widget_code        text PRIMARY KEY,
    tab                text NOT NULL CHECK (tab IN
                       ('finanzas', 'logistica', 'efectividad', 'servicio', 'global')),
    title              text NOT NULL,
    description        text NOT NULL,
    -- All domains must be present for the widget to be 'available'.
    required_domains   text[] NOT NULL,
    -- Optional domains: if missing the widget still renders, but 'degraded'.
    optional_domains   text[] NOT NULL DEFAULT ARRAY[]::text[],
    blocked_message    text NOT NULL,
    sort_order         smallint NOT NULL DEFAULT 100,
    is_active          boolean NOT NULL DEFAULT true
);

INSERT INTO core.widget_catalog
    (widget_code, tab, title, description, required_domains, optional_domains, blocked_message, sort_order) VALUES
    ('kpi_contribution', 'finanzas', 'Contribución diaria',
     'Ingreso recaudado menos fletes, producto y comisiones, día a día.',
     ARRAY['shipments'], ARRAY['movements','ads'],
     'Necesitas al menos una conexión de guías para ver contribución.', 10),

    ('waterfall_pnl', 'finanzas', 'Cascada de P&L',
     'De valor despachado a contribución final, mostrando cada fuga.',
     ARRAY['shipments','movements'], ARRAY['ads'],
     'Conecta una fuente de movimientos de dinero para desglosar la cascada.', 20),

    ('cpa_roas', 'finanzas', 'CPA y ROAS',
     'Costo por venta entregada y retorno sobre la pauta.',
     ARRAY['shipments','ads'], ARRAY[]::text[],
     'Bloqueado: falta conectar una plataforma de pauta (Meta, TikTok o Google Ads).', 30),

    ('carrier_table', 'logistica', 'Transportadoras',
     'Efectividad de entrega, días promedio y costo por transportadora.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Necesitas al menos una conexión de guías.', 10),

    ('aging_bars', 'logistica', 'Antigüedad en tránsito',
     'Guías abiertas por tramo de días. El tramo 13+ es la zona de pérdida.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Necesitas al menos una conexión de guías.', 20),

    ('cohort_curve', 'efectividad', 'Maduración de cohortes',
     'Cómo sube el % de entrega de cada cohorte con los días.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Necesitas al menos una conexión de guías.', 10),

    ('geo_traffic_light', 'efectividad', 'Semáforo geográfico',
     'Ciudades y departamentos por efectividad de entrega y volumen.',
     ARRAY['shipments'], ARRAY[]::text[],
     'Necesitas al menos una conexión de guías.', 20),

    ('margin_delivery_scatter', 'efectividad', 'Margen vs. entrega',
     'Cada producto ubicado por margen y por tasa de entrega.',
     ARRAY['shipments'], ARRAY['movements'],
     'Necesitas al menos una conexión de guías.', 30),

    ('product_table', 'efectividad', 'Productos',
     'Contribución, entrega y devolución por producto.',
     ARRAY['shipments'], ARRAY['movements'],
     'Necesitas al menos una conexión de guías.', 40),

    ('cs_confirmation', 'servicio', 'Confirmación de pedidos',
     'Qué porcentaje confirma el equipo de servicio y con cuántos intentos.',
     ARRAY['cs'], ARRAY['shipments'],
     'Bloqueado: falta conectar la fuente de confirmación de servicio al cliente.', 10),

    ('global_summary', 'global', 'Resumen multi-país',
     'Contribución y efectividad de todos los países en moneda común.',
     ARRAY['shipments'], ARRAY['ads','movements'],
     'Activa al menos un país con datos de guías.', 10)
ON CONFLICT (widget_code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- updated_at maintenance
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS tr_shipment_touch ON core.shipment;
CREATE TRIGGER tr_shipment_touch BEFORE UPDATE ON core.shipment
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

DROP TRIGGER IF EXISTS tr_movement_touch ON core.movement;
CREATE TRIGGER tr_movement_touch BEFORE UPDATE ON core.movement
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();
