"use client";

/**
 * Clientes: the same people, seen across every guide they ever received.
 *
 * A customer is grouped by a deterministic hash of their phone, so the same
 * person is one row no matter how many uploads they appear in. That is what
 * makes this screen worth having: a buyer who returns four parcels out of five
 * is invisible one guide at a time and obvious here.
 *
 * Scoped to one country by the route, because everything on this screen is
 * money: contribution, revenue, contribution per order. Two currencies in one
 * column is not a formatting problem, it is a reading problem.
 *
 * The grade is a word, never a score. With fewer than two closed orders there
 * is no statistical basis for a percentage, and "nuevo" says that honestly
 * instead of inventing a 50%.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/AppShell";
import {
  Block,
  CountryMissing,
  FilterError,
  Line,
  PaginationFooter,
  PiiNotice,
} from "@/components/browse";
import { Button, Card, Chip, Drawer, EmptyState, ErrorState, SkeletonRows, cx } from "@/components/ui";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, qs } from "@/lib/api";
import { useRouteCountry } from "@/lib/country";
import {
  countryFlag,
  formatDate,
  formatMoney,
  formatNumber,
  formatPercent,
  pluralize,
} from "@/lib/format";
import { useApi } from "@/lib/hooks";
import {
  CUSTOMER_ORDERS_CAP,
  GRADE_ORDER,
  PII_HIDDEN_NOTICE,
  PII_MISSING_NOTICE,
  contactLabel,
  contactNotice,
  gradeMeta,
} from "@/lib/orders";
import { statusGroupMeta } from "@/lib/status";
import type {
  Country,
  CustomerDetail,
  CustomerGrade,
  CustomerRow,
  CustomersPage,
} from "@/lib/types";

const PAGE_SIZE = 50;

/** How many of a customer's guides the panel lists before it stops being a list. */
const ORDERS_IN_PANEL = 25;

export default function CustomersPage() {
  return (
    <AppShell>
      {/* `useSearchParams` reads the ?customer= deep link an order card links
          to, and Next requires it to sit behind a boundary. */}
      <Suspense fallback={<SkeletonRows rows={10} />}>
        <CustomersScreen />
      </Suspense>
    </AppShell>
  );
}

function CustomersScreen() {
  const { code: countryCode, country, countries, missing } = useRouteCountry();

  const search = useSearchParams();
  const router = useRouter();
  const linkedHash = search.get("customer");

  const [grade, setGrade] = useState<CustomerGrade | "">("");
  const [minOrders, setMinOrders] = useState("");
  const [page, setPage] = useState(1);
  const [openHash, setOpenHash] = useState<string | null>(linkedHash);

  useEffect(() => {
    if (linkedHash) setOpenHash(linkedHash);
  }, [linkedHash]);

  useEffect(() => {
    setPage(1);
  }, [countryCode, grade, minOrders]);

  function closeCard() {
    setOpenHash(null);
    // Otherwise the link is still in the URL and the card reopens on reload.
    if (linkedHash) {
      router.replace(`/${countryCode.toLowerCase()}/customers`, { scroll: false });
    }
  }

  const path = country
    ? `/customers${qs({
        country: countryCode,
        page,
        page_size: PAGE_SIZE,
        grade,
        min_orders: sanitiseMinOrders(minOrders),
      })}`
    : null;

  const { data, error, loading, reload } = useApi<CustomersPage>(path);

  const rows = data?.rows ?? [];
  const piiVisible = data?.pii_visible ?? true;
  const notice = contactNotice(rows, piiVisible);
  const hasFilters = grade !== "" || minOrders !== "";

  function clearFilters() {
    setGrade("");
    setMinOrders("");
  }

  if (missing) {
    return <CountryMissing code={countryCode} countries={countries} section="customers" />;
  }

  return (
    <>
      <PageHeader
        flag={countryFlag(countryCode)}
        title="Clientes"
        subtitle={`Cada persona de ${country?.name ?? countryCode} agrupada por su teléfono, con todos sus pedidos juntos. Acá se ve quién recibe lo que le mandas y quién te devuelve la mitad, algo que guía por guía es imposible de notar.`}
      />

      {notice && rows.length > 0 && (
        <PiiNotice>
          {notice === "hidden" ? PII_HIDDEN_NOTICE : PII_MISSING_NOTICE}
        </PiiNotice>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setGrade("")}
          aria-pressed={grade === ""}
          className={cx(FILTER_PILL, grade === "" ? FILTER_ON : FILTER_OFF)}
        >
          Todos
        </button>

        {GRADE_ORDER.map((item) => {
          const meta = gradeMeta(item);
          return (
            <button
              key={item}
              type="button"
              onClick={() => setGrade(grade === item ? "" : item)}
              title={meta.explanation}
              aria-pressed={grade === item}
              className={cx(FILTER_PILL, grade === item ? FILTER_ON : FILTER_OFF)}
            >
              {meta.label}
            </button>
          );
        })}

        <label className="ml-auto flex items-center gap-2 text-sm text-ink-dim">
          Mínimo de pedidos
          <input
            type="number"
            min={1}
            value={minOrders}
            onChange={(event) => setMinOrders(event.target.value)}
            placeholder="1"
            aria-label="Mínimo de pedidos"
            className="w-[72px] rounded-control border border-line-input bg-surface px-2.5 py-1.5 text-base text-ink-2"
          />
        </label>

        {hasFilters && (
          <Button size="sm" variant="ghost" onClick={clearFilters}>
            Quitar filtros
          </Button>
        )}
      </div>

      <Card bodyClassName="p-0">
        {loading && (
          <div className="p-4">
            <SkeletonRows rows={10} />
          </div>
        )}

        {!loading && error && (
          <div className="p-4">
            {error instanceof ApiError && error.status === 422 ? (
              <FilterError message={error.message} onClear={clearFilters} />
            ) : (
              <ErrorState message={error.message} onRetry={reload} />
            )}
          </div>
        )}

        {!loading && !error && country && rows.length === 0 && (
          <div className="px-6 py-4">
            <EmptyState
              title={hasFilters ? "Ningún cliente coincide" : "Todavía no hay clientes"}
              instruction={
                hasFilters
                  ? "Prueba con otra calidad o baja el mínimo de pedidos."
                  : "Los clientes se arman solos a partir de las guías: apenas subas un reporte de este país, cada teléfono distinto se convierte en un cliente con su historial."
              }
              action={
                hasFilters ? (
                  <Button size="sm" variant="ghost" onClick={clearFilters}>
                    Quitar filtros
                  </Button>
                ) : undefined
              }
            />
          </div>
        )}

        {!loading && !error && rows.length > 0 && country && (
          <div className="data-table">
            <table className="w-full min-w-[1020px] border-collapse text-sm">
              <thead>
                <tr>
                  {[
                    { label: "Cliente", align: "left" },
                    { label: "Pedidos", align: "right" },
                    { label: "Entregados", align: "right" },
                    { label: "Devueltos", align: "right" },
                    { label: "% entrega", align: "right" },
                    { label: "Contribución", align: "right" },
                    { label: "Calidad", align: "left" },
                    { label: "Ciudad", align: "left" },
                    { label: "Último pedido", align: "left" },
                  ].map((header) => (
                    <th
                      key={header.label}
                      scope="col"
                      className={cx(
                        "px-3 py-2 text-xs font-semibold uppercase tracking-[0.06em] text-ink-dim",
                        header.align === "right" ? "text-right" : "text-left",
                      )}
                    >
                      {header.label}
                    </th>
                  ))}
                </tr>
              </thead>

              <tbody>
                {rows.map((row) => (
                  <CustomerTableRow
                    key={row.customer_hash}
                    row={row}
                    country={country}
                    piiVisible={piiVisible}
                    onOpen={() => setOpenHash(row.customer_hash)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!loading && !error && rows.length > 0 && (
          <PaginationFooter
            page={data?.page ?? page}
            pageSize={data?.page_size ?? PAGE_SIZE}
            total={data?.total ?? 0}
            loading={loading}
            noun="clientes"
            onPageChange={setPage}
          />
        )}
      </Card>

      {openHash && country && (
        <CustomerCard
          key={openHash}
          customerHash={openHash}
          country={country}
          piiVisible={piiVisible}
          onClose={closeCard}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function CustomerTableRow({
  row,
  country,
  piiVisible,
  onOpen,
}: {
  row: CustomerRow;
  country: Country;
  piiVisible: boolean;
  onOpen: () => void;
}) {
  const contact = contactLabel(row, piiVisible);
  const grade = gradeMeta(row.customer_grade);

  return (
    <tr
      onClick={onOpen}
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      className="cursor-pointer border-t border-line-row hover:bg-hover focus:bg-hover-strong focus:outline-none"
    >
      <td className="max-w-[220px] px-3 py-2 text-left">
        <span
          className={cx(
            "block truncate",
            contact.hidden ? "font-mono text-ink-muted" : "font-medium text-ink-body",
          )}
          title={contact.hidden ? "Código de cliente" : contact.primary}
        >
          {contact.primary}
        </span>
        {contact.secondary && (
          <span className="block truncate text-xs text-ink-dim">
            {contact.secondary}
          </span>
        )}
      </td>

      <td className="px-3 py-2 text-right text-ink-2">
        {formatNumber(row.orders, country, 0)}
        {row.open_orders > 0 && (
          <span
            className="ml-1 text-xs text-accent-ink"
            title={`${row.open_orders} todavía en la calle`}
          >
            +{row.open_orders}
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-right text-ink-2">
        {formatNumber(row.delivered, country, 0)}
      </td>
      <td
        className={cx(
          "px-3 py-2 text-right",
          row.returned > 0 ? "text-negative-ink" : "text-ink-dim",
        )}
      >
        {formatNumber(row.returned, country, 0)}
      </td>
      <td className={cx("px-3 py-2 text-right", deliveryTone(row.delivery_rate_pct))}>
        {formatPercent(row.delivery_rate_pct)}
      </td>
      <td
        className={cx(
          "px-3 py-2 text-right font-medium",
          (row.contribution ?? 0) >= 0 ? "text-ink-2" : "text-negative-ink",
        )}
      >
        {formatMoney(row.contribution, country)}
      </td>
      <td className="px-3 py-2 text-left">
        <span title={grade.explanation}>
          <Chip tone={grade.tone}>{grade.label}</Chip>
        </span>
      </td>
      <td className="max-w-[140px] px-3 py-2 text-left text-ink-muted">
        <span className="block truncate">{row.main_city ?? "—"}</span>
      </td>
      <td className="px-3 py-2 text-left text-ink-muted">
        {row.last_order_date ? formatDate(row.last_order_date, country) : "—"}
      </td>
    </tr>
  );
}

/** Same thresholds the carrier and product tables use, so a number means one thing. */
function deliveryTone(pct: number | null): string {
  if (pct === null) return "text-ink-dim";
  if (pct >= 75) return "text-positive-ink";
  if (pct >= 60) return "text-warning-ink";
  return "text-negative-ink";
}

// ---------------------------------------------------------------------------
// Detail card
// ---------------------------------------------------------------------------

/**
 * Everything known about one person, in five blocks.
 *
 * The operator asked for all of it, so none of it is behind a "ver más": who
 * they are, where the parcels go, how they behave, what they leave, and every
 * guide. The order is deliberate - identity first, because that is what you
 * read to a customer on the phone; the money last, because that is what you
 * read before deciding whether to keep despatching to them.
 */
function CustomerCard({
  customerHash,
  country,
  piiVisible,
  onClose,
}: {
  customerHash: string;
  country: Country;
  piiVisible: boolean;
  onClose: () => void;
}) {
  const { data, error, loading, reload } = useApi<CustomerDetail>(
    `/customers/${customerHash}${qs({ country: country.code })}`,
  );

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const customer = data?.customer ?? null;
  // The detail carries its own permission flag; the list's is the stand-in.
  const cardPii = data?.pii_visible ?? piiVisible;
  const contact = customer ? contactLabel(customer, cardPii) : null;
  const grade = customer ? gradeMeta(customer.customer_grade) : null;
  const orders = data?.orders ?? [];

  return (
    <>
      <Drawer
        width="lg"
        onClose={onClose}
        label={contact ? `Cliente ${contact.primary}` : "Detalle del cliente"}
        title={
          <span className={cx(contact?.hidden && "font-mono")}>
            {contact ? contact.primary : "Cargando…"}
          </span>
        }
        subtitle={
          customer &&
          grade && (
            <span className="flex flex-wrap items-center gap-2">
              <Chip tone={grade.tone}>{grade.label}</Chip>
              <span>
                {pluralize(customer.orders, "pedido", "pedidos")} en {country.name}
              </span>
            </span>
          )
        }
      >
          {loading && <SkeletonRows rows={12} />}

          {!loading && error && <ErrorState message={error.message} onRetry={reload} />}

          {!loading && !error && customer && grade && contact && (
            <div className="space-y-4">
              <Block title="Quién es">
                {contact.hidden ? (
                  <>
                    <Line label="Código" value={customer.customer_ref} mono />
                    <p className="pt-1 text-xs leading-relaxed text-ink-dim">
                      {PII_HIDDEN_NOTICE}
                    </p>
                  </>
                ) : (
                  <>
                    <Line label="Nombre" value={customer.customer_name} />
                    <Line label="Teléfono" value={customer.customer_phone} />
                    <Line label="Documento" value={customer.customer_document} />
                    <Line label="Código" value={customer.customer_ref} mono />
                    {contactNotice([customer], true) === "missing" && (
                      <p className="pt-1 text-xs leading-relaxed text-ink-dim">
                        {PII_MISSING_NOTICE}
                      </p>
                    )}
                  </>
                )}
              </Block>

              <Block title="A dónde le llegan los pedidos">
                {contact.hidden ? (
                  <p className="text-sm leading-relaxed text-ink-dim">
                    La dirección de entrega es un dato de contacto, así que se oculta
                    junto con el nombre y el teléfono. La ciudad sí se puede mostrar
                    porque es la que usa el tablero para medir la operación.
                  </p>
                ) : (
                  <Line label="Dirección" value={customer.customer_address} />
                )}
                <Line label="Ciudad" value={customer.main_city} />
                <Line label="País" value={country.name} />
              </Block>

              <Block title="Cómo se comporta">
                <p className="pb-1 text-sm leading-relaxed text-ink-muted">
                  {grade.explanation}
                </p>
                <Line
                  label="Pedidos en total"
                  value={formatNumber(customer.orders, country, 0)}
                />
                <Line
                  label="Entregados"
                  value={formatNumber(customer.delivered, country, 0)}
                />
                <Line
                  label="Devueltos"
                  value={formatNumber(customer.returned, country, 0)}
                />
                <Line
                  label="Todavía en la calle"
                  value={formatNumber(customer.open_orders, country, 0)}
                />
                <Line
                  label="% de entrega"
                  value={formatPercent(customer.delivery_rate_pct)}
                />
                <Line
                  label="Productos distintos"
                  value={
                    customer.distinct_products === null
                      ? null
                      : formatNumber(customer.distinct_products, country, 0)
                  }
                />
                <Line
                  label="Primer pedido"
                  value={
                    customer.first_order_date
                      ? formatDate(customer.first_order_date, country)
                      : null
                  }
                />
                <Line
                  label="Último pedido"
                  value={
                    customer.last_order_date
                      ? formatDate(customer.last_order_date, country)
                      : null
                  }
                />
                <Line
                  label="Sin comprar hace"
                  value={
                    customer.days_since_last_order === null
                      ? null
                      : pluralize(customer.days_since_last_order, "día", "días")
                  }
                />
              </Block>

              <Block title="Cuánto deja">
                <Line label="Recaudo" value={formatMoney(customer.revenue, country)} />
                <Line
                  label="Contribución por pedido"
                  value={formatMoney(customer.contribution_per_order, country)}
                />
                <div className="mt-1 flex items-baseline justify-between gap-3 border-t border-line-subtle pt-2">
                  <span className="text-sm font-semibold text-ink-2">
                    Contribución total
                  </span>
                  <span
                    className={cx(
                      "text-lg font-bold",
                      (customer.contribution ?? 0) >= 0
                        ? "text-positive-ink"
                        : "text-negative-ink",
                    )}
                  >
                    {formatMoney(customer.contribution, country)}
                  </span>
                </div>
              </Block>

              <CustomerOrders orders={orders} country={country} />
            </div>
          )}
      </Drawer>
    </>
  );
}

/** Every guide this person received, newest first. */
function CustomerOrders({
  orders,
  country,
}: {
  orders: CustomerDetail["orders"];
  country: Country;
}) {
  const ordered = useMemo(
    () => [...orders].sort((a, b) => b.created_date.localeCompare(a.created_date)),
    [orders],
  );

  if (ordered.length === 0) {
    return (
      <Block title="Sus guías">
        <p className="text-sm leading-relaxed text-ink-dim">
          No pudimos traer las guías de este cliente. Sus totales de arriba sí están
          calculados sobre ellas.
        </p>
      </Block>
    );
  }

  const shown = ordered.slice(0, ORDERS_IN_PANEL);

  return (
    <Block title="Sus guías">
      <ul className="space-y-2">
        {shown.map((order) => {
          const status = statusGroupMeta(order.status_group);
          return (
            <li
              key={order.shipment_id}
              className="flex items-start justify-between gap-3 border-b border-line-row pb-2 last:border-b-0 last:pb-0"
            >
              <div className="min-w-0">
                <span className="block truncate font-mono text-sm text-ink-2">
                  {order.tracking_number}
                </span>
                <span className="block truncate text-xs text-ink-dim">
                  {formatDate(order.created_date, country)}
                  {order.product_name && (
                    <>
                      <span className="mx-1.5">·</span>
                      {order.product_name}
                    </>
                  )}
                </span>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Chip tone={status.tone}>{status.label}</Chip>
                <span
                  className={cx(
                    "w-[92px] text-right text-sm font-semibold",
                    (order.contribution ?? 0) >= 0 ? "text-ink-2" : "text-negative-ink",
                  )}
                >
                  {formatMoney(order.contribution, country)}
                </span>
              </div>
            </li>
          );
        })}
      </ul>

      {ordered.length > shown.length && (
        <p className="pt-2 text-xs leading-relaxed text-ink-dim">
          {`Hay ${ordered.length - shown.length} guías más antes de estas. Búscalas por número en Órdenes.`}
        </p>
      )}

      {ordered.length >= CUSTOMER_ORDERS_CAP && (
        <p className="pt-1 text-xs leading-relaxed text-ink-dim">
          {`Esta lista llega hasta las ${CUSTOMER_ORDERS_CAP} guías más recientes. Los totales de arriba sí están calculados sobre todos sus pedidos.`}
        </p>
      )}
    </Block>
  );
}

/**
 * A minimum only means something as a whole number of at least one.
 *
 * The box is a plain number input, so it happily produces "-3" and "2.5". Both
 * would come back as a 422 the operator cannot act on, so they are dropped
 * here and the filter simply does not apply.
 */
function sanitiseMinOrders(raw: string): string {
  const value = Number(raw.trim());
  if (!Number.isInteger(value) || value < 1) return "";
  return String(value);
}

const FILTER_PILL =
  "rounded-full border px-3 py-1 text-sm font-semibold transition-colors";
const FILTER_ON = "border-accent/40 bg-accent/[0.12] text-accent-ink";
const FILTER_OFF = "border-line-input text-ink-muted hover:text-ink-2";
