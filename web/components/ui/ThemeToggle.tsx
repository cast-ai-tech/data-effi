"use client";

/**
 * Light / dark switch.
 *
 * The choice lives in a cookie, not in localStorage, so app/layout.tsx can stamp
 * `data-theme` on <html> while rendering on the server: the first paint is
 * already in the right theme and no inline script is needed (the CSP carries a
 * nonce, so an inline script would be blocked anyway). Light is the default and
 * is expressed by the ABSENCE of the attribute.
 */

import { useEffect, useState } from "react";

import { cx } from "@/components/ui/cx";

export const THEME_COOKIE = "masterdata_theme";

function readTheme(): "dark" | "light" {
  if (typeof document === "undefined") return "light";
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function useTheme() {
  const [theme, setTheme] = useState<"dark" | "light">("light");

  useEffect(() => {
    setTheme(readTheme());
  }, []);

  function apply(next: "dark" | "light") {
    setTheme(next);
    if (next === "dark") {
      document.documentElement.dataset.theme = "dark";
      document.cookie = `${THEME_COOKIE}=dark; Path=/; Max-Age=31536000; SameSite=Lax`;
    } else {
      delete document.documentElement.dataset.theme;
      document.cookie = `${THEME_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
    }
  }

  return { theme, setTheme: apply, toggle: () => apply(theme === "dark" ? "light" : "dark") };
}

export function ThemeToggle({
  className,
  showLabel = true,
}: {
  className?: string;
  showLabel?: boolean;
}) {
  const { theme, toggle } = useTheme();
  const dark = theme === "dark";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={dark}
      aria-label={dark ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
      title={dark ? "Modo claro" : "Modo oscuro"}
      className={cx(
        "flex min-h-11 w-full items-center gap-2.5 rounded-control px-3 text-sm text-ink-muted transition-colors hover:bg-hover hover:text-ink-2",
        className,
      )}
    >
      <span aria-hidden className="flex size-5 shrink-0 items-center justify-center">
        {dark ? <SunIcon /> : <MoonIcon />}
      </span>
      {showLabel && <span>{dark ? "Modo claro" : "Modo oscuro"}</span>}
    </button>
  );
}

function SunIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="size-5" aria-hidden>
      <circle cx="10" cy="10" r="3.5" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M10 2v2M10 16v2M2 10h2M16 10h2M4.3 4.3l1.4 1.4M14.3 14.3l1.4 1.4M4.3 15.7l1.4-1.4M14.3 5.7l1.4-1.4"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="size-5" aria-hidden>
      <path
        d="M16.5 12.2A7 7 0 0 1 7.8 3.5a7 7 0 1 0 8.7 8.7Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}
