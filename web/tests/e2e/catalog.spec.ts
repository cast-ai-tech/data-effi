import path from "node:path";

import { expect, test } from "@playwright/test";

import {
  createMerchant,
  logIn,
  skipWithoutStack,
  uploadFixture,
  watchProblems,
  type Merchant,
} from "./helpers";

/**
 * After the first upload: find a guide (with % and _ read as plain text),
 * read the customers, and give a product its real cost.
 */

skipWithoutStack();
test.describe.configure({ mode: "serial" });

const FIXTURES = path.resolve(__dirname, "../../../tests/fixtures");
let merchant: Merchant;

test.beforeAll(async () => {
  merchant = await createMerchant("catalogo", ["CO"]);
  await uploadFixture(merchant, path.join(FIXTURES, "effi_guias_dia1.csv"), {
    country: "CO",
    platform: "effi",
  });
});

test("buscar guías, con % y _ como texto literal", async ({ page }) => {
  await logIn(page, merchant.email, merchant.password);
  const problems = watchProblems(page);
  await page.goto("/co/orders");
  const search = page.getByLabel("Buscar por número de guía");
  const rows = page.locator("main table tbody tr");
  await expect(rows.first()).toBeVisible({ timeout: 30_000 });

  await search.fill("E-1001");
  await expect(rows).toHaveCount(1, { timeout: 15_000 });
  await expect(rows.first()).toContainText("E-1001");

  // "%" and "_" are LIKE wildcards; a merchant typing them means the character.
  for (const term of ["%", "_", "E-100_", "E%1"]) {
    await search.fill(term);
    await expect(page.getByText("Ninguna guía coincide")).toBeVisible({ timeout: 15_000 });
  }

  await search.fill("");
  await expect(rows.nth(5)).toBeVisible({ timeout: 15_000 });
  expect(problems()).toEqual([]);
});

test("clientes del país", async ({ page }) => {
  await logIn(page, merchant.email, merchant.password);
  const problems = watchProblems(page);
  await page.goto("/co/customers");
  await expect(page.locator("main table tbody tr").first()).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Mínimo de pedidos").fill("50");
  await expect(page.locator("main table tbody tr")).toHaveCount(0, { timeout: 15_000 });
  expect(problems()).toEqual([]);
});

test("editar el costo de un producto y que quede guardado", async ({ page }) => {
  await logIn(page, merchant.email, merchant.password);
  const problems = watchProblems(page);
  await page.goto("/co/products");
  const row = page.locator("main table tbody tr").filter({ hasText: "Faja Reductora" }).first();
  await expect(row).toBeVisible({ timeout: 30_000 });
  await row.click();

  const cost = page.getByLabel(/Costo unitario/);
  await cost.fill("31500");
  await page.getByLabel("SKU").fill("FAJA-01");
  const save = page.getByRole("button", { name: /Guardar cambios|Confirmar costo y guardar/ });
  await save.click();
  // A cost change asks for one confirmation before it overwrites the margin.
  const confirm = page.getByRole("button", { name: "Confirmar costo y guardar" });
  if (await confirm.isVisible().catch(() => false)) await confirm.click();
  await expect(cost).toBeHidden({ timeout: 15_000 });

  await page.reload();
  const saved = page.locator("main table tbody tr").filter({ hasText: "Faja Reductora" }).first();
  await expect(saved).toContainText(/31[.,]500/, { timeout: 30_000 });
  await saved.click();
  await expect(page.getByLabel("SKU")).toHaveValue("FAJA-01");
  await page.getByRole("button", { name: "Cancelar" }).click();

  const search = page.getByLabel("Buscar productos");
  await search.fill("FAJA-01");
  await expect(page.locator("main table tbody tr")).toHaveCount(1, { timeout: 15_000 });
  await search.fill("%");
  await expect(page.getByText("Ningún producto coincide")).toBeVisible({ timeout: 15_000 });
  expect(problems()).toEqual([]);
});
