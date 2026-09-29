import { expect, test } from "@playwright/test";

import { logIn, skipWithoutStack } from "./helpers";

/**
 * The one path that must never break: land, log in, read a country dashboard,
 * see a blocked widget still on screen, and reach the upload page.
 *
 * The suite skips itself when the API is not answering, so `npm run test:e2e`
 * on a laptop without the API running reports "skipped" rather than a wall of
 * red that trains everyone to ignore it.
 */

skipWithoutStack();

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

  test("/ingest forwards to the country's upload screen with its dropzone", async ({ page }) => {
    await logIn(page);

    // No global upload any more (migration 042): every file belongs to a
    // country and names its platform, so the old address forwards there. With
    // one country it goes straight in; with several (the demo has three) it
    // asks which one instead of guessing.
    await page.goto("/ingest");
    const chooser = page.getByText("¿De qué país es el archivo?");
    const direct = page.waitForURL(/\/[a-z]{2}\/cargar/, { timeout: 15_000 });
    await Promise.race([chooser.waitFor({ timeout: 15_000 }), direct]);
    if (await chooser.isVisible()) {
      await page.getByRole("main").getByRole("link", { name: /Ecuador/ }).click();
    }
    await expect(page).toHaveURL(/\/[a-z]{2}\/cargar/, { timeout: 15_000 });
    await expect(page.getByText("Arrastra el reporte aquí")).toBeVisible({
      timeout: 15_000,
    });
  });
});
