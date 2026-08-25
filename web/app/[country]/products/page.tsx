"use client";

/**
 * The product catalogue.
 *
 * Products are not created here: they appear on their own the first time a
 * report mentions them. What the operator adds is the commercial truth - the
 * real unit cost, the list price, the weight - and that is the only reason this
 * screen exists. Every margin figure in the whole product is a guess until this
 * table is filled in, so the screen is organised around what is still missing
 * rather than around what is already there.
 *
 * It lives under `/[country]/` with the rest of the data screens, but the
 * catalogue itself is still tenant-wide: `core.product` has no country column
 * and `GET /products` takes no country filter, so the same product row is
 * reachable from every country's menu. Each row is therefore formatted with
 * ITS OWN currency, and the note under the table says so - pretending the list
 * was filtered would be worse than admitting it is not.
 */

import Link from "next/link";
import { useMemo, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { CountryMissing } from "@/components/browse";
import { VERDICT_META, decisionsPath } from "@/components/DecisionStrip";
import {
  Button,
  Card,
  Chip,
  EmptyState,
  ErrorState,
  SkeletonRows,
  cx,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { FormatCountry } from "@/lib/format";
import {
  FALLBACK_COUNTRY,
  countryFlag,
  formatDate,
  formatMoney,
  formatNumber,
  formatPercent,
} from "@/lib/format";
import { useRouteCountry } from "@/lib/country";
import { useApi } from "@/lib/hooks";
import type {
  CatalogueStatus,
  Country,
  Decision,
  DecisionsResponse,
  ProductCatalogueRow,
  ProductCostEntry,
  ProductDetail,
  ProductWrite,
  User,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Status vocabulary
//
// `catalogue_status` drives the whole screen: the chip, the filters and the
// reason to open the panel. Each label says what is WRONG, never what the
// column is called.
// ---------------------------------------------------------------------------

const STATUS_META: Record<
  CatalogueStatus,
  {
    label: string;
    tone: "negative" | "warning" | "positive";
    explanation: string;
  }
> = {
  sin_costo: {
    label: "Falta el costo",
    tone: "negative",
    explanation:
      "Sin costo unitario no hay margen: este producto entra en los reportes como si no costara nada.",
  },
  sin_revisar: {
    label: "Sin confirmar",
    tone: "warning",
    explanation:
      "El costo lo dedujimos de los reportes. Nadie lo ha confirmado todavía.",
  },
  costo_desactualizado: {
    label: "El costo cambió",
    tone: "warning",
    explanation:
      "Lo que dicen las guías se separó más de un 10% del costo guardado. O el proveedor subió el precio, o el catálogo quedó viejo.",
  },
  ok: {
    label: "Al día",
    tone: "positive",
    explanation: "El costo está confirmado y coincide con lo que dicen las guías.",
  },
};

const STATUS_ORDER: CatalogueStatus[] = [
  "sin_costo",
  "sin_revisar",
  "costo_desactualizado",
  "ok",
];

/** Where each cost in the history came from. "manual" is the only confirmed one. */
const COST_SOURCE_LABELS: Record<ProductCostEntry["source"], string> = {
  manual: "lo confirmó una persona",
  import: "lo trajo un archivo",
  observed: "se dedujo de las guías",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** `delivered / shipments`, computed here because the view ships both counts. */
function deliveryRate(row: ProductCatalogueRow): number | null {
  if (row.shipments <= 0) return null;
  return (row.delivered / row.shipments) * 100;
}

/** Above 75% is healthy, below 60% is bleeding. Same thresholds as carriers. */
function deliveryTone(pct: number | null): string {
  if (pct === null) return "text-ink-dim";
  if (pct >= 75) return "text-positive";
  if (pct >= 60) return "text-warning";
  return "text-negative";
}

/** Empty input means "no value", not zero. Zero is a real cost; blank is not. */
function parseNumber(raw: string): number | null {
  const trimmed = raw.trim().replace(",", ".");
  if (trimmed === "") return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

/** A text field on POST: a blank box is simply not sent. */
function textForPost(raw: string): string | undefined {
  const trimmed = raw.trim();
  return trimmed === "" ? undefined : trimmed;
}

/**
 * Range and format check, so a typo comes back as a sentence instead of a 422.
 * Blank is always valid - it just means the operator has not filled it in.
 */
function numericIssue(raw: string, label: string, max?: number): string | null {
  if (raw.trim() === "") return null;
  const value = parseNumber(raw);
  if (value === null) return `${label}: escribe solo números.`;
  if (value < 0) return `${label}: no puede ser negativo.`;
  if (max !== undefined && value > max) return `${label}: no puede pasar de ${max}.`;
  return null;
}

/**
 * Nothing to show.
 *
 * A field can come back as `null` or as `""` - a product touched before the
 * API settled on null semantics carries the empty string. Both mean the same
 * thing to the operator, so both get the dash instead of a blank cell.
 */
function orDash(value: string | null): string {
  return value === null || value.trim() === "" ? "—" : value;
}

/** For editing: a number back into a plain input value. */
function numberInput(value: number | null): string {
  return value === null ? "" : String(value);
}

function matchesSearch(row: ProductCatalogueRow, needle: string): boolean {
  if (needle === "") return true;
  const haystack = [row.product_name, row.sku, row.supplier_name, row.category]
    .filter((part): part is string => Boolean(part))
    .join(" ")
    .toLowerCase();
  return haystack.includes(needle);
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ProductsPage() {
  return (
    <AppShell>
      <ProductsScreen />
    </AppShell>
  );
}

/** A message across the top of the screen. Warning is guidance, negative is a failure. */
type Notice = { tone: "warning" | "negative"; message: string } | null;

/** What the panel is editing: an existing row, a blank product, or nothing. */
type PanelTarget = { mode: "edit"; row: ProductCatalogueRow } | { mode: "new" } | null;

function ProductsScreen() {
  const {
    code: countryCode,
    country: routeCountry,
    countries: activeCountries,
    missing,
  } = useRouteCountry();

  const { data, error, loading, reload } = useApi<ProductCatalogueRow[]>("/products");
  const { data: user } = useApi<User>("/auth/me");

  // Seguir / cortar / vigilar, per product, for the country in the address
  // bar. The catalogue is tenant-wide but a verdict is not: the same product
  // can pay in Colombia and bleed in Ecuador, so the chip says which one.
  const { data: decisions } = useApi<DecisionsResponse>(
    countryCode && !missing ? decisionsPath(countryCode, "products") : null,
    [countryCode],
  );
  const verdictByProduct = useMemo(() => {
    const map = new Map<string, Decision>();
    for (const item of decisions?.items ?? []) {
      if (typeof item.numbers.product_id === "string") map.set(item.numbers.product_id, item);
    }
    return map;
  }, [decisions]);
  const cutCount = useMemo(
    () => (decisions?.items ?? []).filter((item) => item.verdict === "cut").length,
    [decisions],
  );

  const [search, setSearch] = useState("");
  const [statuses, setStatuses] = useState<Set<CatalogueStatus>>(new Set());
  const [panel, setPanel] = useState<PanelTarget>(null);
  // A duplicate name is guidance, not a failure, so the banner carries a tone.
  const [notice, setNotice] = useState<Notice>(null);

  const canEdit = user ? user.role === "owner" || user.role === "analyst" : false;

  const rows = useMemo<ProductCatalogueRow[]>(() => data ?? [], [data]);

  /**
   * The catalogue is tenant-wide, so a product has a currency but not a
   * country. Formatting rules are looked up by currency, falling back to the
   * first active country - never to a hardcoded "$".
   */
  const formatFor = useMemo(() => {
    const byCurrency = new Map<string, Country>();
    for (const country of activeCountries) {
      if (!byCurrency.has(country.currency_code)) {
        byCurrency.set(country.currency_code, country);
      }
    }
    return (currencyCode: string | null): FormatCountry => {
      if (currencyCode) {
        const match = byCurrency.get(currencyCode);
        if (match) return match;
      }
      // The country in the address bar is the best guess for a product nobody
      // gave a currency to - it is the one the reader is looking at.
      return routeCountry ?? activeCountries[0] ?? FALLBACK_COUNTRY;
    };
  }, [activeCountries, routeCountry]);

  const counts = useMemo(() => {
    const tally: Record<CatalogueStatus, number> = {
      sin_costo: 0,
      sin_revisar: 0,
      costo_desactualizado: 0,
      ok: 0,
    };
    for (const row of rows) tally[row.catalogue_status] += 1;
    return tally;
  }, [rows]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return rows
      .filter((row) => statuses.size === 0 || statuses.has(row.catalogue_status))
      .filter((row) => matchesSearch(row, needle))
      .sort((a, b) => {
        // What is broken first, then by how much of the operation it touches.
        const rank =
          STATUS_ORDER.indexOf(a.catalogue_status) -
          STATUS_ORDER.indexOf(b.catalogue_status);
        if (rank !== 0) return rank;
        return b.shipments - a.shipments;
      });
  }, [rows, search, statuses]);

  function toggleStatus(status: CatalogueStatus) {
    setStatuses((current) => {
      const next = new Set(current);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      return next;
    });
  }

  const missingCost = counts.sin_costo + counts.sin_revisar + counts.costo_desactualizado;

  if (missing) {
    return (
      <CountryMissing code={countryCode} countries={activeCountries} section="products" />
    );
  }

  return (
    <>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2.5 text-[22px] font-bold tracking-tight">
            <span className="text-[24px] leading-none">{countryFlag(countryCode)}</span>
            Productos
          </h1>
          <p className="mt-1 max-w-2xl text-[12px] leading-relaxed text-ink-dim">
            El costo que escribes acá es el que decide si un producto gana o pierde plata.
            Mientras esté vacío o desactualizado, todos los márgenes del tablero son una
            aproximación.
          </p>
        </div>

        {canEdit && (
          <Button size="sm" onClick={() => setPanel({ mode: "new" })}>
            Nuevo producto
          </Button>
        )}
      </header>

      {notice && (
        <p
          className={cx(
            "mb-4 rounded-[8px] border px-3 py-2 text-[12px] leading-relaxed",
            notice.tone === "warning"
              ? "border-warning/30 bg-warning/[0.08] text-warning"
              : "border-negative/30 bg-negative/[0.08] text-negative",
          )}
        >
          {notice.message}
        </p>
      )}

      {cutCount > 0 && (
        <p
          role="status"
          className="mb-4 rounded-[8px] border border-accent/30 bg-accent/[0.08] px-3 py-2 text-[12px] leading-relaxed text-ink"
        >
          <span className="font-semibold text-accent">
            {cutCount === 1
              ? "1 producto está bajo su punto de equilibrio"
              : `${cutCount} productos están bajo su punto de equilibrio`}
          </span>{" "}
          en {routeCountry?.name ?? countryCode}: cada despacho pierde plata. La columna
          «Decisión» dice cuáles.
        </p>
      )}

      {!loading && !error && rows.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setStatuses(new Set())}
            className={cx(
              "rounded-full border px-3 py-1 text-[11.5px] font-semibold transition-colors",
              statuses.size === 0
                ? "border-accent/40 bg-accent/[0.12] text-accent"
                : "border-line-input text-ink-muted hover:text-ink-2",
            )}
          >
            Todos ({rows.length})
          </button>

          {STATUS_ORDER.map((status) => {
            const meta = STATUS_META[status];
            const on = statuses.has(status);
            return (
              <button
                key={status}
                type="button"
                onClick={() => toggleStatus(status)}
                title={meta.explanation}
                aria-pressed={on}
                className={cx(
                  "rounded-full border px-3 py-1 text-[11.5px] font-semibold transition-colors",
                  on
                    ? "border-accent/40 bg-accent/[0.12] text-accent"
                    : "border-line-input text-ink-muted hover:text-ink-2",
                )}
              >
                {meta.label} ({counts[status]})
              </button>
            );
          })}

          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Buscar por nombre, SKU o proveedor…"
            aria-label="Buscar productos"
            className="ml-auto w-full max-w-[280px] rounded-[8px] border border-line-input bg-surface px-3 py-1.5 text-[12.5px]"
          />
        </div>
      )}

      <Card bodyClassName="p-0">
        {loading && (
          <div className="p-4">
            <SkeletonRows rows={8} />
          </div>
        )}

        {!loading && error && (
          <div className="p-4">
            <ErrorState message={error.message} onRetry={reload} />
          </div>
        )}

        {!loading && !error && rows.length === 0 && (
          <div className="px-6 py-4">
            <EmptyState
              title="Todavía no hay productos en el catálogo"
              instruction="No tienes que crearlos a mano: apenas subas un reporte de guías, cada producto que aparezca ahí se agrega solo a esta lista. Lo que sí depende de ti es escribirle el costo real a cada uno — sin ese número, el margen y la contribución del tablero son una aproximación."
              action={
                <div className="flex flex-wrap items-center justify-center gap-2">
                  <Link
                    href={`/${countryCode.toLowerCase()}/cargar`}
                    className="rounded-[8px] bg-accent px-3.5 py-1.5 text-[12px] font-semibold text-on-accent no-underline hover:bg-accent-hover"
                  >
                    Master Data
                  </Link>
                  {canEdit && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setPanel({ mode: "new" })}
                    >
                      Crear uno a mano
                    </Button>
                  )}
                </div>
              }
            />
          </div>
        )}

        {!loading && !error && rows.length > 0 && visible.length === 0 && (
          <div className="px-6 py-4">
            <EmptyState
              title="Ningún producto coincide"
              instruction="Ajusta la búsqueda o quita los filtros de estado para volver a ver el catálogo completo."
              action={
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setSearch("");
                    setStatuses(new Set());
                  }}
                >
                  Quitar filtros
                </Button>
              }
            />
          </div>
        )}

        {!loading && !error && visible.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[940px] border-collapse text-[12px]">
              <thead>
                <tr>
                  {[
                    "Producto",
                    "SKU",
                    "Proveedor",
                    "Costo",
                    "Precio",
                    "Margen %",
                    "Guías",
                    "% entrega",
                    "Decisión",
                    "Estado",
                  ].map((header, index) => (
                    <th
                      key={header}
                      scope="col"
                      className={cx(
                        "px-3 py-2 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ink-dim",
                        index >= 3 && index <= 7 ? "text-right" : "text-left",
                      )}
                    >
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>

              <tbody>
                {visible.map((row) => {
                  const money = formatFor(row.currency_code);
                  const rate = deliveryRate(row);
                  const meta = STATUS_META[row.catalogue_status];

                  return (
                    <tr
                      key={row.product_id}
                      onClick={() => setPanel({ mode: "edit", row })}
                      tabIndex={0}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setPanel({ mode: "edit", row });
                        }
                      }}
                      className="cursor-pointer border-t border-line-row hover:bg-white/[0.03] focus:bg-white/[0.05] focus:outline-none"
                    >
                      <td className="max-w-[240px] px-3 py-2 text-left">
                        <span className="block truncate font-medium text-ink-body">
                          {row.product_name}
                        </span>
                        {!row.is_active && (
                          <span className="text-[10.5px] text-ink-dim">Inactivo</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-left text-ink-muted">
                        {orDash(row.sku)}
                      </td>
                      <td className="max-w-[160px] px-3 py-2 text-left text-ink-muted">
                        <span className="block truncate">
                          {orDash(row.supplier_name)}
                        </span>
                      </td>
                      <td
                        className={cx(
                          "px-3 py-2 text-right",
                          row.unit_cost === null ? "text-negative" : "text-ink-2",
                        )}
                      >
                        {row.unit_cost === null
                          ? "sin costo"
                          : formatMoney(row.unit_cost, money)}
                      </td>
                      <td className="px-3 py-2 text-right text-ink-2">
                        {formatMoney(row.list_price, money)}
                      </td>
                      <td className="px-3 py-2 text-right text-ink-2">
                        {formatPercent(row.catalogue_margin_pct)}
                      </td>
                      <td className="px-3 py-2 text-right text-ink-2">
                        {formatNumber(row.shipments, money, 0)}
                      </td>
                      <td className={cx("px-3 py-2 text-right", deliveryTone(rate))}>
                        {formatPercent(rate)}
                      </td>
                      <td className="px-3 py-2 text-left">
                        {(() => {
                          const verdict = verdictByProduct.get(row.product_id);
                          if (!verdict) return <span className="text-ink-dim">—</span>;
                          const verdictMeta = VERDICT_META[verdict.verdict];
                          return (
                            <span title={verdict.headline}>
                              <Chip tone={verdictMeta.tone}>{verdictMeta.label}</Chip>
                            </span>
                          );
                        })()}
                      </td>
                      <td className="px-3 py-2 text-left">
                        <span title={meta.explanation}>
                          <Chip tone={meta.tone}>{meta.label}</Chip>
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {!loading && !error && rows.length > 0 && (
          <p className="border-t border-line-subtle px-4 py-3 text-[11.5px] leading-relaxed text-ink-dim">
            {missingCost > 0
              ? `${missingCost} de ${rows.length} productos todavía no tienen un costo confirmado. Cada uno de ellos distorsiona el margen, la contribución y el punto de equilibrio del tablero.`
              : "Todos los productos tienen el costo confirmado y al día. Los márgenes del tablero son reales."}{" "}
            Este catálogo es el mismo para todos tus países: un producto se despacha
            desde varios, así que la lista no está filtrada por{" "}
            {routeCountry?.name ?? countryCode}. Cada fila muestra los importes en su
            propia moneda.
          </p>
        )}
      </Card>

      {panel && (
        <ProductPanel
          // Remount per product: the panel holds the "before" snapshot that
          // decides what a save is allowed to touch.
          key={panel.mode === "edit" ? panel.row.product_id : "new"}
          target={panel}
          formatFor={formatFor}
          readOnly={!canEdit}
          onClose={() => setPanel(null)}
          onSaved={() => {
            setPanel(null);
            setNotice(null);
            reload();
          }}
          onNotice={setNotice}
          onDuplicate={(name, message) => {
            // The product already exists - almost always because a report
            // created it. Landing the operator on it beats a red box telling
            // them what they cannot do.
            setPanel(null);
            setStatuses(new Set());
            setSearch(name);
            setNotice({ tone: "warning", message });
          }}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Side panel
// ---------------------------------------------------------------------------

interface PanelForm {
  name: string;
  sku: string;
  supplier_name: string;
  category: string;
  unit_cost: string;
  list_price: string;
  target_margin_pct: string;
  weight_kg: string;
  notes: string;
}

function emptyForm(): PanelForm {
  return {
    name: "",
    sku: "",
    supplier_name: "",
    category: "",
    unit_cost: "",
    list_price: "",
    target_margin_pct: "",
    weight_kg: "",
    notes: "",
  };
}

function formFrom(row: ProductCatalogueRow): PanelForm {
  return {
    name: row.product_name,
    sku: row.sku ?? "",
    supplier_name: row.supplier_name ?? "",
    category: row.category ?? "",
    unit_cost: numberInput(row.unit_cost),
    list_price: numberInput(row.list_price),
    target_margin_pct: numberInput(row.target_margin_pct),
    weight_kg: numberInput(row.weight_kg),
    notes: row.notes ?? "",
  };
}

/**
 * Build the PATCH body: ONLY what the operator changed.
 *
 * The API keys on which fields are present - absent leaves the value alone, an
 * explicit `null` clears it. Serialising the whole form would therefore send
 * nulls for every box nobody opened and wipe them, so each field is compared
 * against how it looked when the panel opened.
 *
 * `name` is NOT NULL, so it travels only when it changed to something real.
 */
function buildPatch(
  initial: PanelForm,
  form: PanelForm,
  row: ProductCatalogueRow,
): ProductWrite {
  const patch: ProductWrite = {};

  /** Unchanged -> absent. Emptied -> null. Otherwise the new value. */
  function textChange(key: keyof PanelForm): string | null | undefined {
    const now = form[key].trim();
    if (now === initial[key].trim()) return undefined;
    return now === "" ? null : now;
  }

  function numberChange(key: keyof PanelForm): number | null | undefined {
    const now = form[key].trim();
    if (now === initial[key].trim()) return undefined;
    return now === "" ? null : parseNumber(form[key]);
  }

  const name = form.name.trim();
  if (name !== "" && name !== initial.name.trim()) patch.name = name;

  const sku = textChange("sku");
  if (sku !== undefined) patch.sku = sku;
  const category = textChange("category");
  if (category !== undefined) patch.category = category;
  const supplier = textChange("supplier_name");
  if (supplier !== undefined) patch.supplier_name = supplier;
  const notes = textChange("notes");
  if (notes !== undefined) patch.notes = notes;

  const listPrice = numberChange("list_price");
  if (listPrice !== undefined) patch.list_price = listPrice;
  const targetMargin = numberChange("target_margin_pct");
  if (targetMargin !== undefined) patch.target_margin_pct = targetMargin;
  const weight = numberChange("weight_kg");
  if (weight !== undefined) patch.weight_kg = weight;

  const cost = numberChange("unit_cost");
  if (cost !== undefined) {
    patch.unit_cost = cost;
  } else if (row.catalogue_status !== "ok" && parseNumber(form.unit_cost) !== null) {
    // Unchanged, but the operator opened an unconfirmed product, looked at the
    // cost and saved: that IS the confirmation. Re-sending the same number is
    // what stamps `reviewed_at`, and it cannot wipe anything - it writes back
    // the value that was already there.
    patch.unit_cost = parseNumber(form.unit_cost);
  }

  return patch;
}

function ProductPanel({
  target,
  formatFor,
  readOnly,
  onClose,
  onSaved,
  onNotice,
  onDuplicate,
}: {
  target: Exclude<PanelTarget, null>;
  formatFor: (currencyCode: string | null) => FormatCountry;
  readOnly: boolean;
  onClose: () => void;
  onSaved: () => void;
  onNotice: (notice: Notice) => void;
  onDuplicate: (name: string, message: string) => void;
}) {
  const row = target.mode === "edit" ? target.row : null;
  /** What the product looked like when the panel opened. A save is a diff against this. */
  const initial = useMemo(() => (row ? formFrom(row) : emptyForm()), [row]);
  const [form, setForm] = useState<PanelForm>(initial);
  const [pending, setPending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // The list endpoint has no history; the detail one does. Only fetched for an
  // existing product - a blank form has nothing to look back on.
  const { data: detail } = useApi<ProductDetail>(
    row ? `/products/${row.product_id}` : null,
  );
  const history = detail?.cost_history ?? [];

  const money = formatFor(row?.currency_code ?? null);

  /** Saving with a cost in the box is what confirms it, so the button says so. */
  const confirming =
    row !== null &&
    row.catalogue_status !== "ok" &&
    parseNumber(form.unit_cost) !== null;

  function set<K extends keyof PanelForm>(key: K, value: string) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  /** Catch a typo here rather than letting the API answer with a 422. */
  function firstIssue(): string | null {
    if (form.name.trim() === "") return "El producto necesita un nombre.";
    return (
      numericIssue(form.unit_cost, "Costo unitario") ??
      numericIssue(form.list_price, "Precio de lista") ??
      numericIssue(form.target_margin_pct, "Margen objetivo", 100) ??
      numericIssue(form.weight_kg, "Peso")
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (readOnly) return;

    const issue = firstIssue();
    if (issue) {
      setFormError(issue);
      return;
    }
    setFormError(null);

    const cost = parseNumber(form.unit_cost);
    const name = form.name.trim();

    setPending(true);
    try {
      if (row) {
        const patch = buildPatch(initial, form, row);
        // Nothing moved. A round trip that writes nothing is still a round trip
        // that can fail, so just close.
        if (Object.keys(patch).length === 0) {
          onClose();
          return;
        }
        await api.patch(`/products/${row.product_id}`, patch);
      } else {
        const created: ProductWrite = {
          name,
          sku: textForPost(form.sku),
          supplier_name: textForPost(form.supplier_name),
          category: textForPost(form.category),
          notes: textForPost(form.notes),
        };
        const listPrice = parseNumber(form.list_price);
        const targetMargin = parseNumber(form.target_margin_pct);
        const weight = parseNumber(form.weight_kg);
        if (cost !== null) created.unit_cost = cost;
        if (listPrice !== null) created.list_price = listPrice;
        if (targetMargin !== null) created.target_margin_pct = targetMargin;
        if (weight !== null) created.weight_kg = weight;
        await api.post("/products", created);
      }
      onSaved();
    } catch (err) {
      // A repeated name is the normal case, not an error: ingestion creates
      // products on its own, so the one being typed usually already exists.
      if (err instanceof ApiError && err.code === "conflict") {
        onDuplicate(name, err.message);
        return;
      }
      onNotice({
        tone: "negative",
        message:
          err instanceof ApiError ? err.message : "No se pudo guardar el producto",
      });
    } finally {
      setPending(false);
    }
  }

  /**
   * Archive or restore, without touching anything else.
   *
   * A PATCH carrying only `is_active` leaves `unit_cost` alone, so archiving a
   * product does not silently mark its cost as confirmed.
   */
  async function toggleActive() {
    if (!row || readOnly) return;
    setPending(true);
    try {
      await api.patch(`/products/${row.product_id}`, {
        is_active: !row.is_active,
      } satisfies ProductWrite);
      onSaved();
    } catch (err) {
      onNotice({
        tone: "negative",
        message:
          err instanceof ApiError ? err.message : "No se pudo cambiar el estado",
      });
    } finally {
      setPending(false);
    }
  }

  /** What the reports say this product costs, when it disagrees with the form. */
  const observed = row?.observed_unit_cost ?? null;

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} aria-hidden />
      <aside
        className="fixed right-0 top-0 z-50 flex h-full w-full max-w-[420px] flex-col border-l border-line bg-sidebar"
        role="dialog"
        aria-label={row ? `Editar ${row.product_name}` : "Nuevo producto"}
      >
        <header className="flex items-start justify-between gap-3 border-b border-line-subtle px-4 py-3.5">
          <div className="min-w-0">
            <h2 className="truncate text-[14px] font-bold">
              {row ? row.product_name : "Nuevo producto"}
            </h2>
            {row && (
              <p className="mt-0.5 text-[11px] text-ink-dim">
                {formatNumber(row.shipments, money, 0)} guías ·{" "}
                {row.last_shipment_date
                  ? `último despacho ${formatDate(row.last_shipment_date, money)}`
                  : "sin despachos"}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-[6px] px-2 py-1 text-[13px] text-ink-muted hover:bg-white/[0.05]"
            aria-label="Cerrar"
          >
            ✕
          </button>
        </header>

        <form onSubmit={submit} className="flex flex-1 flex-col overflow-y-auto">
          <div className="flex-1 space-y-3 p-4">
            {readOnly && (
              <p className="rounded-[8px] border border-line-input bg-sunken px-3 py-2 text-[11.5px] leading-relaxed text-ink-muted">
                Tu rol es de solo lectura: puedes ver el catálogo, pero no cambiarlo.
                Pídele a quien tenga rol de dueño o analista que ajuste el costo.
              </p>
            )}

            {row && !row.is_active && (
              <p className="rounded-[8px] border border-line-input bg-sunken px-3 py-2 text-[11.5px] leading-relaxed text-ink-muted">
                Este producto está archivado: sigue en el historial y en los cálculos de
                lo que ya despachaste, pero no cuenta como parte del catálogo activo.
              </p>
            )}

            {row && (
              <p
                className={cx(
                  "rounded-[8px] px-3 py-2 text-[11.5px] leading-relaxed",
                  row.catalogue_status === "ok"
                    ? "border border-line-input bg-sunken text-ink-muted"
                    : "border border-warning/30 bg-warning/[0.07] text-warning",
                )}
              >
                {STATUS_META[row.catalogue_status].explanation}
                {observed !== null && (
                  <>
                    {" "}
                    Las guías indican un costo de{" "}
                    <b>{formatMoney(observed, money)}</b> por unidad.
                  </>
                )}
              </p>
            )}

            <Field label="Nombre">
              <input
                value={form.name}
                onChange={(event) => set("name", event.target.value)}
                required
                disabled={readOnly}
                className={INPUT_CLASS}
              />
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="SKU">
                <input
                  value={form.sku}
                  onChange={(event) => set("sku", event.target.value)}
                  disabled={readOnly}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label="Categoría">
                <input
                  value={form.category}
                  onChange={(event) => set("category", event.target.value)}
                  disabled={readOnly}
                  className={INPUT_CLASS}
                />
              </Field>
            </div>

            <Field label="Proveedor">
              <input
                value={form.supplier_name}
                onChange={(event) => set("supplier_name", event.target.value)}
                disabled={readOnly}
                placeholder="Como aparece en la factura"
                className={INPUT_CLASS}
              />
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field
                label={`Costo unitario (${money.currency_symbol})`}
                hint="Lo que le pagas al proveedor por una unidad. Al guardar, este costo queda confirmado por ti."
              >
                <input
                  value={form.unit_cost}
                  onChange={(event) => set("unit_cost", event.target.value)}
                  inputMode="decimal"
                  disabled={readOnly}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label={`Precio de lista (${money.currency_symbol})`}>
                <input
                  value={form.list_price}
                  onChange={(event) => set("list_price", event.target.value)}
                  inputMode="decimal"
                  disabled={readOnly}
                  className={INPUT_CLASS}
                />
              </Field>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Field label="Margen objetivo (%)">
                <input
                  value={form.target_margin_pct}
                  onChange={(event) => set("target_margin_pct", event.target.value)}
                  inputMode="decimal"
                  disabled={readOnly}
                  className={INPUT_CLASS}
                />
              </Field>
              <Field label="Peso (kg)" hint="Sirve para comparar el flete por kilo.">
                <input
                  value={form.weight_kg}
                  onChange={(event) => set("weight_kg", event.target.value)}
                  inputMode="decimal"
                  disabled={readOnly}
                  className={INPUT_CLASS}
                />
              </Field>
            </div>

            <Field label="Notas">
              <textarea
                value={form.notes}
                onChange={(event) => set("notes", event.target.value)}
                rows={3}
                disabled={readOnly}
                placeholder="Acuerdos con el proveedor, mínimos de compra, lo que quieras recordar."
                className={cx(INPUT_CLASS, "resize-y")}
              />
            </Field>

            {history.length > 0 && (
              <section className="border-t border-line-subtle pt-3">
                <h3 className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-ink-faint">
                  Cómo se ha movido el costo
                </h3>
                <ul className="mt-2 space-y-1.5">
                  {history.slice(0, HISTORY_LIMIT).map((entry) => (
                    <li
                      key={entry.id}
                      className="flex items-baseline justify-between gap-3 text-[11.5px]"
                    >
                      <span className="font-semibold text-ink-2">
                        {formatMoney(entry.unit_cost, money)}
                      </span>
                      <span
                        className={cx(
                          "text-right",
                          // A human vouching for a number is the signal; a file
                          // writing one is just background.
                          entry.source === "manual" ? "text-ink-muted" : "text-ink-dim",
                        )}
                      >
                        {formatDate(entry.changed_at, money)} ·{" "}
                        {COST_SOURCE_LABELS[entry.source]}
                      </span>
                    </li>
                  ))}
                </ul>
                {history.length > HISTORY_LIMIT && (
                  <p className="mt-1.5 text-[10.5px] text-ink-dim">
                    {`Hay ${history.length - HISTORY_LIMIT} cambios más antes de estos.`}
                  </p>
                )}
                <p className="mt-2 text-[11px] leading-relaxed text-ink-dim">
                  Si el costo viene subiendo, el proveedor cambió el precio y el margen
                  de este producto se movió con él sin que nadie lo anunciara.
                </p>
              </section>
            )}
          </div>

          {!readOnly && (
            <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-line-subtle px-4 py-3">
              {formError && (
                <p className="w-full text-[11.5px] leading-relaxed text-negative">
                  {formError}
                </p>
              )}

              {row && (
                <Button
                  type="button"
                  size="sm"
                  variant={row.is_active ? "danger" : "ghost"}
                  onClick={toggleActive}
                  disabled={pending}
                  className="mr-auto"
                  title={
                    row.is_active
                      ? "Deja de contarlo en el catálogo activo. No borra nada y se puede reactivar."
                      : "Vuelve a contarlo en el catálogo activo."
                  }
                >
                  {row.is_active ? "Archivar" : "Reactivar"}
                </Button>
              )}

              <Button type="button" size="sm" variant="ghost" onClick={onClose}>
                Cancelar
              </Button>
              <Button type="submit" size="sm" disabled={pending || !form.name.trim()}>
                {pending
                  ? "Guardando…"
                  : confirming
                    ? "Confirmar costo y guardar"
                    : row
                      ? "Guardar cambios"
                      : "Crear producto"}
              </Button>
            </footer>
          )}
        </form>
      </aside>
    </>
  );
}

/** How many cost changes fit in the panel before it stops being a list. */
const HISTORY_LIMIT = 6;

const INPUT_CLASS =
  "w-full rounded-[8px] border border-line-input bg-surface px-3 py-2 text-[13px] disabled:opacity-60";

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11.5px] text-ink-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[10.5px] text-ink-dim">{hint}</span>}
    </label>
  );
}
