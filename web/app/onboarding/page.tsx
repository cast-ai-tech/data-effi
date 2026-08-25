"use client";

/**
 * Four-step wizard. Nothing here is optional theatre: by the end the workspace
 * has countries, at least one connection, and ideally its first file, which is
 * the minimum for any screen in the product to say something true.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Button, Card, Chip, SkeletonRows, cx } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { countryFlag } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Country, CountryPlatform } from "@/lib/types";

const STEPS = ["Países", "Conexiones", "Histórico", "Calibración"] as const;

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [primary, setPrimary] = useState<string | null>(null);

  const { data: countries, loading, reload } = useApi<Country[]>("/config/countries");

  const supported = countries ?? [];

  async function saveCountries() {
    for (const code of selected) {
      await api.put("/config/countries", { country_code: code, is_active: true });
    }
    reload();
    setStep(1);
  }

  return (
    <main className="mx-auto min-h-screen w-full max-w-[860px] px-6 py-10">
      <div className="mb-8">
        <div className="mb-2 flex items-center gap-2.5">
          <div className="flex size-7 items-center justify-center rounded-control bg-accent text-md font-extrabold text-on-accent">
            DE
          </div>
          <span className="text-lg font-bold tracking-tight">Data Effi</span>
        </div>

        <div className="mt-5 flex items-center gap-2">
          {STEPS.map((label, index) => (
            <div key={label} className="flex flex-1 items-center gap-2">
              <div className="flex-1">
                <div
                  className={cx(
                    "h-[3px] rounded-full transition-colors",
                    index <= step ? "bg-accent" : "bg-track",
                  )}
                />
                <p
                  className={cx(
                    "mt-1.5 text-xs",
                    index === step ? "font-semibold text-ink" : "text-ink-dim",
                  )}
                >
                  {index + 1}. {label}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {step === 0 && (
        <section>
          <h1 className="text-xl font-bold">¿En qué países operas?</h1>
          <p className="mt-1.5 text-base text-ink-dim">
            Cada país trae su moneda, sus transportadoras y sus plataformas. El país
            principal define la moneda con la que se consolida la vista global.
          </p>

          {loading ? (
            <SkeletonRows rows={4} className="mt-6" />
          ) : (
            <div className="mt-6 grid grid-cols-2 gap-2.5 sm:grid-cols-3">
              {supported.map((country) => {
                const isSelected = selected.includes(country.code);
                return (
                  <button
                    key={country.code}
                    type="button"
                    onClick={() => {
                      const next = isSelected
                        ? selected.filter((code) => code !== country.code)
                        : [...selected, country.code];
                      setSelected(next);
                      if (!isSelected && !primary) setPrimary(country.code);
                      if (isSelected && primary === country.code) {
                        setPrimary(next[0] ?? null);
                      }
                    }}
                    className={cx(
                      "flex flex-col items-start gap-1.5 rounded-card border p-3.5 text-left transition-colors",
                      isSelected
                        ? "border-accent bg-accent/[0.08]"
                        : "border-dashed border-line-input bg-sunken hover:border-line-strong",
                    )}
                  >
                    <span className="text-2xl leading-none">
                      {countryFlag(country.code)}
                    </span>
                    <span className="text-base font-semibold text-ink">{country.name}</span>
                    <span className="text-xs text-ink-dim">{country.currency_code}</span>
                    {primary === country.code && (
                      <Chip tone="accent" className="mt-0.5">
                        PRINCIPAL
                      </Chip>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          <div className="mt-7 flex justify-end">
            <Button onClick={saveCountries} disabled={selected.length === 0}>
              Continuar
            </Button>
          </div>
        </section>
      )}

      {step === 1 && (
        <ConnectionsStep
          countries={selected}
          onBack={() => setStep(0)}
          onNext={() => setStep(2)}
        />
      )}

      {step === 2 && (
        <section>
          <h1 className="text-xl font-bold">Carga tu histórico</h1>
          <p className="mt-1.5 text-base text-ink-dim">
            Con 90 días de reportes las curvas de maduración y las comparaciones tienen
            de dónde salir. Puedes hacerlo después, pero los números iniciales serán
            pobres.
          </p>

          <Card className="mt-6">
            <div className="flex flex-col items-center gap-3 py-8 text-center">
              <p className="text-base text-ink-2">
                La pantalla de carga acepta varios archivos a la vez.
              </p>
              <Link href="/ingest" className="no-underline">
                <Button>Ir a Cargar datos</Button>
              </Link>
            </div>
          </Card>

          <div className="mt-7 flex justify-between">
            <Button variant="ghost" onClick={() => setStep(1)}>
              Atrás
            </Button>
            <Button onClick={() => setStep(3)}>Continuar</Button>
          </div>
        </section>
      )}

      {step === 3 && (
        <CalibrationStep
          onBack={() => setStep(2)}
          onFinish={() => router.push("/global")}
        />
      )}
    </main>
  );
}

function ConnectionsStep({
  countries,
  onBack,
  onNext,
}: {
  countries: string[];
  onBack: () => void;
  onNext: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<string[]>([]);

  return (
    <section>
      <h1 className="text-xl font-bold">Conecta tus fuentes</h1>
      <p className="mt-1.5 text-base text-ink-dim">
        Solo aparecen las plataformas que existen en cada país. Empieza por la carga
        manual: funciona siempre y no requiere permisos de nadie.
      </p>

      {error && (
        <p className="mt-4 rounded-control border border-negative/30 bg-negative/[0.08] px-3 py-2 text-sm text-negative-ink">
          {error}
        </p>
      )}

      <div className="mt-6 space-y-5">
        {countries.map((code) => (
          <CountryPlatforms
            key={code}
            code={code}
            created={created}
            onCreated={(id) => setCreated((prev) => [...prev, id])}
            onError={setError}
          />
        ))}
      </div>

      <div className="mt-7 flex justify-between">
        <Button variant="ghost" onClick={onBack}>
          Atrás
        </Button>
        <Button onClick={onNext}>Continuar</Button>
      </div>
    </section>
  );
}

function CountryPlatforms({
  code,
  created,
  onCreated,
  onError,
}: {
  code: string;
  created: string[];
  onCreated: (id: string) => void;
  onError: (message: string) => void;
}) {
  const { data: platforms, loading, reload } = useApi<CountryPlatform[]>(
    `/config/platforms?country=${code}`,
  );

  const tiers = useMemo(() => {
    const groups = { 1: [], 2: [], 3: [] } as Record<number, CountryPlatform[]>;
    for (const platform of platforms ?? []) groups[platform.tier]?.push(platform);
    return groups;
  }, [platforms]);

  async function connect(platform: CountryPlatform) {
    try {
      await api.post("/config/connections", {
        country_code: code,
        platform_code: platform.platform_code,
        name: `${platform.platform_name} ${code}`,
        consent_granted: platform.requires_consent,
      });
      onCreated(`${code}:${platform.platform_code}`);
      reload();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "No se pudo crear la conexión");
    }
  }

  return (
    <Card title={`${countryFlag(code)} ${code}`}>
      {loading ? (
        <SkeletonRows rows={3} />
      ) : (
        <div className="space-y-4">
          {[2, 1, 3].map((tier) => {
            const group = tiers[tier] ?? [];
            if (group.length === 0) return null;
            return (
              <div key={tier}>
                <p className="mb-2 text-xs font-bold uppercase tracking-[0.08em] text-ink-faint">
                  {tier === 1 ? "API oficial" : tier === 2 ? "Archivo" : "Sesión (Tier 3)"}
                </p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {group.map((platform) => (
                    <div
                      key={platform.platform_code}
                      className="flex items-center justify-between gap-2 rounded-control border border-line bg-sunken px-3 py-2.5"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-base font-semibold text-ink">
                          {platform.platform_name}
                        </p>
                        <p className="text-xs text-ink-dim">
                          {platform.data_domains.join(" · ")}
                        </p>
                      </div>
                      {platform.is_connected || created.includes(`${code}:${platform.platform_code}`) ? (
                        <Chip tone="positive">Conectada</Chip>
                      ) : (
                        <Button size="sm" variant="ghost" onClick={() => connect(platform)}>
                          Conectar
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
                {tier === 3 && (
                  <p className="mt-2 text-xs leading-relaxed text-warning-ink">
                    Tier 3 usa tu sesión de usuario y puede ir contra los términos de la
                    plataforma. Lee <code>docs/tier3-politica.md</code> antes de activarla.
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function CalibrationStep({
  onBack,
  onFinish,
}: {
  onBack: () => void;
  onFinish: () => void;
}) {
  const { data: countries, loading, reload } = useApi<Country[]>("/config/countries");
  const active = (countries ?? []).filter((country) => country.is_active);

  async function apply(country: Country) {
    if (!country.maturation_days_suggested) return;
    await api.put("/config/countries", {
      country_code: country.code,
      is_active: true,
      maturation_days: country.maturation_days_suggested,
    });
    reload();
  }

  return (
    <section>
      <h1 className="text-xl font-bold">Calibración de maduración</h1>
      <p className="mt-1.5 text-base leading-relaxed text-ink-dim">
        Una cohorte de guías tarda días en estabilizar su porcentaje de entrega. Data Effi
        mide el p90 real de tu operación y te propone la ventana; no la cambia solo,
        porque eso decide cómo se mide todo lo demás.
      </p>

      {loading ? (
        <SkeletonRows rows={3} className="mt-6" />
      ) : (
        <div className="mt-6 space-y-2.5">
          {active.map((country) => (
            <div
              key={country.code}
              className="flex items-center justify-between gap-3 rounded-control border border-line bg-surface px-4 py-3"
            >
              <div>
                <p className="text-base font-semibold">
                  {countryFlag(country.code)} {country.name}
                </p>
                <p className="text-sm text-ink-dim">
                  Ventana actual: {country.maturation_days ?? 21} días
                </p>
              </div>

              {country.maturation_days_suggested ? (
                <div className="flex items-center gap-2.5">
                  <span className="text-sm text-ink-2">
                    Medimos {country.maturation_days_suggested} días
                  </span>
                  <Button size="sm" onClick={() => apply(country)}>
                    Aplicar
                  </Button>
                </div>
              ) : (
                <span className="text-sm text-ink-dim">
                  Sin datos suficientes todavía
                </span>
              )}
            </div>
          ))}
          {active.length === 0 && (
            <p className="text-base text-ink-dim">
              No hay países activos aún.
            </p>
          )}
        </div>
      )}

      <div className="mt-7 flex justify-between">
        <Button variant="ghost" onClick={onBack}>
          Atrás
        </Button>
        <Button onClick={onFinish}>Ir al tablero</Button>
      </div>
    </section>
  );
}
