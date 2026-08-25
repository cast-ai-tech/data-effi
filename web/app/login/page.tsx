"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { BrandMark } from "@/components/BrandMark";
import { Button } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { safeNextPath } from "@/lib/safe-next";
import type { Tokens } from "@/lib/types";

type Mode = "login" | "register" | "invite";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNextPath(params.get("next"));
  const inviteToken = params.get("invite");

  const [mode, setMode] = useState<Mode>(inviteToken ? "invite" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);

    try {
      // The proxy keeps the tokens in HttpOnly cookies; the body carries the rest.
      if (mode === "login") {
        await api.post<Tokens>("/auth/login", { email, password }, { auth: false });
      } else if (mode === "register") {
        await api.post<Tokens>(
          "/auth/register",
          { email, password, full_name: fullName },
          { auth: false },
        );
      } else {
        await api.post<Tokens>(
          "/auth/accept-invite",
          { token: inviteToken, password, full_name: fullName },
          { auth: false },
        );
      }

      router.push(mode === "register" ? "/empresas/nueva" : next);
      router.refresh();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "No se pudo conectar con el servidor.",
      );
    } finally {
      setPending(false);
    }
  }

  const title =
    mode === "login"
      ? "Entra a tu operación"
      : mode === "register"
        ? "Inicia gratis 1 mes"
        : "Te invitaron a Master Data";
  const lead =
    mode === "login"
      ? "Analítica de contraentrega para tus países, tus transportadoras y tus productos."
      : mode === "register"
        ? "Crea tu cuenta y, en el siguiente paso, tu empresa. Un mes gratis, sin tarjeta."
        : "Escribe tu nombre y elige una contraseña. El correo ya viene en la invitación.";
  const submitLabel =
    mode === "login" ? "Entrar" : mode === "register" ? "Iniciar gratis" : "Crear mi cuenta";

  return (
    <main className="flex min-h-screen items-center justify-center bg-page px-4">
      <div className="w-full max-w-[380px]">
        <BrandMark size="md" className="mb-7" />

        <h1 className="text-xl font-bold leading-tight">{title}</h1>
        <p className="mt-1.5 text-base leading-relaxed text-ink-dim">{lead}</p>

        <form onSubmit={submit} className="mt-6 space-y-3">
          {mode !== "login" && (
            <Field
              label="Tu nombre"
              value={fullName}
              onChange={setFullName}
              autoComplete="name"
              minLength={2}
              required
            />
          )}

          {mode !== "invite" && (
            <Field
              label="Correo"
              type="email"
              value={email}
              onChange={setEmail}
              autoComplete="email"
              required
            />
          )}
          <Field
            label="Contraseña"
            type="password"
            value={password}
            onChange={setPassword}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            hint={mode === "login" ? undefined : "Mínimo 10 caracteres"}
            minLength={mode === "login" ? undefined : 10}
            required
          />

          {error && (
            <p
              role="alert"
              className="rounded-control border border-negative/30 bg-negative/[0.08] px-3 py-2 text-sm text-negative-ink"
            >
              {error}
            </p>
          )}

          <Button type="submit" className="w-full" disabled={pending}>
            {pending ? "Un momento…" : submitLabel}
          </Button>
        </form>

        {mode === "invite" ? (
          <p className="mt-4 text-center text-sm text-ink-dim">
            ¿Ya tienes cuenta con este correo?{" "}
            <button
              type="button"
              onClick={() => {
                setMode("login");
                setError(null);
              }}
              className="text-ink-muted underline underline-offset-2 hover:text-accent-ink"
            >
              Entrar
            </button>
          </p>
        ) : (
          <button
            type="button"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setError(null);
            }}
            className="mt-4 w-full text-center text-sm text-ink-muted hover:text-accent-ink"
          >
            {mode === "login"
              ? "¿Primera vez? Inicia gratis 1 mes"
              : "Ya tengo cuenta, entrar"}
          </button>
        )}
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
      <span className="mb-1 block text-sm font-medium text-ink-muted">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-control border border-line-input bg-surface px-3 py-2.5 text-base text-ink placeholder:text-ink-dim focus:border-accent focus:outline-none"
        {...props}
      />
      {hint && <span className="mt-1 block text-xs text-ink-dim">{hint}</span>}
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
