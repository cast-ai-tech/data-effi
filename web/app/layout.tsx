import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { cookies, headers } from "next/headers";

import { FlagFont } from "@/components/FlagFont";
import { Suspense } from "react";

import { DateRangeProvider } from "@/lib/date-range";
import { NotificationsProvider } from "@/lib/notifications";

import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
  variable: "--font-inter",
});

const THEME_COOKIE = "masterdata_theme";

export const metadata: Metadata = {
  title: {
    default: "Master Data · Tu operación en números",
    template: "%s · Master Data",
  },
  description:
    "Sube los reportes de tus guías y Master Data te dice, por país y por producto, si estás ganando o perdiendo plata.",
  applicationName: "Master Data",
  openGraph: {
    title: "Master Data · Tu operación en números",
    description:
      "Sube los reportes de tus guías y Master Data te dice, por país y por producto, si estás ganando o perdiendo plata.",
    siteName: "Master Data",
    locale: "es_CO",
    type: "website",
  },
};

async function readTheme(): Promise<"dark" | undefined> {
  const store = await cookies();
  return store.get(THEME_COOKIE)?.value === "dark" ? "dark" : undefined;
}

export async function generateViewport(): Promise<Viewport> {
  const theme = await readTheme();
  return {
    themeColor: theme === "dark" ? "#0b0e14" : "#f4f6f9",
    width: "device-width",
    initialScale: 1,
  };
}

// Rendered per request, never prerendered. The Content-Security-Policy that
// middleware.ts sends carries a nonce that changes on every request, and Next
// only stamps that nonce onto its own inline scripts while rendering the page
// for THAT request. A page built once at deploy time has no nonce to match
// and every script on it is blocked - which is exactly what happened.
export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Reading the request headers is what opts the whole tree into dynamic
  // rendering; the nonce itself is picked up by Next from the CSP header.
  await headers();
  // The theme is decided here, on the server, so the first paint is already
  // right: no flash and no inline script (see components/ui/ThemeToggle.tsx).
  const theme = await readTheme();
  return (
    <html lang="es" className={inter.variable} data-theme={theme}>
      <body className="min-h-screen bg-page text-ink antialiased">
        {/*
          Windows no trae los glifos de las banderas, así que Chrome pinta las
          dos letras del código ("CO" en vez de 🇨🇴). Esto carga una fuente que
          SOLO cubre ese rango de caracteres, y solo cuando el navegador
          demuestra que le hace falta. Ver components/FlagFont.tsx.
        */}
        <FlagFont />
        {/*
          The range lives in the query string, so the provider reads
          `useSearchParams` and therefore needs a Suspense boundary here. It
          wraps the whole app on purpose: every screen under it - dashboards,
          órdenes, productos - reads the same filter.
        */}
        <Suspense fallback={null}>
          <DateRangeProvider>
            {/*
              Live events and the bell's counters. Mounted here so a screen
              that is not the dashboard - Cargar datos, Productos - still hears
              that a load finished. It stays idle until AppShell turns it on.
            */}
            <NotificationsProvider>{children}</NotificationsProvider>
          </DateRangeProvider>
        </Suspense>
      </body>
    </html>
  );
}
