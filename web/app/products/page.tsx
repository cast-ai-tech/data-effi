"use client";

/**
 * `/products` before the country moved into the route.
 *
 * Kept as a redirect rather than deleted: this URL was real for a while and is
 * sitting in open tabs and pasted links. A 404 would punish the reader for an
 * information-architecture decision they had no part in.
 */

import { Suspense } from "react";

import { AppShell } from "@/components/AppShell";
import { CountryRedirect } from "@/components/browse";
import { SkeletonRows } from "@/components/ui";

export default function ProductsRedirectPage() {
  return (
    <AppShell>
      {/* The redirect carries the query string, so a ?customer= deep link
          survives the hop. Reading it needs a boundary. */}
      <Suspense fallback={<SkeletonRows rows={8} />}>
        <CountryRedirect section="products" />
      </Suspense>
    </AppShell>
  );
}
