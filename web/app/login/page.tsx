"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button } from "@/components/ui";
import { ApiError, api, storeTokens } from "@/lib/api";
import type { Tokens } from "@/lib/types";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/global";

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [tenantName, setTenantName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);

    try {
      const tokens =
        mode === "login"
          ? await api.post<Tokens>(
              "/auth/login",
              { email, password },
              { auth: false },
            )
          : await api.post<Tokens>(
              "/auth/register",
              { email, password, full_name: fullName, tenant_name: tenantName },
              { auth: false },
            );

      storeTokens(tokens);
      router.push(mode === "register" ? "/onboarding" : next);
      router.refresh();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo conectar con el servidor.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-page px-4">
      <div className="w-full max-w-[380px]">
        <div className="mb-7 flex items-center gap-2.5">
          <div className="flex size-8 items-center justify-center rounded-[8px] bg-accent text-[16px] font-extrabold text-on-accent">
            N
          </div>
          <span className="text-[18px] font-bold tracking-tight">Norte</span>
        </div>

        <h1 className="text-[20px] font-bold leading-tight">
          {mode === "login" ? "Entra a tu operación" : "Crea tu espacio de trabajo"}
        </h1>
        <p className="mt-1.5 text-[13px] leading-relaxed text-ink-dim">
          {mode === "login"
            ? "Analítica de contraentrega para tus países, tus transportadoras y tus productos."
            : "Esto crea la cuenta dueña del despliegue. Las demás personas entran por invitación."}
        </p>

        <form onSubmit={submit} className="mt-6 space-y-3">
          {mode === "register" && (
            <>
              <Field
                label="Tu nombre"
                value={fullName}
                onChange={setFullName}
                autoComplete="name"
                required
              />
              <Field
                label="Nombre de la operación"
                value={tenantName}
                onChange={setTenantName}
                placeholder="Mi empresa"
                required
              />
            </>
          )}

          <Field
            label="Correo"
            type="email"
            value={email}
            onChange={setEmail}
            autoComplete="email"
            required
          />
          <Field
            label="Contraseña"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            hint={mode === "register" ? "Mínimo 10 caracteres" : undefined}
            required
          />

          {error && (
            <p
              role="alert"
              className="rounded-[8px] border border-negative/30 bg-negative/[0.08] px-3 py-2 text-[12px] text-negative"
            >
              {error}
            </p>
          )}

          <Button type="submit" className="w-full" disabled={pending}>
            {pending ? "Un momento…" : mode === "login" ? "Entrar" : "Crear cuenta"}
          </Button>
        </form>

        <button
          type="button"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
          className="mt-4 w-full text-center text-[12px] text-ink-muted hover:text-accent"
        >
          {mode === "login"
            ? "¿Primera vez? Crear el espacio de trabajo"
            : "Ya tengo cuenta, entrar"}
        </button>
      </div>
    </main>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  hint,
  ...props
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  hint?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "onChange" | "value" | "type">) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11.5px] font-medium text-ink-muted">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-[8px] border border-line-input bg-surface px-3 py-2.5 text-[13px] text-ink placeholder:text-ink-dim focus:border-accent focus:outline-none"
        {...props}
      />
      {hint && <span className="mt-1 block text-[11px] text-ink-dim">{hint}</span>}
    </label>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
