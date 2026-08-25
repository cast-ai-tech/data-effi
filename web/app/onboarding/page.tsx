"use client";

/**
 * The old assistant ("¿En qué países operas?") is gone: a company is born
 * with its country on /empresas/nueva, and the next step is its connections.
 * Bookmarks and old links land here, so this page only forwards.
 */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { SkeletonRows } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import type { User } from "@/lib/types";

export default function OnboardingPage() {
  const router = useRouter();
  const { data: user, error } = useApi<User>("/auth/me");

  useEffect(() => {
    if (error) {
      router.replace("/login");
      return;
    }
    if (!user) return;
    router.replace(user.tenant_id ? "/connections" : "/empresas/nueva");
  }, [user, error, router]);

  return (
    <main className="mx-auto max-w-[640px] px-4 py-10">
      <SkeletonRows rows={3} />
    </main>
  );
}
