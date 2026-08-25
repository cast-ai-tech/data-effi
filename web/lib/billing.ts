/**
 * The words of the plans screen and the free-month banner.
 *
 * Mirrors `api/billing.py` (migration 048): a registration is a free month
 * for one company; then Master / Master Pro / Master Elite or a custom deal
 * with an advisor. Billing is manual - a chosen plan is "pending" until an
 * advisor activates it - so the copy has to say that plainly.
 */

import type { Plan, SubscriptionState } from "@/lib/types";

/** "$ 29 / mes", or "A convenir" for the custom plan. */
export function planPrice(plan: Pick<Plan, "price_usd" | "is_custom">): string {
  if (plan.is_custom || plan.price_usd === null || plan.price_usd === undefined) {
    return "A convenir";
  }
  return `USD ${Math.round(plan.price_usd)} / mes`;
}

/** "1 empresa" / "3 empresas" / "Las que necesites". */
export function planCompanies(plan: Pick<Plan, "max_tenants" | "is_custom">): string {
  if (plan.is_custom || plan.max_tenants === null || plan.max_tenants === undefined) {
    return "Las empresas que necesites";
  }
  return plan.max_tenants === 1 ? "1 empresa" : `${plan.max_tenants} empresas`;
}

export type BannerTone = "accent" | "warning" | "negative" | "positive" | "neutral";

export interface BannerCopy {
  tone: BannerTone;
  text: string;
  /** Whether the banner deserves a "Ver planes" link. */
  cta: boolean;
}

/**
 * One line for the top of every screen. Quiet while the month is young,
 * louder in the last week, red once the door is closed.
 */
export function subscriptionBanner(state: SubscriptionState | null | undefined): BannerCopy | null {
  if (!state) return null;
  if (state.blocked) {
    return { tone: "negative", text: state.message, cta: true };
  }
  if (state.status === "active") {
    return null;
  }
  if (state.status === "pending") {
    return { tone: "accent", text: state.message, cta: true };
  }
  const days = state.days_left ?? 0;
  if (days <= 7) {
    return {
      tone: "warning",
      text: days === 0 ? "Tu mes gratis termina hoy." : `Tu mes gratis termina en ${days} ${days === 1 ? "día" : "días"}.`,
      cta: true,
    };
  }
  return { tone: "neutral", text: `Prueba gratis: te quedan ${days} días.`, cta: true };
}

export const PLANS_PATH = "/planes";

/** Where a 402 from the API sends the browser. Never from the plans screen itself. */
export function shouldRedirectToPlans(pathname: string): boolean {
  return !pathname.startsWith(PLANS_PATH) && !pathname.startsWith("/login");
}
