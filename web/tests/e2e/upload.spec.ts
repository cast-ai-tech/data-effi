import path from "node:path";

import { expect, test } from "@playwright/test";

import { createMerchant, logIn, skipWithoutStack, watchProblems, type Merchant } from "./helpers";

/**
 * A merchant uploads an Effi guides report for Colombia and watches the job
 * finish; the same file again is recognised as a duplicate; the dashboard of
 * the country then has numbers.
 */

skipWithoutStack();
test.describe.configure({ mode: "serial" });

const FIXTURES = path.resolve(__dirname, "../../../tests/fixtures");
let merchant: Merchant;

test.beforeAll(async () => {
  merchant = await createMerchant("carga", ["CO"]);
});

async function pickEffiGuides(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/co/cargar");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Colombia");
  await page.getByLabel("1. Tipo de reporte").selectOption("shipments");
  await page.getByRole("radiogroup", { name: "Plataforma del archivo" }).getByRole("radio", { name: /Effi/ }).click();
}

test("subir el reporte de guías y ver el trabajo terminar", async ({ page }) => {
  await logIn(page, merchant.email, merchant.password);
  const problems = watchProblems(page);
  await pickEffiGuides(page);

  await page.locator('input[type="file"]').setInputFiles(path.join(FIXTURES, "effi_guias_dia1.csv"));
  await expect(page.getByText("Listo", { exact: true }).first()).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("10 filas", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("0 con error").first()).toBeVisible();
  expect(problems()).toEqual([]);
});

test("el mismo archivo otra vez no duplica nada", async ({ page }) => {
  await logIn(page, merchant.email, merchant.password);
  await pickEffiGuides(page);
  await page.locator('input[type="file"]').setInputFiles(path.join(FIXTURES, "effi_guias_dia1.csv"));
  await expect(page.getByText(/ya estaba cargado|duplicad|ya se había/i).first()).toBeVisible({
    timeout: 90_000,
  });
});

test("un archivo de otro tipo se explica, no se carga a ciegas", async ({ page }) => {
  await logIn(page, merchant.email, merchant.password);
  await pickEffiGuides(page);
  // A money-movements report dropped while "Guías" is selected.
  await page.locator('input[type="file"]').setInputFiles(path.join(FIXTURES, "effi_movimientos_real_shape.xls"));
  const alert = page.getByRole("alert").filter({ hasText: "Tipo de reporte" });
  await expect(alert).toContainText("«Movimientos de dinero»", { timeout: 60_000 });
  await expect(alert).not.toContainText("tracking_number");
  // Nothing was queued: the only job on screen is none at all.
  await expect(page.getByText("Procesando", { exact: true })).toHaveCount(0);
});

test("el tablero del país muestra lo cargado", async ({ page }) => {
  await logIn(page, merchant.email, merchant.password);
  const problems = watchProblems(page);
  await page.goto("/co");
  await expect(page.getByRole("heading", { level: 1 }).first()).toContainText("Colombia");
  await expect(page.locator("main section").first()).toBeVisible({ timeout: 30_000 });
  await page.goto("/co/orders");
  await expect(page.getByText("E-1001").first()).toBeVisible({ timeout: 30_000 });
  expect(problems()).toEqual([]);
});
