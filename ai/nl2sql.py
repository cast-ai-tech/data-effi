"""Natural language to SQL, with the guard rails that make it safe to expose.

FOUR INDEPENDENT LAYERS, in order of how much I trust them:

1. THE DATABASE ROLE. `norte_readonly` can SELECT from `mart` and nothing else.
   No INSERT/UPDATE/DELETE grant exists to abuse, statement_timeout is 5s, and
   `search_path` is just `mart`. Even a perfect prompt injection reaches only
   aggregates this tenant is already allowed to see.
2. THE TENANT GUC. Every mart view filters by `core.current_tenant_id()`. A query
   that "forgets" the tenant filter returns this tenant's rows anyway - and with
   no GUC set, zero rows.
3. THE VALIDATOR IN THIS FILE. Parses the generated SQL with sqlglot and rejects
   anything that is not exactly one SELECT over allow-listed views.
4. THE PROMPT. Least trusted. It is a hint to the model, not a control.

A model is not a security boundary. Layers 1-3 assume layer 4 has been defeated.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

MAX_ROWS = 500
STATEMENT_TIMEOUT_MS = 5_000

# The only relations a generated query may touch. Business views only: nothing
# from core, nothing from raw, nothing from stg, no catalogs.
#
# THE OPERATOR ASKED FOR "ANY DATA IN THE PLATFORM". This list is the answer to
# that: every business aggregate Norte computes is reachable. What is NOT on it,
# and never will be:
#
#   * mart.v_source_row_archive - the original file rows. Customer name, id,
#     address and phone are stored as SHA-256 there, but a hash is still a
#     row-level record of a real person. "Any data" means every aggregate, not
#     every customer.
#   * mart.v_ai_memory - what the copilot remembers. A generated query that can
#     read the memory is a generated query that can be steered by whatever text
#     was written into it.
#   * anything in core, raw or stg.
#
# Adding a view here is a deliberate act. It is the ONLY part of this file that
# should ever change to widen reach - the checks below are not negotiable.
ALLOWED_VIEWS: frozenset[str] = frozenset(
    {
        "v_daily_contribution",
        "v_carrier_effectiveness",
        "v_geo_performance",
        "v_product_performance",
        "v_cohort_maturation",
        "v_aging",
        "v_cs_confirmation",
        "v_cpa_roas",
        "v_global_summary",
        "v_connection_health",
        "v_available_platforms",
        "v_country_dashboard_layout",
        "v_batch_history",
        "v_alert_signals",
        # Migration 008: the real-world Effi metrics
        "v_problem_rate",
        "v_cash_cycle",
        # Migration 009: the dropshipping chain, fulfilment, office queue, freight
        "v_dropshipping_margin",
        "v_fulfillment_sla",
        "v_office_rescue",
        "v_freight_analysis",
        "v_product_catalogue",
    }
)

# Views that must never be reachable, whatever else changes. Asserted by the
# test battery so a careless addition to ALLOWED_VIEWS above fails loudly.
NEVER_ALLOWED_VIEWS: frozenset[str] = frozenset(
    {
        "v_source_row_archive",     # row-level customer data, hashed or not
        "v_ai_memory",              # prompt-injectable text the model already trusts
    }
)

# Functions that read files, run commands, sleep, or reach the network. None of
# these are reachable by the read-only role, but rejecting them here means an
# attempt shows up in the logs as a rejection rather than as a permission error.
FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {
        "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
        "pg_sleep", "pg_sleep_for", "pg_sleep_until",
        "lo_import", "lo_export", "dblink", "dblink_exec",
        "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
        "set_config", "current_setting", "pg_read_server_files",
        "query_to_xml", "xmlelement", "copy",
        "pg_client_encoding", "pg_backend_pid",
        "convert_from", "decode", "encode", "chr",
        "version", "current_user", "session_user", "current_database",
        "inet_client_addr", "inet_server_addr", "pg_stat_activity",
        "has_table_privilege", "pg_get_functiondef",
    }
)

# PostgreSQL allows these with no parentheses, so they never look like a function
# call to a parser. They leak who and what the connection is.
FORBIDDEN_BARE_KEYWORDS: frozenset[str] = frozenset(
    {
        "current_user", "session_user", "current_role", "current_catalog",
        "current_database", "current_schema", "system_user",
    }
)

# Statement types that must never appear, even nested.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Grant, exp.Command, exp.Transaction, exp.Commit,
    exp.Rollback, exp.Use, exp.Set, exp.Copy, exp.Merge,
)


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    sql: str | None = None
    reason: str | None = None
    tables: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return not self.ok


def _reject(reason: str) -> ValidationResult:
    logger.warning("nl2sql rejected a query: %s", reason)
    return ValidationResult(ok=False, reason=reason)


def validate_sql(raw_sql: str) -> ValidationResult:
    """Parse and vet a generated statement. Returns the safe SQL or a reason."""
    if not raw_sql or not raw_sql.strip():
        return _reject("La consulta está vacía")

    sql_text = _strip_markdown_fence(raw_sql).strip().rstrip(";").strip()

    if not sql_text:
        return _reject("La consulta está vacía")

    # A semicolon that survives the single trailing strip means stacked statements.
    if ";" in sql_text:
        return _reject("Solo se permite una sentencia; se encontró más de una")

    try:
        statements = sqlglot.parse(sql_text, read="postgres")
    except Exception as exc:
        return _reject(f"No se pudo interpretar la consulta ({type(exc).__name__})")

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return _reject(f"Se esperaba exactamente una sentencia, se encontraron {len(statements)}")

    statement = statements[0]

    if not isinstance(statement, exp.Select):
        return _reject(
            f"Solo se permiten consultas SELECT; se recibió {type(statement).__name__.upper()}"
        )

    for node_type in FORBIDDEN_NODES:
        if list(statement.find_all(node_type)):
            return _reject(f"La consulta contiene una operación no permitida ({node_type.__name__})")

    # SELECT ... INTO creates a table.
    if statement.args.get("into"):
        return _reject("SELECT ... INTO no está permitido")
    # Locking clauses can block other sessions.
    if statement.args.get("locks"):
        return _reject("Las cláusulas de bloqueo no están permitidas")

    cte_names = {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
    }

    referenced: list[str] = []
    for table in statement.find_all(exp.Table):
        name = (table.name or "").lower()
        schema = (table.db or "").lower()

        if name in cte_names and not schema:
            continue      # a reference to a CTE defined in this same query

        if schema and schema != "mart":
            return _reject(
                f"Solo se puede consultar el esquema 'mart'; se pidió '{schema}.{name}'"
            )
        if name not in ALLOWED_VIEWS:
            return _reject(
                f"La vista '{name}' no está permitida. "
                f"Disponibles: {', '.join(sorted(ALLOWED_VIEWS))}"
            )
        referenced.append(f"mart.{name}")

    if not referenced:
        return _reject("La consulta no lee ninguna vista permitida")

    for func in statement.find_all(exp.Anonymous):
        func_name = (func.this or "").lower() if isinstance(func.this, str) else ""
        if func_name in FORBIDDEN_FUNCTIONS:
            return _reject(f"La función '{func_name}' no está permitida")

    # Catch function names sqlglot models as dedicated nodes rather than Anonymous.
    lowered = sql_text.lower()
    for forbidden in FORBIDDEN_FUNCTIONS:
        if re.search(rf"\b{re.escape(forbidden)}\s*\(", lowered):
            return _reject(f"La función '{forbidden}' no está permitida")

    # PostgreSQL niladic keywords: `current_user` is valid SQL with no parentheses,
    # so the pattern above never sees it. None of these are columns in mart, so a
    # bare-word match cannot be a false positive.
    for keyword in FORBIDDEN_BARE_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", lowered):
            return _reject(f"'{keyword}' no está permitido en una consulta generada")

    safe_sql = _enforce_limit(statement)
    return ValidationResult(ok=True, sql=safe_sql, tables=sorted(set(referenced)))


def _strip_markdown_fence(text: str) -> str:
    """Models like to wrap SQL in ```sql fences. Take what is inside."""
    fenced = re.search(r"```(?:sql)?\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    return fenced.group(1) if fenced else text


def _enforce_limit(statement: exp.Select) -> str:
    """Guarantee a LIMIT no larger than MAX_ROWS."""
    limit = statement.args.get("limit")
    if limit is None:
        statement = statement.limit(MAX_ROWS)
    else:
        try:
            requested = int(limit.expression.this)
            if requested > MAX_ROWS:
                statement.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROWS)))
        except (AttributeError, TypeError, ValueError):
            statement.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROWS)))

    return statement.sql(dialect="postgres", pretty=True)


# =============================================================================
# The catalog handed to the model
# =============================================================================

VIEW_CATALOG = """
mart.v_daily_contribution(country_code, store_name, day, shipments, delivered, returned,
    in_transit, dead, declared_value, revenue, freight, cogs, fees, ad_spend, contribution,
    contribution_margin_pct, delivery_rate_terminal_pct, delivery_rate_dispatched_pct,
    currency_code, ad_spend_missing)
    -- One row per country+store+cohort day. `day` is the date the guide was created.

mart.v_carrier_effectiveness(country_code, carrier_name, shipments, delivered, returned,
    in_transit, delivery_rate_pct, return_rate_pct, avg_days_to_deliver, p90_days_to_deliver,
    freight_total, avg_freight_per_shipment, revenue, contribution, currency_code)

mart.v_geo_performance(country_code, level1_name, city_name, shipments, delivered, returned,
    in_transit, delivery_rate_pct, revenue, contribution, avg_days_to_deliver,
    traffic_light, currency_code)
    -- traffic_light is one of: 'verde', 'amarillo', 'rojo', 'sin_datos'.

mart.v_product_performance(country_code, product_name, sku, supplier_name, shipments, units,
    delivered, returned, delivery_rate_pct, revenue, cogs, freight, contribution,
    contribution_per_shipment, margin_pct, currency_code)

mart.v_cohort_maturation(country_code, cohort_date, cohort_size, days_since, delivered_by_day,
    delivery_rate_pct, is_observable, is_mature, maturation_days)

mart.v_aging(country_code, aging_bucket, bucket_order, shipments, value_at_risk,
    avg_days_open, currency_code)
    -- aging_bucket is one of: '0-3', '4-7', '8-12', '13-20', '21+'.

mart.v_cs_confirmation(country_code, day, interactions, confirmed, rejected, no_answer,
    pending, confirmation_rate_pct, avg_attempts)

mart.v_cpa_roas(country_code, day, ad_spend, impressions, clicks, shipments, delivered,
    revenue, cpa_dispatched, cpa_delivered, roas, currency_code)
    -- Empty when there is no ad platform connected.

mart.v_global_summary(country_code, country_name, currency_code, shipments, delivered,
    returned, in_transit, delivery_rate_pct, revenue, ad_spend, contribution,
    fx_rate_to_usd, contribution_usd, fx_missing, last_shipment_date)

mart.v_connection_health(connection_name, country_code, platform_name, tier, status,
    last_sync_at, hours_since_sync, health)

mart.v_batch_history(source_name, kind, status, rows_total, rows_inserted, rows_updated,
    rows_failed, discrepancy_count, started_at, country_code)

mart.v_problem_rate(country_code, carrier_name, shipments, novedad, en_oficina, devolucion,
    con_problema, problem_rate_pct, value_in_office, currency_code)
    -- Novedad + en oficina + devolución as one number, por transportadora. Las tres se
    -- persiguen igual, aunque el reporte las separe. value_in_office es plata esperando
    -- que alguien pase a recogerla.

mart.v_cash_cycle(country_code, settled, delivered_unsettled, avg_days_to_cash,
    p50_days_to_cash, p90_days_to_cash, cash_in_transit, currency_code)
    -- Una fila por país. Entregado no es cobrado: cash_in_transit es lo entregado que la
    -- transportadora ya recaudó y todavía no te consigna.

mart.v_dropshipping_margin(country_code, product_name, sku, supplier_name, shipments,
    delivered, units, revenue, supplier_cost, freight, gross_margin, gross_margin_pct,
    net_contribution, contribution_per_shipment, cost_of_undelivered,
    breakeven_delivery_pct, delivery_rate_pct, catalogue_cost, catalogue_price,
    catalogue_reviewed, observed_unit_cost, currency_code)
    -- La cadena completa por producto: lo que cobraste, lo que le pagaste al proveedor y
    -- lo que queda. El flete y la mercancía se pagan en TODO lo despachado, no solo en lo
    -- entregado. breakeven_delivery_pct es el % de entrega por debajo del cual el producto
    -- pierde plata: compáralo contra delivery_rate_pct.

mart.v_fulfillment_sla(country_code, carrier_name, service_level, shipments, delivered,
    avg_prep_days, p50_prep_days, p90_prep_days, avg_transit_days, p90_transit_days,
    avg_total_days, prep_share_pct, on_time_count, measurable_count, on_time_pct)
    -- Parte el reloj de entrega en dos: alistamiento (prep_*, lo que tardas TÚ en
    -- despachar) y tránsito (transit_*, lo que tarda la transportadora). prep_share_pct
    -- es qué porcentaje de la espera es culpa propia.

mart.v_office_rescue(country_code, carrier_name, level1_name, city_name, shipments,
    value_waiting, avg_days_waiting, fresh_0_7, aging_8_14, urgent_15_21, probably_lost,
    value_still_recoverable, currency_code)
    -- Guías esperando en oficina de la transportadora: ni entregadas ni devueltas.
    -- value_still_recoverable es el valor de la banda de 8 a 21 días, que es la que
    -- todavía se rescata con una llamada. Pasados 21 días normalmente se devuelve sola.

mart.v_freight_analysis(country_code, carrier_name, service_level, shipments,
    avg_weight_kg, total_weight_kg, freight_total, avg_freight, freight_per_kg,
    avg_freight_base, avg_handling, avg_collection_fee, avg_discount_pct, discount_value,
    freight_share_of_value_pct, return_freight_total, currency_code)
    -- freight_per_kg es la cifra comparable entre transportadoras: una no es cara por
    -- llevar paquetes más pesados. freight_share_of_value_pct es el flete como porcentaje
    -- de lo que vale la guía.

mart.v_product_catalogue(product_name, sku, category, supplier_name, unit_cost, list_price,
    target_margin_pct, weight_kg, currency_code, is_active, reviewed_at, notes, shipments,
    delivered, last_shipment_date, observed_unit_cost, catalogue_status,
    catalogue_margin_pct)
    -- OJO: esta vista NO tiene country_code, el catálogo es del negocio entero.
    -- catalogue_status es uno de: 'sin_costo', 'sin_revisar', 'costo_desactualizado', 'ok'.
    -- 'costo_desactualizado' significa que el costo observado en las guías se separó más
    -- de 10% del costo del catálogo.
"""

SYSTEM_PROMPT = f"""Eres un analista de datos que traduce preguntas de negocio a SQL de PostgreSQL.

Trabajas para Data Effi, una plataforma de analítica de ecommerce contraentrega (COD) en LATAM.

REGLAS ABSOLUTAS:
- Devuelves ÚNICAMENTE una sentencia SELECT. Nada más: ni explicación, ni punto y coma final,
  ni varias sentencias.
- Solo puedes leer estas vistas del esquema `mart`. Cualquier otra tabla o esquema hace que
  la consulta se rechace.
- No uses funciones del sistema, ni current_setting, ni set_config, ni pg_*.
- Incluye siempre un LIMIT, máximo {MAX_ROWS}.
- No necesitas filtrar por tenant: las vistas ya lo hacen solas.

VOCABULARIO DEL NEGOCIO:
- "Guía" o "despacho" = una fila de envío. NO uses la palabra "venta": en contraentrega una
  venta no es dinero hasta que se entrega.
- "Efectividad" o "% de entrega" = delivery_rate_pct.
- "Contribución" = recaudo menos flete, producto, comisiones y pauta.
- "Devolución" = returned. Cuesta el flete de ida Y el de vuelta.
- "Maduración" = cuántos días tarda una cohorte en estabilizar su % de entrega.

CATÁLOGO DE VISTAS:
{VIEW_CATALOG}

Responde solo con el SQL.
"""

REJECTION_SUGGESTIONS = [
    "¿Cuál es mi contribución por día este mes?",
    "¿Qué transportadora tiene la peor efectividad de entrega?",
    "¿Qué producto me está quemando plata?",
    "¿Qué ciudades están en rojo con más de 25 guías?",
    "¿Cuántas guías llevan más de 13 días abiertas y cuánto valen?",
]
