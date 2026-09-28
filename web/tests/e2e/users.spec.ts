import { expect, test, type Locator, type Page } from "@playwright/test";

import {
  createMerchant,
  logIn,
  skipWithoutStack,
  uniqueEmail,
  waitForLoginForm,
  watchProblems,
  type Merchant,
} from "./helpers";

/**
 * The owner gives a partner read access to one country only; the partner
 * accepts the invitation, sees only that country, and the owner later changes
 * the role and finally removes the access.
 */

skipWithoutStack();
test.describe.configure({ mode: "serial" });

let owner: Merchant;
const partnerEmail = uniqueEmail("socio");
const PARTNER_PASSWORD = "clave-socio-segura-01";
let inviteLink = "";

test.beforeAll(async () => {
  owner = await createMerchant("duenio", ["CO", "EC"]);
});

function memberRow(page: Page, email: string): Locator {
  return page.locator("main li").filter({ hasText: email });
}

test("invitar a un socio de solo lectura, limitado a Ecuador", async ({ page }) => {
  await logIn(page, owner.email, owner.password);
  const problems = watchProblems(page);
  await page.goto("/usuarios");
  await page.getByLabel("Correo").fill(partnerEmail);
  await page.getByRole("radio", { name: /Solo lectura/ }).check();
  const countries = page.getByRole("group", { name: /Países/ });
  await countries.getByRole("button", { name: /Ecuador/ }).click();
  await page.getByRole("button", { name: "Dar acceso" }).click();

  await expect(page.getByText(`Enlace de invitación para ${partnerEmail}`)).toBeVisible({
    timeout: 15_000,
  });
  const text = await page.getByText(/\/login\?invite=/).first().textContent();
  inviteLink = (text ?? "").trim();
  expect(inviteLink).toMatch(/\/login\?invite=\S+/);
  expect(problems()).toEqual([]);
});

test("el socio acepta y solo ve Ecuador", async ({ page }) => {
  const problems = watchProblems(page, [/\/co\b.* -> 403/, /status of 403/]);
  await page.goto(inviteLink.replace(/^https?:\/\/[^/]+/, ""));
  await waitForLoginForm(page, "Crear mi cuenta");
  await expect(page.getByRole("heading", { name: "Te invitaron a Master Data" })).toBeVisible();
  await page.getByLabel("Tu nombre").fill("Socio E2E");
  await page.getByLabel("Contraseña").fill(PARTNER_PASSWORD);
  await page.getByRole("button", { name: "Crear mi cuenta" }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 30_000 });

  await expect(page.locator('nav a[href="/ec"]').first()).toBeAttached({ timeout: 20_000 });
  await expect(page.locator('nav a[href="/co"]')).toHaveCount(0);

  // Typing the other country's address gets a clear refusal, not a crash.
  await page.goto("/co");
  await expect(page.getByText("No tienes acceso a Colombia")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/API esté corriendo/)).toHaveCount(0);
  // The board did not even ask: nothing failed on the way.
  expect(problems()).toEqual([]);

  await page.goto("/co/orders");
  await expect(page.locator("main")).toContainText(/acceso|permiso|país/i, { timeout: 20_000 });
  expect(problems().filter((problem) => problem.kind === "pageerror")).toEqual([]);
});

test("el dueño cambia el rol y luego quita el acceso", async ({ page, browser }) => {
  await logIn(page, owner.email, owner.password);
  const problems = watchProblems(page);
  await page.goto("/usuarios");
  const row = memberRow(page, partnerEmail);
  await expect(row).toBeVisible({ timeout: 20_000 });
  await expect(row).toContainText("Solo lectura");

  await row.getByRole("button", { name: "Cambiar" }).click();
  await row.getByRole("button", { name: "Analista" }).click();
  await expect(row).toContainText("Analista", { timeout: 15_000 });

  await row.getByRole("button", { name: "Quitar" }).click();
  await page.getByRole("button", { name: /^(Quitar|Sí, quitar|Quitar acceso)/ }).last().click();
  await expect(page.getByText(partnerEmail, { exact: true })).toHaveCount(0, { timeout: 15_000 });
  expect(problems()).toEqual([]);

  // The partner can no longer get in to this company.
  const other = await browser.newPage();
  await other.goto("/login");
  await waitForLoginForm(other);
  await other.getByLabel("Correo").fill(partnerEmail);
  await other.getByLabel("Contraseña").fill(PARTNER_PASSWORD);
  await other.getByRole("button", { name: "Entrar", exact: true }).click();
  await other.waitForTimeout(3_000);
  await other.goto("/ec");
  await expect(other.locator('nav a[href="/ec"]')).toHaveCount(0, { timeout: 20_000 });
  await other.close();
});
