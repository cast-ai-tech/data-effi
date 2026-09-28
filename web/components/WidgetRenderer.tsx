"use client";

/**
 * Renders one widget in whichever of its three states the API reported.
 *
 * A BLOCKED WIDGET IS NEVER HIDDEN. That is the whole point: it shows greyed
 * out, with a lock, naming the exact connector that is missing and offering a
 * button to go add it. A dashboard that silently omits what it cannot compute
 * teaches the operator that they are seeing everything, which is the single
 * most expensive lie a BI tool can tell.
 */

import Link from "next/link";
import { useMemo } from "react";

import { BasisDisclosure, useBasisReport } from "@/components/DateBasisNote";
import { WIDGET_REGISTRY } from "@/components/widgets/registry";
import { Card, cx } from "@/components/ui";
import { DateBasisScope } from "@/lib/date-range";
import type { Country, LayoutWidget } from "@/lib/types";
import { isUploadKind } from "@/lib/upload-memory";

const DOMAIN_LABELS: Record<string, string> = {
  shipments: "guías",
  movements: "movimientos de dinero",
  ads: "pauta",
  cs: "servicio al cliente",
  catalog: "catálogo de productos",
};

export function describeDomains(domains: string[]): string {
  return domains.map((domain) => DOMAIN_LABELS[domain] ?? domain).join(", ");
}

/**
 * Where to go to fill the gap a card names.
 *
 * Every domain but the catalogue arrives as a report on Cargar datos, which
 * opens on the right report type. `/settings` - where this used to point - is
 * not where either a report or a connection is added any more.
 */
export function fixFor(
  domains: readonly string[],
  countryCode: string,
): { href: string; label: string } {
  const first = domains.find((domain) => isUploadKind(domain));
  if (first) {
    return {
      href: `/${countryCode.toLowerCase()}/cargar?tipo=${first}`,
      label: `Cargar ${DOMAIN_LABELS[first] ?? first}`,
    };
  }
  return { href: "/connections", label: "Ir a Conexiones" };
}

/**
 * The degraded band's text.
 *
 * The generic message comes from SQL (migration 003) with the raw domain codes
 * in it - "Vista parcial: falta movements" - which reads as a bug to someone
 * who does not speak the schema. That one is rewritten with the screen's
 * words; a widget's own custom message is shown as it came.
 */
export function degradedMessage(widget: LayoutWidget): string | null {
  const message = widget.state_message;
  if (message?.startsWith("Vista parcial: falta ") && widget.missing_optional.length > 0) {
    const plural = widget.missing_optional.length > 1;
    return `Vista parcial: ${plural ? "faltan" : "falta"} ${describeDomains(
      widget.missing_optional,
    )}. Lo que ves es correcto, pero incompleto.`;
  }
  return message;
}

/**
 * The date range is NOT passed in. Every widget reads it from
 * `useRangedApi`, which takes it from the URL - so a widget added later cannot
 * forget to wire a prop and silently render unfiltered numbers next to
 * filtered ones.
 */
export function WidgetRenderer({
  widget,
  country,
}: {
  widget: LayoutWidget;
  country: Country;
}) {
  const Component = useMemo(() => WIDGET_REGISTRY[widget.widget_code], [widget.widget_code]);
  const { note, platformNote, onBasis, onPlatform } = useBasisReport();

  if (widget.state === "blocked") {
    return <BlockedWidget widget={widget} countryCode={country.code} />;
  }

  if (!Component) {
    // A widget the backend knows about and this build does not. Say so rather
    // than rendering nothing, so the gap is visible instead of invisible.
    return (
      <Card title={widget.title} subtitle={widget.description}>
        <p className="py-6 text-center text-sm text-ink-dim">
          Este widget existe en el servidor pero no en esta versión de la interfaz
          (<code className="text-ink-muted">{widget.widget_code}</code>).
        </p>
      </Card>
    );
  }

  // A degraded widget can also be one that ignores the range, so two bands can
  // stack; only the topmost one rounds off the corner.
  const degraded = Boolean(widget.state === "degraded" && widget.state_message);
  const degradedText = degraded ? degradedMessage(widget) : null;
  // Missing data that a report would fill gets the way to load it right there.
  const degradedFix =
    degraded && widget.missing_optional.some((domain) => isUploadKind(domain))
      ? fixFor(widget.missing_optional, country.code)
      : null;

  return (
    <div className="relative">
      {degraded && (
        <div
          className="flex items-start gap-2 rounded-t-card border border-b-0 border-warning/25 bg-warning/[0.08] px-4 py-2"
          role="status"
        >
          <LockIcon className="mt-[1px] size-3.5 shrink-0 text-warning-ink" variant="warning" />
          <p className="text-sm leading-snug text-warning-ink">
            {degradedText}
            {degradedFix && (
              <>
                {" "}
                <Link
                  href={degradedFix.href}
                  className="font-semibold text-warning-ink underline underline-offset-2"
                >
                  {degradedFix.label}
                </Link>
              </>
            )}
          </p>
        </div>
      )}
      <div className={cx(degraded && "[&>section]:rounded-t-none")}>
        <BasisDisclosure note={note} platformNote={platformNote} rounded={!degraded}>
          <DateBasisScope onBasis={onBasis} onPlatform={onPlatform}>
            <Component
              countryCode={country.code}
              country={country}
              state={widget.state}
              message={widget.state_message}
            />
          </DateBasisScope>
        </BasisDisclosure>
      </div>
    </div>
  );
}

/**
 * The blocked state: greyed, blurred sample content, a lock, the reason, and a
 * way out. Never a hidden element.
 */
function BlockedWidget({ widget, countryCode }: { widget: LayoutWidget; countryCode: string }) {
  const missing = describeDomains(widget.missing_required);
  const fix = fixFor(widget.missing_required, countryCode);

  return (
    <section
      className="relative overflow-hidden rounded-card border border-line bg-surface"
      data-widget-state="blocked"
      data-widget-code={widget.widget_code}
      aria-label={`${widget.title} (bloqueado)`}
    >
      <header className="border-b border-line-subtle px-4 py-3">
        <h3 className="flex items-center gap-2 text-base font-semibold text-ink-dim">
          <LockIcon className="size-3.5" />
          {widget.title}
        </h3>
      </header>

      {/* Blurred placeholder so the shape of what you are missing is visible.
          Only the placeholder is hidden from assistive technology: the reason
          and the link over it are the part that must be read. */}
      <div className="relative min-h-[168px] p-4">
        <div className="pointer-events-none select-none space-y-3 opacity-40 blur-[3px]" aria-hidden>
          <div className="flex items-end gap-2">
            {[46, 72, 38, 84, 60, 52, 76].map((height, index) => (
              <div
                key={index}
                className="flex-1 rounded-sm bg-ink-dim"
                style={{ height: `${height}px` }}
              />
            ))}
          </div>
          <div className="h-3 w-2/3 rounded bg-ink-dim" />
          <div className="h-3 w-1/2 rounded bg-ink-dim" />
        </div>

        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-page/[0.72] px-6 text-center">
          <LockIcon className="size-5 text-ink-muted" />
          <p className="max-w-xs text-base leading-relaxed text-ink-2">
            {widget.state_message ??
              `Falta conectar ${missing} para ver este widget.`}
          </p>
          <Link
            href={fix.href}
            className="rounded-control bg-accent px-3.5 py-1.5 text-sm font-semibold text-on-accent no-underline hover:bg-accent-hover"
          >
            {fix.label}
          </Link>
        </div>
      </div>
    </section>
  );
}

export function LockIcon({
  className,
  variant = "muted",
}: {
  className?: string;
  variant?: "muted" | "warning";
}) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      className={cx(className, variant === "warning" ? "text-warning-ink" : "")}
      aria-hidden
    >
      <rect
        x="3.2"
        y="7"
        width="9.6"
        height="6.5"
        rx="1.6"
        stroke="currentColor"
        strokeWidth="1.3"
      />
      <path
        d="M5.6 7V5.2a2.4 2.4 0 0 1 4.8 0V7"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
      />
    </svg>
  );
}
