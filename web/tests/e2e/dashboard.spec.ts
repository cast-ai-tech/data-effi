import { expect, test, type Page, type Request } from "@playwright/test";

import { logIn, skipWithoutStack, watchProblems } from "./helpers";

/**
 * Reading the business: Global, a country and its Informe under the three
 * header filters (dates, statuses, platform), and a country board the merchant
 * reorders by dragging and then puts back with "Restablecer el orden".
 */

skipWithoutStack();
test.describe.configure({ mode: "serial" });

const cards = (page: Page) => page.locator("main [draggable='true']");

async function cardTitles(page: Page): Promise<string[]> {
  await expect(cards(page).first()).toBeVisible({ timeout: 30_000 });
  return cards(page).evaluateAll((nodes) =>
    nodes.map((node) => (node.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 40)),
  );
}

function kpiRequests(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/backend/kpis/")) seen.push(request);
  });
  return seen;
}

test("Global con rango de fechas: vacío explicado y vuelta a Máximo", async ({ page }) => {
  await logIn(page);
  const problems = watchProblems(page);
  await page.goto("/global");
  await expect(page.getByRole("heading", { name: "Global" })).toBeVisible();
  const seen = kpiRequests(page);

  const dates = page.getByRole("banner").getByRole("button", { name: "Máximo", exact: true });
  await dates.click();
  const dialog = page.getByRole("dialog", { name: "Seleccionar rango de fechas" });
  await dialog.getByRole("button", { name: "Últimos 7 días" }).click();
  await dialog.getByRole("button", { name: "Aplicar" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: /Últimos 7 días/ })).toBeVisible();
  // The reads now carry the window.
  await expect.poll(() => seen.some((request) => /date_from=|from=/.test(request.url()))).toBe(true);
  await page.waitForTimeout(2_000);

  await page.getByRole("button", { name: /Últimos 7 días/ }).click();
  await dialog.getByRole("button", { name: "Máximo" }).click();
  await dialog.getByRole("button", { name: "Aplicar" }).click();
  await expect(page.getByRole("button", { name: /Últimos 7 días/ })).toHaveCount(0);
  await page.waitForTimeout(2_000);
  expect(problems()).toEqual([]);
});

test("filtro de estados y de plataforma en un país y su informe", async ({ page }) => {
  await logIn(page);
  const problems = watchProblems(page);
  await page.goto("/ec");
  await expect(cards(page).first()).toBeVisible({ timeout: 30_000 });
  const seen = kpiRequests(page);

  await page.getByRole("banner").getByRole("button", { name: /^Estados/ }).click();
  const statuses = page.getByRole("group", { name: "Estados incluidos en todos los cálculos" });
  await statuses.getByRole("button", { name: "Devolución" }).click();
  await expect(page.getByRole("button", { name: /Estados\s*4 de 5/ })).toBeVisible();
  await expect.poll(() => seen.some((request) => /status/.test(request.url()))).toBe(true);
  await page.waitForTimeout(2_000);

  // The filter follows the merchant to the Informe, and the printable band
  // says so: the header with the filter does not print.
  await page.getByRole("link", { name: "Informe diario" }).click();
  await expect(page).toHaveURL(/\/ec\/informe\?.*statuses=/, { timeout: 20_000 });
  await expect(page.getByText(/sin Devolución/)).toBeVisible({ timeout: 20_000 });

  // And "Volver al tablero" comes back to the same filtered board.
  await page.getByRole("link", { name: "Volver al tablero" }).click();
  await expect(page).toHaveURL(/\/ec\?.*statuses=/, { timeout: 20_000 });
  await expect(page.getByRole("button", { name: /Estados\s*4 de 5/ })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("banner").getByRole("button", { name: /^Estados/ }).click();
  await page.getByRole("button", { name: "Contar todos otra vez" }).click();
  await expect(page.getByRole("button", { name: /Estados\s*Todos/ })).toBeVisible();

  await page.getByRole("banner").getByRole("button", { name: /^Plataforma/ }).click();
  const platforms = page.getByRole("group", { name: "Plataforma que cargó las guías" });
  await expect(platforms).toBeVisible();
  const options = platforms.getByRole("button");
  const count = await options.count();
  expect(count).toBeGreaterThan(1);
  await options.nth(1).click();
  await page.waitForTimeout(2_500);
  await page.getByRole("banner").getByRole("button", { name: /^Plataforma/ }).click();
  await page.getByRole("group", { name: "Plataforma que cargó las guías" }).getByRole("button", { name: "Todas" }).click();
  await page.waitForTimeout(1_500);
  expect(problems()).toEqual([]);
});

test("arrastrar una tarjeta, recargar y restablecer el orden", async ({ page }) => {
  await logIn(page);
  const problems = watchProblems(page);
  await page.goto("/gt");
  const before = await cardTitles(page);
  expect(before.length).toBeGreaterThan(2);

  await cards(page).nth(1).dragTo(cards(page).nth(0));
  await expect(page.getByRole("button", { name: "Restablecer el orden" })).toBeVisible({
    timeout: 15_000,
  });
  const moved = await cardTitles(page);
  expect(moved[0]).toBe(before[1]);

  // Saved in the account, not in the tab.
  await page.reload();
  await expect.poll(() => cardTitles(page).then((titles) => titles[0])).toBe(before[1]);

  await page.getByRole("button", { name: "Restablecer el orden" }).click();
  await expect.poll(() => cardTitles(page).then((titles) => titles[0])).toBe(before[0]);
  await expect(page.getByRole("button", { name: "Restablecer el orden" })).toHaveCount(0);
  await page.reload();
  await expect.poll(() => cardTitles(page).then((titles) => titles[0])).toBe(before[0]);
  expect(problems()).toEqual([]);
});
