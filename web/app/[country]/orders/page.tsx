"use client";

/**
 * Órdenes: one row per guide, and the card that tells the whole story of one.
 *
 * The table is the index; the card is the answer. An operator comes here with a
 * guide number in their hand because a customer is on the phone, so search is
 * the first control and the detail card carries everything at once - money
 * broken down, and a timeline that reads like a story rather than two tables
 * the reader has to interleave by eye.
 *
 * PAGINATION IS SERVER-SIDE, ALWAYS. One country here has 1.649 guides and
 * growing; a client-side filter over that is a page that freezes on the day the
 * operation is doing well.
 *
 * The country comes from the route, not from a picker: guides are priced,
 * dated and separated by the rules of one country, and there is no such thing
 * as a list of guides that spans two.
 */

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { AppShell } from "@/components/AppShell";
import {
  Block,
  CountryMissing,
  FilterError,
  Line,
  PaginationFooter,
  PiiNotice,
} from "@/components/browse";
import {
  Button,
  Card,
  Chip,
  EmptyState,
  ErrorState,
  SkeletonRows,
  cx,
} from "@/components/ui";
import { ApiError, qs } from "@/lib/api";
import { useRouteCountry } from "@/lib/country";
import {
  countryFlag,
  formatDate,
  formatMoney,
  formatNumber,
  pluralize,
} from "@/lib/format";
import { useApi } from "@/lib/hooks";
import {
  PII_HIDDEN_NOTICE,
  PII_MISSING_NOTICE,
  contactLabel,
  contactNotice,
  daysOpenTone,
  daysToDeliver,
} from "@/lib/orders";
import { STATUS_GROUPS, statusGroupMeta, type StatusGroup } from "@/lib/status";
import type {
  Country,
  OrderDetail,
  OrderRow,
  OrdersPage,
  TimelineEvent,
} from "@/lib/types";

/** Fifty rows is what fits on a laptop screen without a second scroll gesture. */
const PAGE_SIZE = 50;

/** Long enough that typing a guide number is one request, not fourteen. */
const SEARCH_DEBOUNCE_MS = 350;

const TONE_TEXT: Record<string, string> = {
  neutral: "text-ink-2",
  accent: "text-accent",
  positive: "text-positive",
  warning: "text-warning",
  negative: "text-negative",
};

export default function OrdersPage() {
  return (
    <AppShell>
      <OrdersScreen />
    </AppShell>
  );
}

function OrdersScreen() {
  const { code: countryCode, country, countries, missing } = useRouteCountry();

  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search, SEARCH_DEBOUNCE_MS);
  const [group, setGroup] = useState<StatusGroup | "">("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [openOrderId, setOpenOrderId] = useState<string | null>(null);

  // Any change to what is being asked for invalidates where you were in it:
  // page 7 of the old filter is not page 7 of the new one.
  useEffect(() => {
    setPage(1);
  }, [countryCode, debouncedSearch, group, fromDate, toDate, onlyOpen]);

  const path = country
    ? `/orders${qs({
        country: countryCode,
        page,
        page_size: PAGE_SIZE,
        search: debouncedSearch.trim(),
        group,
        from_date: fromDate,
        to_date: toDate,
        only_open: onlyOpen,
      })}`
    : null;

  const { data, error, loading, reload } = useApi<OrdersPage>(path);

  const rows = data?.rows ?? [];
  const piiVisible = data?.pii_visible ?? true;
  const notice = contactNotice(rows, piiVisible);
  const hasFilters =
    debouncedSearch.trim() !== "" ||
    group !== "" ||
    fromDate !== "" ||
    toDate !== "" ||
    onlyOpen;

  function clearFilters() {
    setSearch("");
    setGroup("");
    setFromDate("");
    setToDate("");
    setOnlyOpen(false);
  }

  if (missing) {
    return <CountryMissing code={countryCode} countries={countries} section="orders" />;
  }

  return (
    <>
      <header className="mb-5">
        <h1 className="flex items-center gap-2.5 text-[22px] font-bold tracking-tight">
          <span className="text-[24px] leading-none">{countryFlag(countryCode)}</span>
          Órdenes
        </h1>
        <p className="mt-1 max-w-2xl text-[12px] leading-relaxed text-ink-dim">
          Cada guía despachada de {country?.name ?? countryCode}, con lo que dejó y
          cuánto lleva abierta. Haz clic en una fila para ver su historia completa: a
          quién iba, qué se cobró y qué se pagó, y todo lo que le fue pasando en el
          camino.
        </p>
      </header>

      {notice && rows.length > 0 && (
        <PiiNotice>
          {notice === "hidden" ? PII_HIDDEN_NOTICE : PII_MISSING_NOTICE}
        </PiiNotice>
      )}

      <div className="mb-4 flex flex-wrap items-end gap-2">
        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-dim">Buscar guía</span>
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Número de guía…"
            aria-label="Buscar por número de guía"
            className="w-[220px] rounded-[8px] border border-line-input bg-surface px-3 py-1.5 text-[12.5px]"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-dim">Estado</span>
          <select
            value={group}
            onChange={(event) => setGroup(event.target.value as StatusGroup | "")}
            className="rounded-[8px] border border-line-input bg-surface px-3 py-1.5 text-[12.5px]"
          >
            <option value="">Todos</option>
            {STATUS_GROUPS.map((item) => (
              <option key={item} value={item}>
                {statusGroupMeta(item).label}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-dim">Desde</span>
          <input
            type="date"
            value={fromDate}
            onChange={(event) => setFromDate(event.target.value)}
            className="rounded-[8px] border border-line-input bg-surface px-3 py-1.5 text-[12.5px]"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-dim">Hasta</span>
          <input
            type="date"
            value={toDate}
            onChange={(event) => setToDate(event.target.value)}
            className="rounded-[8px] border border-line-input bg-surface px-3 py-1.5 text-[12.5px]"
          />
        </label>

        <label
          className="flex cursor-pointer items-center gap-2 rounded-[8px] border border-line-input px-3 py-2 text-[12px] text-ink-2"
          title="Deja fuera las guías que ya se entregaron, se devolvieron o se dieron por perdidas."
        >
          <input
            type="checkbox"
            checked={onlyOpen}
            onChange={(event) => setOnlyOpen(event.target.checked)}
            className="size-3.5 accent-[var(--color-accent)]"
          />
          Solo abiertas
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
              title={hasFilters ? "Ninguna guía coincide" : "Todavía no hay guías"}
              instruction={
                hasFilters
                  ? "Ajusta la búsqueda, el estado o el rango de fechas. Ten en cuenta que la búsqueda es por número de guía, no por nombre del cliente."
                  : "Apenas subas un reporte de guías para este país, cada una aparecerá acá con su estado y su contribución."
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
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] border-collapse text-[12px]">
              <thead>
                <tr>
                  {[
                    { label: "Guía", align: "left" },
                    { label: "Cliente", align: "left" },
                    { label: "Ciudad", align: "left" },
                    { label: "Producto", align: "left" },
                    { label: "Estado", align: "left" },
                    { label: "Contribución", align: "right" },
                    { label: "Días", align: "right" },
                  ].map((header) => (
                    <th
                      key={header.label}
                      scope="col"
                      className={cx(
                        "px-3 py-2 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-ink-dim",
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
                  <OrderTableRow
                    key={row.shipment_id}
                    row={row}
                    country={country}
                    piiVisible={piiVisible}
                    onOpen={() => setOpenOrderId(row.shipment_id)}
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
            noun="guías"
            onPageChange={setPage}
          />
        )}
      </Card>

      {openOrderId && country && (
        <OrderCard
          key={openOrderId}
          shipmentId={openOrderId}
          country={country}
          piiVisible={piiVisible}
          onClose={() => setOpenOrderId(null)}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function OrderTableRow({
  row,
  country,
  piiVisible,
  onOpen,
}: {
  row: OrderRow;
  country: Country;
  piiVisible: boolean;
  onOpen: () => void;
}) {
  const status = statusGroupMeta(row.status_group);
  const contact = contactLabel(row, piiVisible);
  const daysTone = daysOpenTone(row.days_open, row.is_terminal);
  // A closed guide has no days open - it has days it took. Different number,
  // same column, so each cell says which one it is showing.
  const delivery = daysToDeliver(row.created_date, row.delivered_at);

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
      className="cursor-pointer border-t border-line-row hover:bg-white/[0.03] focus:bg-white/[0.05] focus:outline-none"
    >
      <td className="px-3 py-2 text-left">
        <span className="block font-medium text-ink-body">{row.tracking_number}</span>
        <span className="text-[10.5px] text-ink-dim">
          {formatDate(row.created_date, country)}
        </span>
      </td>

      <td className="max-w-[200px] px-3 py-2 text-left">
        <span
          className={cx(
            "block truncate",
            contact.hidden ? "font-mono text-ink-muted" : "text-ink-body",
          )}
          title={contact.hidden ? "Código de cliente" : contact.primary}
        >
          {contact.primary}
        </span>
        {contact.secondary && (
          <span className="block truncate text-[10.5px] text-ink-dim">
            {contact.secondary}
          </span>
        )}
      </td>

      <td className="max-w-[140px] px-3 py-2 text-left text-ink-muted">
        <span className="block truncate">{row.city_name ?? "—"}</span>
      </td>

      <td className="max-w-[200px] px-3 py-2 text-left text-ink-muted">
        <span className="block truncate">{row.product_name ?? "—"}</span>
      </td>

      <td className="px-3 py-2 text-left">
        <span title={row.status_label}>
          <Chip tone={status.tone}>{status.label}</Chip>
        </span>
      </td>

      <td
        className={cx(
          "px-3 py-2 text-right font-medium",
          (row.contribution ?? 0) >= 0 ? "text-ink-2" : "text-negative",
        )}
      >
        {formatMoney(row.contribution, country)}
      </td>

      <td className="px-3 py-2 text-right">
        {row.is_terminal ? (
          <>
            <span className="block text-ink-muted">
              {delivery === null ? "—" : formatNumber(delivery, country, 0)}
            </span>
            <span className="block text-[10.5px] text-ink-dim">
              {delivery === null ? "cerrada" : "en entregar"}
            </span>
          </>
        ) : (
          <>
            <span className={cx("block", TONE_TEXT[daysTone])}>
              {formatNumber(row.days_open, country, 0)}
            </span>
            <span className="block text-[10.5px] text-ink-dim">abierta</span>
          </>
        )}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Detail card
// ---------------------------------------------------------------------------

/**
 * Everything about one guide, in the order an operator asks for it.
 *
 * Identification, then who it was for, then where it went, then what it was,
 * then the money, then the history. Someone reading a customer their tracking
 * number stops at the first block; someone auditing a loss reads to the end.
 */
function OrderCard({
  shipmentId,
  country,
  piiVisible,
  onClose,
}: {
  shipmentId: string;
  country: Country;
  piiVisible: boolean;
  onClose: () => void;
}) {
  const { data, error, loading, reload } = useApi<OrderDetail>(`/orders/${shipmentId}`);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const order = data?.order ?? null;
  // The detail endpoint answers with its own `pii_visible`; the list's value is
  // only a stand-in until it arrives.
  const cardPii = data?.pii_visible ?? piiVisible;
  const contact = order ? contactLabel(order, cardPii) : null;
  const status = order ? statusGroupMeta(order.status_group) : null;
  const delivery = order ? daysToDeliver(order.created_date, order.delivered_at) : null;

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} aria-hidden />
      <aside
        className="fixed right-0 top-0 z-50 flex h-full w-full max-w-[520px] flex-col border-l border-line bg-sidebar"
        role="dialog"
        aria-label={order ? `Guía ${order.tracking_number}` : "Detalle de la guía"}
      >
        <header className="flex items-start justify-between gap-3 border-b border-line-subtle px-4 py-3.5">
          <div className="min-w-0">
            <h2 className="truncate text-[15px] font-bold">
              {order ? order.tracking_number : "Cargando…"}
            </h2>
            {order && (
              <div className="mt-1 flex items-center gap-2">
                {status && <Chip tone={status.tone}>{status.label}</Chip>}
                <span className="text-[11px] text-ink-dim">{order.status_label}</span>
              </div>
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

        <div className="flex-1 overflow-y-auto p-4">
          {loading && <SkeletonRows rows={10} />}

          {!loading && error && <ErrorState message={error.message} onRetry={reload} />}

          {!loading && !error && order && contact && (
            <div className="space-y-4">
              <Block title="La guía">
                <Line label="Número" value={order.tracking_number} mono />
                <Line
                  label="Guía transportadora"
                  value={order.carrier_tracking_number}
                  mono
                />
                <Line label="Transportadora" value={order.carrier_name} />
                <Line label="Despachada" value={formatDate(order.created_date, country)} />
                <Line
                  label="Entregada"
                  value={
                    order.delivered_at ? formatDate(order.delivered_at, country) : null
                  }
                />
                {order.is_terminal ? (
                  <Line
                    label="Tardó en entregarse"
                    value={
                      delivery === null
                        ? null
                        : delivery === 0
                          ? "el mismo día"
                          : pluralize(delivery, "día", "días")
                    }
                  />
                ) : (
                  <Line
                    label="Tiempo abierta"
                    value={
                      order.days_open === null
                        ? null
                        : `${pluralize(order.days_open, "día", "días")} y contando`
                    }
                  />
                )}
              </Block>

              <Block title="El cliente">
                {contact.hidden ? (
                  <>
                    <Line label="Código" value={order.customer_ref} mono />
                    <p className="pt-1 text-[11px] leading-relaxed text-ink-dim">
                      {PII_HIDDEN_NOTICE}
                    </p>
                  </>
                ) : (
                  <>
                    <Line label="Nombre" value={order.customer_name} />
                    <Line label="Teléfono" value={order.customer_phone} />
                    <Line label="Documento" value={order.customer_document} />
                    <Line label="Dirección" value={order.customer_address} />
                    <Line label="Código" value={order.customer_ref} mono />
                    {contactNotice([order], true) === "missing" && (
                      <p className="pt-1 text-[11px] leading-relaxed text-ink-dim">
                        {PII_MISSING_NOTICE}
                      </p>
                    )}
                  </>
                )}

                {order.customer_hash && (
                  <Link
                    href={`/${country.code.toLowerCase()}/customers?customer=${order.customer_hash}`}
                    className="mt-1 block text-[11.5px] font-semibold no-underline"
                  >
                    Ver todos los pedidos de este cliente →
                  </Link>
                )}
              </Block>

              <Block title="El destino">
                <Line label="Ciudad" value={order.city_name} />
                <Line label={country.geo_level1_label} value={order.province_name} />
              </Block>

              <Block title="El producto">
                <Line label="Producto" value={order.product_name} />
                <Line
                  label="Cantidad"
                  value={
                    order.quantity === null
                      ? null
                      : formatNumber(order.quantity, country, 0)
                  }
                />
              </Block>

              <MoneyBlock order={order} country={country} />

              <Timeline events={data?.timeline ?? []} country={country} />
            </div>
          )}
        </div>
      </aside>
    </>
  );
}

/**
 * The money, broken into the four things that ate it.
 *
 * Costs carry an explicit minus sign rather than being listed as positive
 * numbers under a heading called "costos": a column of positive figures that
 * subtract is how a reader talks themselves into a margin that is not there.
 */
function MoneyBlock({ order, country }: { order: OrderRow; country: Country }) {
  const contribution = order.contribution ?? 0;

  return (
    <Block title="El dinero">
      <Line label="Recaudo" value={formatMoney(order.revenue_amount, country)} />
      <Line label="Flete" value={negative(order.freight_amount, country)} />
      <Line label="Costo del producto" value={negative(order.cogs_amount, country)} />
      <Line label="Comisión" value={negative(order.fee_amount, country)} />

      <div className="mt-1 flex items-baseline justify-between gap-3 border-t border-line-subtle pt-2">
        <span className="text-[11.5px] font-semibold text-ink-2">Contribución</span>
        <span
          className={cx(
            "text-[15px] font-bold",
            contribution >= 0 ? "text-positive" : "text-negative",
          )}
        >
          {formatMoney(order.contribution, country)}
        </span>
      </div>

      {!order.is_terminal && (
        <p className="pt-1.5 text-[11px] leading-relaxed text-ink-dim">
          Esta guía sigue en la calle: el flete, el producto y la comisión ya se
          pagaron, pero el recaudo todavía no ha entrado. La contribución de arriba
          será la definitiva solo cuando la guía cierre.
        </p>
      )}
    </Block>
  );
}

/** A cost, written the way it behaves. */
function negative(
  value: number | null,
  country: Country,
): string {
  if (value === null || value === undefined) return "—";
  if (value === 0) return formatMoney(0, country);
  return `-${formatMoney(Math.abs(value), country)}`;
}

/**
 * The history of the guide, status and money on one thread.
 *
 * They are marked differently - a hollow ring for what happened to the parcel,
 * a filled dot and an amount for what happened to the money - but they are
 * never split into two lists. The operator wants to read that it was dispatched,
 * then the freight was charged, then it was delivered, then the collection came
 * in, in that order, the way it actually happened.
 */
function Timeline({
  events,
  country,
}: {
  events: TimelineEvent[];
  country: Country;
}) {
  const ordered = useMemo(
    () =>
      [...events].sort((a, b) => {
        const byDate = a.event_date.localeCompare(b.event_date);
        if (byDate !== 0) return byDate;
        // Same day: what happened to the parcel explains the money that moved.
        if (a.event_kind === b.event_kind) return 0;
        return a.event_kind === "estado" ? -1 : 1;
      }),
    [events],
  );

  if (ordered.length === 0) {
    return (
      <Block title="Trazabilidad">
        <p className="text-[11.5px] leading-relaxed text-ink-dim">
          Esta guía no tiene historial todavía. Aparecerá acá en cuanto llegue un
          reporte con sus movimientos de estado o de dinero.
        </p>
      </Block>
    );
  }

  return (
    <Block title="Trazabilidad">
      <ol className="space-y-0">
        {ordered.map((event, index) => {
          const isMoney = event.event_kind === "dinero";
          const amount = event.amount ?? 0;

          return (
            <li
              key={`${event.event_date}-${event.event_label}-${index}`}
              className="relative flex gap-3 pb-3 last:pb-0"
            >
              <div className="flex flex-col items-center">
                <span
                  className={cx(
                    "mt-[5px] size-[9px] shrink-0 rounded-full border",
                    isMoney
                      ? amount < 0
                        ? "border-negative bg-negative"
                        : "border-positive bg-positive"
                      : "border-ink-muted bg-transparent",
                  )}
                  aria-hidden
                />
                {index < ordered.length - 1 && (
                  <span className="mt-1 w-px flex-1 bg-line-input" aria-hidden />
                )}
              </div>

              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-[12px] text-ink-body">
                    {event.event_label}
                  </span>
                  {isMoney && (
                    <span
                      className={cx(
                        "shrink-0 text-[12px] font-semibold",
                        amount < 0 ? "text-negative" : "text-positive",
                      )}
                    >
                      {formatMoney(event.amount, country)}
                    </span>
                  )}
                </div>
                <p className="text-[10.5px] text-ink-dim">
                  {formatDate(event.event_date, country)}
                  <span className="mx-1.5">·</span>
                  {isMoney ? "movimiento de dinero" : "cambio de estado"}
                  {event.reference && (
                    <>
                      <span className="mx-1.5">·</span>
                      <span className="font-mono">{event.reference}</span>
                    </>
                  )}
                </p>
              </div>
            </li>
          );
        })}
      </ol>
    </Block>
  );
}

/**
 * Hold a value still until the typing stops.
 *
 * Local to this screen on purpose: it is the only one with a text box that
 * queries the server on every keystroke.
 */
function useDebounced<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setSettled(value), delayMs);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [value, delayMs]);

  return settled;
}
