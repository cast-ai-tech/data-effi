import { describe, expect, it } from "vitest";

import {
  planCompanies,
  planPrice,
  shouldRedirectToPlans,
  subscriptionBanner,
} from "@/lib/billing";
import type { SubscriptionState } from "@/lib/types";

function state(overrides: Partial<SubscriptionState>): SubscriptionState {
  return {
    status: "trial",
    plan_code: null,
    plan_name: null,
    requested_plan_code: null,
    requested_plan_name: null,
    trial_ends_at: "2026-09-24T00:00:00Z",
    current_period_end: null,
    days_left: 30,
    max_tenants: 1,
    tenants_used: 1,
    blocked: false,
    message: "Prueba gratis: te quedan 30 días.",
    ...overrides,
  };
}

describe("plan copy", () => {
  it("prints the three fixed prices and 'a convenir' for the custom one", () => {
    expect(planPrice({ price_usd: 29, is_custom: false })).toBe("USD 29 / mes");
    expect(planPrice({ price_usd: 99, is_custom: false })).toBe("USD 99 / mes");
    expect(planPrice({ price_usd: null, is_custom: true })).toBe("A convenir");
  });

  it("counts companies in the operator's words", () => {
    expect(planCompanies({ max_tenants: 1, is_custom: false })).toBe("1 empresa");
    expect(planCompanies({ max_tenants: 6, is_custom: false })).toBe("6 empresas");
    expect(planCompanies({ max_tenants: null, is_custom: true })).toBe("Las empresas que necesites");
  });
});

describe("subscriptionBanner", () => {
  it("is quiet on an active plan and absent without a subscription", () => {
    expect(subscriptionBanner(null)).toBeNull();
    expect(subscriptionBanner(state({ status: "active", plan_code: "master" }))).toBeNull();
  });

  it("counts the days of the free month and gets louder in the last week", () => {
    expect(subscriptionBanner(state({ days_left: 20 }))).toMatchObject({ tone: "neutral", cta: true });
    expect(subscriptionBanner(state({ days_left: 3 }))?.text).toBe("Tu mes gratis termina en 3 días.");
    expect(subscriptionBanner(state({ days_left: 1 }))?.text).toBe("Tu mes gratis termina en 1 día.");
    expect(subscriptionBanner(state({ days_left: 0 }))?.text).toBe("Tu mes gratis termina hoy.");
  });

  it("turns red when the door is closed", () => {
    const banner = subscriptionBanner(
      state({ blocked: true, days_left: 0, message: "Tu mes gratis terminó. Elige un plan." }),
    );
    expect(banner).toMatchObject({ tone: "negative", text: "Tu mes gratis terminó. Elige un plan." });
  });

  it("says a chosen plan is waiting for the advisor", () => {
    expect(subscriptionBanner(state({ status: "pending", requested_plan_code: "master_pro" }))?.tone).toBe(
      "accent",
    );
  });
});

describe("shouldRedirectToPlans", () => {
  it("never loops on the plans screen or the login", () => {
    expect(shouldRedirectToPlans("/planes")).toBe(false);
    expect(shouldRedirectToPlans("/login")).toBe(false);
    expect(shouldRedirectToPlans("/ec")).toBe(true);
    expect(shouldRedirectToPlans("/usuarios")).toBe(true);
  });
});
