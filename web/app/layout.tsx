import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { headers } from "next/headers";

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

export const metadata: Metadata = {
  title: "Data Effi · Analítica contraentrega",
  description:
    "Plataforma de analítica para operaciones de ecommerce contraentrega (COD) multi-país en LATAM.",
};

export const viewport: Viewport = {
  themeColor: "#0b0e14",
  width: "device-width",
  initialScale: 1,
};

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
  return (
    <html lang="es" className={inter.variable}>
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
