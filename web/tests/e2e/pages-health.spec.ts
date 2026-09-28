import { expect, test } from "@playwright/test";

import { logIn, skipWithoutStack, watchProblems } from "./helpers";

/**
 * Every screen a merchant reaches from the sidebar opens without a console
 * error, an uncaught exception or a failed request, on a desktop and on a
 * 390px phone. On the phone the page must not scroll sideways either.
 */

skipWithoutStack();

const PAGES: { path: string; heading: RegExp }[] = [
  { path: "/global", heading: /Global/ },
  { path: "/ec", heading: /Ecuador/ },
  { path: "/ec/informe", heading: /.+/ },
  { path: "/ec/orders", heading: /.+/ },
  { path: "/ec/customers", heading: /.+/ },
  { path: "/ec/products", heading: /.+/ },
  { path: "/ec/cargar", heading: /.+/ },
  { path: "/co", heading: /Colombia/ },
  { path: "/orders", heading: /.+/ },
  { path: "/customers", heading: /.+/ },
  { path: "/products", heading: /.+/ },
  { path: "/connections", heading: /.+/ },
  { path: "/settings", heading: /.+/ },
  { path: "/usuarios", heading: /.+/ },
  { path: "/organizacion", heading: /.+/ },
  { path: "/empresas", heading: /.+/ },
  { path: "/planes", heading: /.+/ },
  { path: "/cuenta", heading: /.+/ },
];

for (const viewport of [
  { name: "escritorio", width: 1366, height: 900 },
  { name: "móvil 390px", width: 390, height: 844 },
]) {
  test.describe(`páginas sanas en ${viewport.name}`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } });

    test("cada pantalla abre sin errores de consola ni de red", async ({ page }) => {
      test.setTimeout(240_000);
      await logIn(page);
      const problems = watchProblems(page);
      const overflow: string[] = [];

      for (const { path, heading } of PAGES) {
        await test.step(path, async () => {
          await page.goto(path);
          await expect(page.getByRole("heading", { level: 1 }).first()).toHaveText(heading, {
            timeout: 20_000,
          });
          // The notification stream keeps a request open for good, so
          // "networkidle" never comes; give the page's own reads time to land.
          await page.waitForLoadState("load");
          await page.waitForTimeout(2_500);
          if (viewport.width < 500) {
            const width = await page.evaluate(() => document.documentElement.scrollWidth);
            if (width > viewport.width + 1) overflow.push(`${path}: ${width}px`);
          }
        });
      }

      expect(problems(), "errores de consola o de red").toEqual([]);
      expect(overflow, "páginas que se desbordan a lo ancho").toEqual([]);
    });
  });
}
