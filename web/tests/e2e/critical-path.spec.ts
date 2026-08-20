import { expect, test, type Page } from "@playwright/test";

/**
 * The one path that must never break: land, log in, read a country dashboard,
 * see a blocked widget still on screen, and reach the upload page.
 *
 * The suite skips itself when the API is not answering, so `npm run test:e2e`
 * on a laptop without docker compose up reports "skipped" rather than a wall of
 * red that trains everyone to ignore it.
 */

const API_HEALTH_URL = process.env.DATAEFFI_API_URL
  ? `${process.env.DATAEFFI_API_URL}/health`
  : "http://localhost:8000/health";

const DEMO_EMAIL = process.env.DATAEFFI_DEMO_EMAIL ?? "demo@dataeffi.co";
const DEMO_PASSWORD = process.env.DATAEFFI_DEMO_PASSWORD ?? "demo-dataeffi-2026";

let stackIsUp = false;

test.beforeAll(async () => {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const response = await fetch(API_HEALTH_URL, { signal: controller.signal });
    clearTimeout(timer);
    stackIsUp = response.ok;
  } catch {
    stackIsUp = false;
  }
});

test.beforeEach(() => {
  test.skip(
    !stackIsUp,
    `La API no responde en ${API_HEALTH_URL}. Levanta el stack (docker compose up) para correr estas pruebas.`,
  );
});

async function logIn(page: Page): Promise<void> {
  await page.goto("/");

  // Unauthenticated, the middleware sends everything to /login.
  await expect(page).toHaveURL(/\/login/);

  await page.getByLabel("Correo").fill(DEMO_EMAIL);
  await page.getByLabel("Contraseña").fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "Entrar" }).click();

  // Landing anywhere that is not the login screen means the session took.
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test.describe("critical path", () => {
  test("redirects an anonymous visitor to the login screen", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("button", { name: "Entrar" })).toBeVisible();
  });

  test("logs in with the demo credentials and lands on a dashboard", async ({ page }) => {
    await logIn(page);

    await expect(page).toHaveURL(/\/global/);
    await expect(page.getByRole("heading", { name: "Global" })).toBeVisible();
  });

  test("opens a country dashboard and shows widgets, blocked ones included", async ({
    page,
  }) => {
    await logIn(page);

    // The sidebar lists one link per active country, at /<code>. Those links
    // arrive from /config/countries after hydration, so wait for the first one
    // instead of reading an empty nav.
    await expect
      .poll(
        async () =>
          (
            await page.locator("nav a[href]").evaluateAll((nodes) =>
              nodes
                .map((node) => node.getAttribute("href") ?? "")
                .filter((href) => /^\/[a-z]{2}$/.test(href)),
            )
          ).length,
        {
          message: "El workspace demo debería tener al menos un país activo.",
          timeout: 20_000,
        },
      )
      .toBeGreaterThan(0);

    const hrefs = await page.locator("nav a[href]").evaluateAll((nodes) =>
      nodes
        .map((node) => node.getAttribute("href") ?? "")
        .filter((href) => /^\/[a-z]{2}$/.test(href)),
    );

    await page.goto(hrefs[0]);
    await expect(page).toHaveURL(new RegExp(`${hrefs[0]}$`));

    // At least one widget card rendered.
    const cards = page.locator("main section");
    await expect(cards.first()).toBeVisible({ timeout: 15_000 });

    // And a blocked one is present rather than quietly omitted.
    const blocked = page.locator('[data-widget-state="blocked"]').first();
    await expect(blocked).toBeVisible({ timeout: 15_000 });
    // The lock message plus the escape hatch. The link is matched by href, not
    // by role: the overlay sits inside an aria-hidden wrapper today.
    await expect(blocked).toContainText(/conectar/i);
    await expect(blocked.locator('a[href="/settings"]')).toBeVisible();
  });

  test("shows the upload dropzone on /ingest", async ({ page }) => {
    await logIn(page);

    await page.goto("/ingest");
    await expect(page.getByText("Arrastra tus reportes aquí")).toBeVisible({
      timeout: 15_000,
    });
  });
});
