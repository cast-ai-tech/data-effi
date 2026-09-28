import { expect, test as base, type Page } from "@playwright/test";

/**
 * Shared plumbing for the end-to-end specs.
 *
 * The stack (Postgres + API + web) runs OUTSIDE Playwright; see the "Pruebas
 * end-to-end" section of web/README.md. When the API does not answer, every
 * spec skips itself instead of failing.
 */

export const API_URL = (process.env.MASTERDATA_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);
export const DEMO_EMAIL = process.env.MASTERDATA_DEMO_EMAIL ?? "demo@masterdata.app";
export const DEMO_PASSWORD = process.env.MASTERDATA_DEMO_PASSWORD ?? "demo-masterdata-2026";

let stackChecked: boolean | null = null;

export async function stackIsUp(): Promise<boolean> {
  if (stackChecked !== null) return stackChecked;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const response = await fetch(`${API_URL}/health`, { signal: controller.signal });
    clearTimeout(timer);
    stackChecked = response.ok;
  } catch {
    stackChecked = false;
  }
  return stackChecked;
}

export function skipWithoutStack(): void {
  base.beforeEach(async () => {
    base.skip(
      !(await stackIsUp()),
      `La API no responde en ${API_URL}/health. Levanta la API y la web antes de correr estas pruebas (ver web/README.md).`,
    );
  });
}

/** A unique, disposable e-mail so specs never collide with each other or with a rerun. */
export function uniqueEmail(prefix: string): string {
  const stamp = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  return `${prefix}-${stamp}@example.com`;
}

/**
 * The login button stays disabled until React takes over the form. Waiting on
 * it guarantees the fields we type into are the hydrated, controlled ones.
 */
export async function waitForLoginForm(page: Page, label = "Entrar"): Promise<void> {
  await expect(page.getByRole("button", { name: label, exact: true })).toBeEnabled({
    timeout: 30_000,
  });
}

export async function logIn(
  page: Page,
  email: string = DEMO_EMAIL,
  password: string = DEMO_PASSWORD,
): Promise<void> {
  await page.goto("/login");
  await waitForLoginForm(page);
  await page.getByLabel("Correo").fill(email);
  await page.getByLabel("Contraseña").fill(password);
  await page.getByRole("button", { name: "Entrar", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 30_000 });
}

export type Merchant = { email: string; password: string; token: string; tenantId: string };

async function apiCall<T>(
  path: string,
  init: { method?: string; body?: unknown; token?: string } = {},
): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: init.method ?? "GET",
    headers: {
      "content-type": "application/json",
      ...(init.token ? { authorization: `Bearer ${init.token}` } : {}),
    },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${init.method ?? "GET"} ${path} -> ${response.status}: ${text}`);
  return (text ? JSON.parse(text) : null) as T;
}

/**
 * A brand-new merchant with one company in `countries`, built through the API
 * so a spec about uploads does not also re-test registration. The token is
 * the one standing in that company.
 */
export async function createMerchant(
  prefix: string,
  countries: string[] = ["CO"],
): Promise<Merchant> {
  const email = uniqueEmail(prefix);
  const password = "clave-e2e-segura-01";
  await apiCall("/auth/register", {
    method: "POST",
    body: { email, password, full_name: `E2E ${prefix}` },
  });
  const first = await apiCall<{ access_token: string }>("/auth/login", {
    method: "POST",
    body: { email, password },
  });
  const tenant = await apiCall<{ tenant_id: string }>("/org/tenants", {
    method: "POST",
    token: first.access_token,
    body: { name: `Empresa ${prefix}`, countries, company_type: "dropshipping" },
  });
  const switched = await apiCall<{ access_token: string }>("/auth/switch", {
    method: "POST",
    token: first.access_token,
    body: { tenant_id: tenant.tenant_id },
  });
  return { email, password, token: switched.access_token, tenantId: tenant.tenant_id };
}

export { apiCall };

export type Problem ={ kind: "console" | "pageerror" | "request"; text: string };

/**
 * Collects console errors, uncaught exceptions and failed network requests.
 * `allow` lists patterns that a spec deliberately provokes (a Dropi call with a
 * fake token answers 4xx on purpose, for instance).
 */
export function watchProblems(page: Page, allow: RegExp[] = []): () => Problem[] {
  const problems: Problem[] = [];
  const allowed = (text: string) => allow.some((pattern) => pattern.test(text));

  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (!allowed(text)) problems.push({ kind: "console", text });
  });
  page.on("pageerror", (error) => {
    const text = `${error.name}: ${error.message}`;
    if (!allowed(text)) problems.push({ kind: "pageerror", text });
  });
  page.on("requestfailed", (request) => {
    const failure = request.failure()?.errorText ?? "";
    // Navigations and polls that the browser aborts on purpose are not failures.
    if (/ERR_ABORTED|NS_BINDING_ABORTED|cancelled/i.test(failure)) return;
    const text = `${request.method()} ${request.url()} -> ${failure}`;
    if (!allowed(text)) problems.push({ kind: "request", text });
  });
  page.on("response", (response) => {
    if (response.status() < 400) return;
    const text = `${response.request().method()} ${response.url()} -> ${response.status()}`;
    if (!allowed(text)) problems.push({ kind: "request", text });
  });

  return () => problems;
}

export { expect };
