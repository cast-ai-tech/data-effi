"use client";

/**
 * Conexiones: the whole integration catalogue, and what is already wired up.
 *
 * The catalogue lists platforms that do NOT work yet, greyed out, with what
 * they will need. That is deliberate and it matches how blocked widgets
 * behave: showing what is missing beats pretending the list is shorter.
 *
 * Nothing on this screen ever touches a credential. A connection stores the
 * NAME of the environment variable the server reads at sync time, and the
 * webhook token is displayed once and kept only as a SHA-256.
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { ConfirmDetail } from "@/components/ConfirmDialog";
import { Button, Chip, Drawer, EmptyState, ErrorState, SectionTitle, SkeletonRows, StatusDot, cx } from "@/components/ui";
import { TIER_LABELS } from "@/lib/glossary";
import { API_URL, ApiError, api } from "@/lib/api";
import { countryFlag, formatRelative } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type {
  Connection,
  Country,
  Platform,
  PlatformCategory,
  User,
  WebhookKind,
  WebhookSecret,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Vocabulary
// ---------------------------------------------------------------------------

/** Shelf order. Money-in first, plumbing last. */
const CATEGORY_ORDER: PlatformCategory[] = [
  "pauta",
  "tienda",
  "crm",
  "fulfillment",
  "automatizacion",
  "archivos",
  "analitica",
  "otros",
];

const CATEGORY_LABELS: Record<PlatformCategory, string> = {
  pauta: "Pauta",
  tienda: "Tiendas",
  crm: "CRM y servicio",
  fulfillment: "Fulfillment",
  automatizacion: "Automatización",
  archivos: "Archivos",
  analitica: "Analítica",
  otros: "Otros",
};

const CATEGORY_HINTS: Record<PlatformCategory, string> = {
  pauta: "cuánto gastaste y en qué",
  tienda: "pedidos y catálogo",
  crm: "conversaciones y confirmación",
  fulfillment: "guías y plata en la calle",
  automatizacion: "lo que te manda otra herramienta",
  archivos: "reportes que subes o publicas",
  analitica: "métricas de terceros",
  otros: "todo lo demás",
};

/** The domains in the words an operator uses, not the column names. */
const DOMAIN_LABELS: Record<string, string> = {
  shipments: "guías",
  movements: "movimientos de dinero",
  ads: "pauta",
  cs: "servicio al cliente",
  catalog: "catálogo",
};

/** What a webhook connection can be told to assume, in the operator's words. */
const WEBHOOK_KINDS: Array<{ value: WebhookKind; label: string }> = [
  { value: "shipments", label: "Guías" },
  { value: "movements", label: "Movimientos de dinero" },
  { value: "ads", label: "Pauta" },
  { value: "cs", label: "Servicio al cliente" },
];


const HEALTH_LABELS: Record<Connection["health"], string> = {
  ok: "al día",
  stale: "sin sincronizar",
  error: "con error",
  never_synced: "nunca sincronizó",
  disabled: "desactivada",
};

type Tone = "neutral" | "accent" | "positive" | "warning" | "negative";

function healthTone(health: Connection["health"]): Tone {
  if (health === "ok") return "positive";
  if (health === "error") return "negative";
  if (health === "disabled") return "neutral";
  return "warning";
}

/** "guías, pauta y catálogo" - a list a person would actually say out loud. */
function joinSpanish(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} y ${items[items.length - 1]}`;
}

function domainLabel(domain: string): string {
  return DOMAIN_LABELS[domain] ?? domain;
}

/** Credentials that live in an env var; `file` and `none` need nothing. */
function needsSecretRef(platform: Platform): boolean {
  return (
    platform.auth_type === "api_key" ||
    platform.auth_type === "oauth2" ||
    platform.auth_type === "session"
  );
}

function defaultConnectionName(platform: Platform, countryCode: string): string {
  return platform.scope === "country" && countryCode
    ? `${platform.platform_name} ${countryCode}`
    : platform.platform_name;
}

/** A revealed webhook, held only in React state and never refetched. */
interface WebhookReveal {
  connectionName: string;
  secret: WebhookSecret;
}

/**
 * Something destructive, waiting for a deliberate yes.
 *
 * Both of these reach outside Master Data - deleting a connection stops a feed,
 * revoking a webhook breaks whatever automation is posting to it - so neither
 * runs until the operator confirms in the dialog.
 */
type PendingAction =
  | { kind: "delete"; connection: Connection }
  | { kind: "revoke"; connection: Connection };

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export default function ConnectionsPage() {
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState<Platform | null>(null);
  const [reveal, setReveal] = useState<WebhookReveal | null>(null);
  const [confirming, setConfirming] = useState<PendingAction | null>(null);
  const [actionPending, setActionPending] = useState(false);

  const catalogue = useApi<Platform[]>("/config/platforms");
  const connections = useApi<Connection[]>("/config/connections");
  const countries = useApi<Country[]>("/config/countries");
  const me = useApi<User>("/auth/me");

  // Creating and revoking a webhook token are owner-only on the API. Showing
  // those buttons to an analyst would be offering a door that always slams.
  const isOwner = me.data?.role === "owner";

  const activeCountries = useMemo(
    () => (countries.data ?? []).filter((country) => country.is_active),
    [countries.data],
  );

  const byPlatform = useMemo(() => {
    const map = new Map<string, Connection[]>();
    for (const connection of connections.data ?? []) {
      const bucket = map.get(connection.platform_code);
      if (bucket) bucket.push(connection);
      else map.set(connection.platform_code, [connection]);
    }
    return map;
  }, [connections.data]);

  const sections = useMemo(() => {
    const buckets = new Map<PlatformCategory, Platform[]>();
    for (const platform of catalogue.data ?? []) {
      const bucket = buckets.get(platform.category);
      if (bucket) bucket.push(platform);
      else buckets.set(platform.category, [platform]);
    }
    return CATEGORY_ORDER.flatMap((category) => {
      const items = buckets.get(category);
      return items ? [{ category, items }] : [];
    });
  }, [catalogue.data]);

  // A connection whose platform left the catalogue would otherwise vanish from
  // the screen while still pulling data. Better to show it than to hide it.
  const orphans = useMemo(() => {
    if (!catalogue.data) return [];
    const known = new Set(catalogue.data.map((platform) => platform.platform_code));
    return (connections.data ?? []).filter(
      (connection) => !known.has(connection.platform_code),
    );
  }, [catalogue.data, connections.data]);

  const loading = catalogue.loading || connections.loading;
  const loadError = catalogue.error ?? connections.error;

  function reload() {
    catalogue.reload();
    connections.reload();
  }

  function requestDelete(connection: Connection) {
    setConfirming({ kind: "delete", connection });
  }

  function requestRevoke(connection: Connection) {
    setConfirming({ kind: "revoke", connection });
  }

  /** Runs whichever action the dialog was opened for. */
  async function runConfirmed() {
    if (!confirming) return;
    const { kind, connection } = confirming;
    const path =
      kind === "delete"
        ? `/config/connections/${connection.connection_id}`
        : `/config/connections/${connection.connection_id}/webhook`;

    setActionPending(true);
    try {
      await api.delete(path);
      setError(null);
      setConfirming(null);
      reload();
    } catch (err) {
      // Close and surface it in the page banner: an error trapped inside a
      // modal is an error the operator has to dismiss before they can read it.
      setConfirming(null);
      setError(
        err instanceof ApiError
          ? err.message
          : kind === "delete"
            ? "No se pudo eliminar la conexión"
            : "No se pudo revocar el webhook",
      );
    } finally {
      setActionPending(false);
    }
  }

  async function createWebhook(connection: Connection) {
    try {
      const secret = await api.post<WebhookSecret>(
        `/config/connections/${connection.connection_id}/webhook`,
      );
      setError(null);
      setReveal({ connectionName: connection.connection_name, secret });
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo generar el webhook");
    }
  }

  return (
    <AppShell>
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight">Conexiones</h1>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-dim">
            De dónde salen los números. Master Data guarda el <b>nombre</b> de la variable
            donde vive cada credencial, nunca la credencial.
          </p>
        </div>
        <Link
          href="/settings"
          className="inline-flex shrink-0 items-center gap-1.5 rounded-control border border-line-input px-3 py-1.5 text-sm font-semibold text-ink-2 no-underline transition-colors hover:border-accent hover:text-accent-ink"
        >
          Configuración
        </Link>
      </header>

      {error && (
        <p className="mb-4 rounded-control border border-negative/30 bg-negative/[0.08] px-3 py-2 text-sm text-negative-ink">
          {error}
        </p>
      )}

      {loading && <SkeletonRows rows={8} />}

      {!loading && loadError && (
        <ErrorState
          message={
            loadError instanceof ApiError
              ? loadError.message
              : "El catálogo de integraciones no respondió."
          }
          onRetry={reload}
        />
      )}

      {!loading && !loadError && sections.length === 0 && (
        <EmptyState
          title="El catálogo llegó vacío"
          instruction="Ninguna integración está publicada para tu espacio. Vuelve a cargar; si sigue vacío, avísale a quien administra Master Data."
          action={
            <Button size="sm" variant="ghost" onClick={reload}>
              Reintentar
            </Button>
          }
        />
      )}

      {!loading && !loadError && sections.length > 0 && (
        <div className="space-y-7">
          {sections.map(({ category, items }) => (
            <section key={category}>
              <SectionTitle hint={CATEGORY_HINTS[category]}>
                {CATEGORY_LABELS[category]}
              </SectionTitle>
              <div className="grid items-stretch gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {items.map((platform) => (
                  <PlatformCard
                    key={platform.platform_code}
                    platform={platform}
                    connections={byPlatform.get(platform.platform_code) ?? []}
                    isOwner={isOwner}
                    onConnect={() => setConnecting(platform)}
                    onDelete={requestDelete}
                    onCreateWebhook={createWebhook}
                    onRevokeWebhook={requestRevoke}
                  />
                ))}
              </div>
            </section>
          ))}

          {orphans.length > 0 && (
            <section>
              <SectionTitle hint="su plataforma ya no está en el catálogo">
                Fuera del catálogo
              </SectionTitle>
              <div className="rounded-card border border-warning/30 bg-warning/[0.05] p-4">
                <p className="mb-3 text-sm leading-relaxed text-warning-ink">
                  Estas conexiones siguen activas pero su plataforma ya no aparece en el
                  catálogo. Revisa si todavía las necesitas.
                </p>
                <div className="space-y-2.5">
                  {orphans.map((connection) => (
                    <ConnectionRow
                      key={connection.connection_id}
                      connection={connection}
                      webhookCapable={connection.has_webhook}
                      isOwner={isOwner}
                      onDelete={requestDelete}
                      onCreateWebhook={createWebhook}
                      onRevokeWebhook={requestRevoke}
                    />
                  ))}
                </div>
              </div>
            </section>
          )}
        </div>
      )}

      {connecting && (
        <ConnectPanel
          platform={connecting}
          countries={activeCountries}
          isOwner={isOwner}
          countriesLoading={countries.loading}
          countriesError={countries.error}
          onRetryCountries={countries.reload}
          onClose={() => setConnecting(null)}
          onCreated={() => {
            setConnecting(null);
            reload();
          }}
          onRevealWebhook={setReveal}
        />
      )}

      {confirming && (
        <ConfirmDialog
          title={
            confirming.kind === "delete"
              ? "Eliminar esta conexión"
              : "Revocar la URL del webhook"
          }
          consequence={
            confirming.kind === "delete"
              ? "No se puede deshacer. Para volver a leer esta fuente tendrás que crear la conexión otra vez."
              : "Se rompe cualquier automatización tuya que esté enviando datos a esa URL, en n8n, Make, Zapier o donde la hayas pegado. La URL no se puede recuperar: tendrás que generar una nueva y volver a pegarla en cada una."
          }
          details={connectionDetails(confirming.connection, activeCountries)}
          confirmLabel={
            confirming.kind === "delete" ? "Eliminar conexión" : "Revocar URL"
          }
          pending={actionPending}
          onConfirm={runConfirmed}
          onCancel={() => setConfirming(null)}
        >
          {confirming.kind === "delete" ? (
            <p>
              Master Data dejará de leer esta fuente. Los datos que ya cargó se quedan
              donde están: los tableros siguen mostrando lo que llegó hasta hoy, pero
              no entrará nada nuevo.
            </p>
          ) : (
            <p>
              La URL deja de recibir datos en el momento en que confirmes. La conexión
              se queda: podrás generar una URL nueva desde su tarjeta.
            </p>
          )}
        </ConfirmDialog>
      )}

      {reveal && <WebhookPanel reveal={reveal} onClose={() => setReveal(null)} />}
    </AppShell>
  );
}

/** The facts that identify a connection in a confirmation dialog. */
function connectionDetails(
  connection: Connection,
  countries: Country[],
): ConfirmDetail[] {
  // The country's real name, never its ISO code: "EC" is an identifier the
  // system uses, not a word an operator reads. Taken from core.country rather
  // than a table in the frontend, so a new country needs no code change.
  const country = countries.find((c) => c.code === connection.country_code);
  return [
    { label: "Conexión", value: connection.connection_name },
    { label: "Plataforma", value: connection.platform_name },
    {
      label: "Alcance",
      value: connection.country_code
        ? `${countryFlag(connection.country_code)} ${country?.name ?? connection.country_code}`
        : "🌐 Global · todos los países",
    },
    {
      label: "Última carga",
      value: connection.last_sync_at
        ? formatRelative(connection.last_sync_at)
        : "nunca sincronizó",
    },
  ];
}

// ---------------------------------------------------------------------------
// One platform, one card
// ---------------------------------------------------------------------------

function PlatformCard({
  platform,
  connections,
  isOwner,
  onConnect,
  onDelete,
  onCreateWebhook,
  onRevokeWebhook,
}: {
  platform: Platform;
  connections: Connection[];
  isOwner: boolean;
  onConnect: () => void;
  onDelete: (connection: Connection) => void;
  onCreateWebhook: (connection: Connection) => void;
  onRevokeWebhook: (connection: Connection) => void;
}) {
  const planned = platform.availability === "planned";
  const domains = platform.data_domains.map(domainLabel);

  return (
    <article
      className={cx(
        "flex h-full flex-col rounded-card border bg-surface p-4",
        planned ? "border-line-subtle opacity-60" : "border-line",
      )}
    >
      <h3 className="text-md font-semibold leading-tight text-ink">
        {platform.platform_name}
      </h3>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <Chip tone={platform.tier === 3 ? "warning" : "neutral"}>
          {TIER_LABELS[platform.tier] ?? `Tipo ${platform.tier}`}
        </Chip>
        {platform.scope === "global" && <Chip>Global</Chip>}
        {platform.availability === "beta" && <Chip tone="warning">Beta</Chip>}
        {planned && <Chip>Próximamente</Chip>}
        {platform.direction === "out" && <Chip>Salida</Chip>}
      </div>

      <p className="mt-2.5 text-sm leading-relaxed text-ink-muted">
        {domains.length > 0 ? (
          <>
            Aporta <span className="text-ink-2">{joinSpanish(domains)}</span>.
          </>
        ) : (
          <>No trae datos: se lleva los tuyos hacia afuera.</>
        )}
      </p>

      {platform.scope === "global" && (
        <p className="mt-1.5 text-xs leading-relaxed text-ink-dim">
          Sirve para todos los países a la vez: cada carga declara de qué país es.
        </p>
      )}

      {platform.setup_hint && (
        <p className="mt-2 text-sm leading-relaxed text-ink-dim">
          {platform.setup_hint}
        </p>
      )}

      {platform.docs_url && (
        <a
          href={platform.docs_url}
          target="_blank"
          rel="noreferrer"
          className="mt-1.5 text-xs"
        >
          Cómo se configura
        </a>
      )}

      {connections.length > 0 && (
        <div className="mt-3 space-y-2.5 border-t border-line-row pt-3">
          {connections.map((connection) => (
            <ConnectionRow
              key={connection.connection_id}
              connection={connection}
              webhookCapable={platform.auth_type === "webhook"}
              isOwner={isOwner}
              onDelete={onDelete}
              onCreateWebhook={onCreateWebhook}
              onRevokeWebhook={onRevokeWebhook}
            />
          ))}
        </div>
      )}

      <div
        className={cx(
          "mt-auto flex items-center gap-2 pt-3.5",
          planned ? "justify-between" : "justify-end",
        )}
      >
        {planned && (
          <span className="text-xs text-ink-faint">Todavía no se puede conectar</span>
        )}
        <Button
          size="sm"
          variant={connections.length > 0 ? "ghost" : "primary"}
          disabled={planned}
          onClick={onConnect}
        >
          {connections.length > 0 ? "Añadir otra" : "Conectar"}
        </Button>
      </div>
    </article>
  );
}

function ConnectionRow({
  connection,
  webhookCapable,
  isOwner,
  onDelete,
  onCreateWebhook,
  onRevokeWebhook,
}: {
  connection: Connection;
  webhookCapable: boolean;
  isOwner: boolean;
  onDelete: (connection: Connection) => void;
  onCreateWebhook: (connection: Connection) => void;
  onRevokeWebhook: (connection: Connection) => void;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink">
          {countryFlag(connection.country_code)} {connection.connection_name}
        </p>
        <p className="mt-0.5 flex items-center gap-1.5 text-xs text-ink-dim">
          <StatusDot tone={healthTone(connection.health)} />
          {HEALTH_LABELS[connection.health]}
          {connection.last_sync_at ? ` · ${formatRelative(connection.last_sync_at)}` : ""}
        </p>
        {connection.last_error && (
          <p className="mt-1 text-xs leading-relaxed text-negative-ink">
            {connection.last_error}
          </p>
        )}
        {connection.consent_granted_at && (
          <p className="mt-1 text-xs text-ink-faint">
            Consentimiento otorgado {formatRelative(connection.consent_granted_at)}
          </p>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        {webhookCapable &&
          (isOwner ? (
            connection.has_webhook ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onRevokeWebhook(connection)}
              >
                Revocar URL
              </Button>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onCreateWebhook(connection)}
              >
                Generar URL
              </Button>
            )
          ) : (
            !connection.has_webhook && (
              <span className="text-xs text-warning-ink">
                Falta la URL: pídesela al dueño del espacio
              </span>
            )
          ))}
        <Button size="sm" variant="danger" onClick={() => onDelete(connection)}>
          Eliminar
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The slide-over both flows share
// ---------------------------------------------------------------------------

function SidePanel({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <Drawer onClose={onClose} label={title} title={title} subtitle={subtitle}>
        {children}
      </Drawer>
    </>
  );
}

// ---------------------------------------------------------------------------
// Connect flow
// ---------------------------------------------------------------------------

const FIELD_CLASS =
  "w-full rounded-control border border-line-input bg-surface px-3 py-2 text-base text-ink focus:border-accent focus:outline-none";

function ConnectPanel({
  platform,
  countries,
  isOwner,
  countriesLoading,
  countriesError,
  onRetryCountries,
  onClose,
  onCreated,
  onRevealWebhook,
}: {
  platform: Platform;
  countries: Country[];
  isOwner: boolean;
  countriesLoading: boolean;
  countriesError: Error | null;
  onRetryCountries: () => void;
  onClose: () => void;
  onCreated: () => void;
  onRevealWebhook: (reveal: WebhookReveal) => void;
}) {
  // A country platform is only offered where it actually operates.
  const eligible = useMemo(() => {
    if (platform.scope !== "country") return [];
    if (platform.available_countries.length === 0) return countries;
    return countries.filter((country) =>
      platform.available_countries.includes(country.code),
    );
  }, [platform, countries]);

  const [countryCode, setCountryCode] = useState(eligible[0]?.code ?? "");
  const [name, setName] = useState(() =>
    defaultConnectionName(platform, eligible[0]?.code ?? ""),
  );
  const [secretRef, setSecretRef] = useState("");
  const [defaultKind, setDefaultKind] = useState<WebhookKind | "">("");
  const [consent, setConsent] = useState(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const wantsSecret = needsSecretRef(platform);
  const isWebhook = platform.auth_type === "webhook";
  // Only a country platform cares; a global one never reads the list.
  const waitingForCountries = platform.scope === "country" && countriesLoading;
  const countriesFailed = platform.scope === "country" && Boolean(countriesError);
  const missingCountry =
    platform.scope === "country" &&
    !countriesLoading &&
    !countriesError &&
    eligible.length === 0;

  // The panel can open before /config/countries lands, in which case the initial
  // state above had nothing to pick. Adopt the first country the moment it
  // arrives, or the select would render options while the value stayed empty.
  useEffect(() => {
    if (platform.scope !== "country" || countryCode) return;
    const first = eligible[0]?.code;
    if (!first) return;
    setCountryCode(first);
    setName((current) =>
      current === defaultConnectionName(platform, "")
        ? defaultConnectionName(platform, first)
        : current,
    );
  }, [platform, countryCode, eligible]);

  /** Keep the default name in step with the country until the operator edits it. */
  function changeCountry(next: string) {
    if (name === defaultConnectionName(platform, countryCode)) {
      setName(defaultConnectionName(platform, next));
    }
    setCountryCode(next);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setFailure(null);

    const body: Record<string, unknown> = {
      platform_code: platform.platform_code,
      name: name.trim() || defaultConnectionName(platform, countryCode),
      consent_granted: consent,
    };
    // Omitted entirely for a global platform: the trigger in migration 012
    // rejects a global connection that carries a country.
    if (platform.scope === "country") body.country_code = countryCode;
    if (wantsSecret) body.secret_ref = secretRef.trim() || null;

    try {
      const created = await api.post<Connection>("/config/connections", body);

      if (isWebhook && isOwner && created?.connection_id) {
        // With a default_kind the automation can post just `{"rows": [...]}`;
        // without one, every call must name its own kind or the API 400s.
        const query = defaultKind ? `?default_kind=${defaultKind}` : "";
        const secret = await api.post<WebhookSecret>(
          `/config/connections/${created.connection_id}/webhook${query}`,
        );
        onRevealWebhook({ connectionName: created.connection_name, secret });
      }
      onCreated();
    } catch (err) {
      setFailure(
        err instanceof ApiError ? err.message : "No se pudo crear la conexión",
      );
      setPending(false);
    }
  }

  return (
    <SidePanel
      title={`Conectar ${platform.platform_name}`}
      subtitle={CATEGORY_LABELS[platform.category]}
      onClose={onClose}
    >
      {waitingForCountries ? (
        <SkeletonRows rows={4} />
      ) : countriesFailed ? (
        <ErrorState
          message={
            countriesError instanceof ApiError
              ? countriesError.message
              : "No se pudo leer la lista de países."
          }
          onRetry={onRetryCountries}
        />
      ) : missingCountry ? (
        <EmptyState
          title="No hay países donde conectarla"
          instruction={
            platform.available_countries.length > 0
              ? `${platform.platform_name} solo opera en ${platform.available_countries.join(", ")}. Activa uno de esos países en Configuración y vuelve.`
              : "Activa al menos un país en Configuración y vuelve a intentarlo."
          }
          action={
            <Link
              href="/settings"
              className="inline-flex items-center rounded-control border border-line-input px-3 py-1.5 text-sm font-semibold text-ink-2 no-underline hover:border-accent hover:text-accent-ink"
            >
              Ir a Configuración
            </Link>
          }
        />
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {failure && (
            <p className="rounded-control border border-negative/30 bg-negative/[0.08] px-3 py-2 text-sm text-negative-ink">
              {failure}
            </p>
          )}

          {platform.scope === "country" ? (
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-ink-muted">
                País
              </span>
              <select
                value={countryCode}
                onChange={(event) => changeCountry(event.target.value)}
                className={FIELD_CLASS}
              >
                {eligible.map((country) => (
                  <option key={country.code} value={country.code}>
                    {country.name}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs leading-relaxed text-ink-dim">
                Cada cuenta pertenece a una sola operación. Si manejas dos países, crea
                una conexión por país.
              </p>
            </label>
          ) : (
            <p className="rounded-control border border-line bg-sunken px-3 py-2.5 text-sm leading-relaxed text-ink-2">
              Es una conexión <b>global</b>: no se ata a un país porque cada carga
              declara de qué país es.
            </p>
          )}

          <label className="block">
            <span className="mb-1 block text-sm font-medium text-ink-muted">
              Nombre
            </span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={defaultConnectionName(platform, countryCode)}
              className={FIELD_CLASS}
            />
            <p className="mt-1 text-xs text-ink-dim">
              Como lo vas a reconocer tú en la lista de fuentes.
            </p>
          </label>

          {wantsSecret && (
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-ink-muted">
                Variable de entorno con la credencial
              </span>
              <input
                value={secretRef}
                onChange={(event) => setSecretRef(event.target.value)}
                placeholder="EFFI_API_KEY"
                autoComplete="off"
                spellCheck={false}
                className={`${FIELD_CLASS} font-mono`}
              />
              <p className="mt-1 text-xs leading-relaxed text-ink-dim">
                Escribe el <b>nombre</b> de la variable, no la clave. Master Data nunca
                guarda credenciales: cada vez que sincroniza las lee del servidor usando
                este nombre. Si lo dejas vacío lo puedes llenar después.
              </p>
            </label>
          )}

          {isWebhook && isOwner && (
            <label className="block">
              <span className="mb-1 block text-sm font-medium text-ink-muted">
                Qué va a enviar (opcional)
              </span>
              <select
                value={defaultKind}
                onChange={(event) =>
                  setDefaultKind(event.target.value as WebhookKind | "")
                }
                className={FIELD_CLASS}
              >
                <option value="">Lo dirá cada envío</option>
                {WEBHOOK_KINDS.map((kind) => (
                  <option key={kind.value} value={kind.value}>
                    {kind.label}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs leading-relaxed text-ink-dim">
                Si eliges uno, tu automatización puede mandar solo las filas. Si lo dejas
                en «lo dirá cada envío», cada llamada tiene que decir de qué tipo es o
                Master Data la rechaza.
              </p>
            </label>
          )}

          {isWebhook &&
            (isOwner ? (
              <div className="rounded-control border border-line bg-sunken px-3 py-2.5 text-sm leading-relaxed text-ink-2">
                Al crearla, Master Data te dará una URL con un token adentro. Se muestra{" "}
                <b>una sola vez</b> y no hay forma de volver a verla: ten a mano dónde
                pegarla (n8n, Make o Zapier) antes de continuar.
              </div>
            ) : (
              <div className="rounded-control border border-warning/30 bg-warning/[0.07] px-3 py-2.5 text-sm leading-relaxed text-warning-ink">
                Puedes crear la conexión, pero la URL de envío solo la puede generar el
                dueño del espacio. Quedará lista y él la genera desde la tarjeta.
              </div>
            ))}

          {platform.requires_consent && (
            <label className="flex items-start gap-2.5 rounded-control border border-warning/30 bg-warning/[0.07] p-3">
              <input
                type="checkbox"
                checked={consent}
                onChange={(event) => setConsent(event.target.checked)}
                className="mt-0.5"
              />
              <span className="text-sm leading-relaxed text-warning-ink">
                Entiendo que Master Data entrará a {platform.platform_name} <b>con mi usuario
                y contraseña</b>, que esa plataforma podría suspender mi cuenta por eso, y que
                la responsabilidad es mía.
              </span>
            </label>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" size="sm" variant="ghost" onClick={onClose}>
              Cancelar
            </Button>
            <Button
              type="submit"
              size="sm"
              disabled={
                pending ||
                (platform.scope === "country" && !countryCode) ||
                (platform.requires_consent && !consent)
              }
            >
              {pending ? "Creando…" : "Crear conexión"}
            </Button>
          </div>
        </form>
      )}
    </SidePanel>
  );
}

// ---------------------------------------------------------------------------
// The token, shown once
// ---------------------------------------------------------------------------

/**
 * Does the issued URL live somewhere other than the API we talk to?
 *
 * The API builds `url` from PUBLIC_API_URL, falling back to the request host
 * when that is unset. So a mismatch here means one of two things: the two
 * configured origins disagree, or a proxy without X-Forwarded-Host handed back
 * an internal address. We cannot tell which from the browser, and we cannot fix
 * either - but a token shown once must not be discovered to be wrong later.
 */
function looksLikeMismatchedHost(webhookUrl: string): boolean {
  try {
    return new URL(webhookUrl).origin !== new URL(API_URL).origin;
  } catch {
    return false;
  }
}

function WebhookPanel({
  reveal,
  onClose,
}: {
  reveal: WebhookReveal;
  onClose: () => void;
}) {
  const kind = reveal.secret.default_kind;
  const kindLabel = WEBHOOK_KINDS.find((item) => item.value === kind)?.label;

  // With a default_kind the automation may omit it entirely.
  const bodyExample = kind
    ? `{
  "rows": [
    {
      "guia": "1234567890",
      "fecha": "2026-08-19",
      "estado": "Entregado",
      "ciudad": "Medellín",
      "transportadora": "Envía",
      "valor": 189900
    }
  ]
}`
    : `{
  "kind": "shipments",
  "rows": [
    {
      "guia": "1234567890",
      "fecha": "2026-08-19",
      "estado": "Entregado",
      "ciudad": "Medellín",
      "transportadora": "Envía",
      "valor": 189900
    }
  ]
}`;

  return (
    <SidePanel title="Webhook listo" subtitle={reveal.connectionName} onClose={onClose}>
      <div className="space-y-4">
        <p className="rounded-control border border-warning/30 bg-warning/[0.07] px-3 py-2.5 text-sm leading-relaxed text-warning-ink">
          Esta URL se muestra <b>una sola vez</b>. Master Data guarda únicamente su huella,
          así que no existe ninguna pantalla donde volver a verla. Cópiala ahora. Si la
          pierdes tendrás que revocarla y generar otra, y eso <b>rompe</b> la
          automatización que estuviera usando la anterior.
        </p>

        {/* Copy written by the API. Shown as it comes. */}
        {reveal.secret.message && (
          <p className="text-sm leading-relaxed text-ink-2">
            {reveal.secret.message}
          </p>
        )}

        <CopyField label="URL de envío" value={reveal.secret.url} />
        <p className="-mt-1.5 text-xs leading-relaxed text-ink-dim">
          El token va dentro de la URL, así que la URL <b>es</b> la contraseña. Trátala
          como tal: no la pegues en un chat público ni en un repositorio.
        </p>

        {looksLikeMismatchedHost(reveal.secret.url) && (
          <p className="rounded-control border border-warning/30 bg-warning/[0.07] px-3 py-2.5 text-sm leading-relaxed text-warning-ink">
            Ojo: esta dirección no coincide con la dirección pública configurada de Data
            Effi. Casi siempre es que las dos configuraciones no están de acuerdo.
            Pruébala antes de guardarla en tu automatización; si no responde, avísale a
            quien instaló Master Data.
          </p>
        )}

        <CopyField label="Token" value={reveal.secret.token} />
        <p className="-mt-1.5 text-xs leading-relaxed text-ink-dim">
          Es el mismo token que ya viene en la URL. Está aparte por si tu herramienta lo
          pide en un campo propio.
        </p>

        <div className="rounded-control border border-line bg-sunken p-3.5">
          <h3 className="text-sm font-semibold text-ink">
            Cómo usarlo en n8n, Make o Zapier
          </h3>
          <ol className="mt-2 list-decimal space-y-1.5 pl-4 text-sm leading-relaxed text-ink-2">
            <li>
              Agrega un paso que haga una petición HTTP: en n8n es <b>HTTP Request</b>, en
              Make <b>HTTP · Make a request</b>, en Zapier{" "}
              <b>Webhooks by Zapier · POST</b>.
            </li>
            <li>
              Método <b>POST</b> y, como dirección, la URL de arriba tal cual. No hace
              falta configurar ninguna cabecera de autenticación: el token ya viaja en la
              dirección.
            </li>
            <li>
              Cuerpo en JSON. Cada fila usa los mismos nombres de columna de tus reportes:
              <pre className="mt-1.5 data-table rounded-md bg-page px-2.5 py-2 font-mono text-xs leading-relaxed text-ink-2">
                {bodyExample}
              </pre>
            </li>
          </ol>

          <div className="mt-2.5 space-y-1.5 text-xs leading-relaxed text-ink-dim">
            {kind ? (
              <p>
                Esta conexión ya sabe que le mandas{" "}
                <b className="text-ink-2">{kindLabel?.toLowerCase()}</b>, así que no
                necesitas incluir <code className="text-ink-muted">kind</code>. Si algún
                día mandas otra cosa por aquí, agrégalo y manda.
              </p>
            ) : (
              <p>
                <code className="text-ink-muted">kind</code> es obligatorio en cada envío
                porque no definiste uno por defecto. Puede ser{" "}
                <code className="text-ink-muted">shipments</code>,{" "}
                <code className="text-ink-muted">movements</code>,{" "}
                <code className="text-ink-muted">ads</code> o{" "}
                <code className="text-ink-muted">cs</code>.
              </p>
            )}
            <p>
              Si tu herramienta no te deja envolver el contenido, también se acepta un
              arreglo suelto de filas.
            </p>
            <p>
              Master Data responde apenas recibe, no cuando termina: el conteo de filas
              aparece en <b className="text-ink-2">Cargar datos</b> cuando la carga
              termina de procesarse. Es la misma cola, y el mismo control de duplicados,
              que un archivo subido a mano.
            </p>
          </div>
        </div>

        <div className="flex justify-end">
          <Button size="sm" onClick={onClose}>
            Ya la guardé
          </Button>
        </div>
      </div>
    </SidePanel>
  );
}

/** A read-only value with one button. Selecting 40 hex characters by hand is a trap. */
function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setFailed(false);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard is blocked outside HTTPS and in some browsers. Say so instead
      // of silently doing nothing.
      setFailed(true);
    }
  }

  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-ink-muted">{label}</span>
        <button
          type="button"
          onClick={copy}
          className="rounded-md border border-line-input px-2 py-0.5 text-xs font-semibold text-ink-2 transition-colors hover:border-accent hover:text-accent-ink"
        >
          {copied ? "Copiado" : "Copiar"}
        </button>
      </div>
      <p className="break-all rounded-control border border-line-input bg-page px-3 py-2 font-mono text-sm leading-relaxed text-ink selection:bg-accent/30">
        {value}
      </p>
      {failed && (
        <p className="mt-1 text-xs text-warning-ink">
          El navegador no dejó copiar. Selecciona el texto y cópialo a mano.
        </p>
      )}
    </div>
  );
}
