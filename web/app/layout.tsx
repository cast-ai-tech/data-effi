import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { Suspense } from "react";

import { DateRangeProvider } from "@/lib/date-range";

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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className={inter.variable}>
      <body className="min-h-screen bg-page text-ink antialiased">
        {/*
          The range lives in the query string, so the provider reads
          `useSearchParams` and therefore needs a Suspense boundary here. It
          wraps the whole app on purpose: every screen under it - dashboards,
          órdenes, productos - reads the same filter.
        */}
        <Suspense fallback={null}>
          <DateRangeProvider>{children}</DateRangeProvider>
        </Suspense>
      </body>
    </html>
  );
}
