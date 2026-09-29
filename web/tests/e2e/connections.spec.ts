import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import {
  API_URL,
  createMerchant,
  logIn,
  skipWithoutStack,
  uploadFixture,
  watchProblems,
  type Merchant,
} from "./helpers";

/**
 * The connections screen with nothing mocked on the API side. Dropi and Effi
 * are unreachable with fake credentials, and that is the point: the merchant
 * must read a clear sentence, never a crash or a stack of codes.
 */

skipWithoutStack();
test.describe.configure({ mode: "serial" });

const FIXTURES = path.resolve(__dirname, "../../../tests/fixtures");
let merchant: Merchant;

test.beforeAll(async () => {
  merchant = await createMerchant("conexiones", ["CO"]);
  // A first upload per platform creates its connection (migration 042).
  await uploadFixture(merchant, path.join(FIXTURES, "effi_guias_dia1.csv"), {
    country: "CO",
    platform: "effi",
  });
  await uploadFixture(merchant, path.join(FIXTURES, "dropi_ordenes_real_shape.xlsx"), {
    country: "CO",
    platform: "dropi",
  });
});

async function manage(page: Page, platform: RegExp) {
  await page.goto("/connections");
  const card = page.locator("article").filter({
    has: page.getByRole("heading", { level: 3, name: platform }),
  });
  await card.getByRole("button", { name: "Gestionar" }).first().click();
  const drawer = page.getByRole("dialog", { name: /Gestionar conexión/ });
  await expect(drawer).toBeVisible();
  return drawer;
}

test("Dropi: un token falso se explica con palabras, sin romper la pantalla", async ({ page }) => {
  await logIn(page, merchant.email, merchant.password);
  // The API answers the fake token with a 4xx on purpose.
  const problems = watchProblems(page, [/\/config\/dropi\/.* -> 4\d\d/, /status of 4\d\d/]);
  const drawer = await manage(page, /^Dropi/);

  await drawer.getByLabel(/Token de integración/).fill("token-falso-e2e-1234567890");
  await drawer.getByRole("button", { name: "Conectar API" }).click();
  // Wait for the action to end, not for any paragraph that happens to say
  // "Dropi": the panel's own intro already does.
  const idle = drawer.getByRole("button", { name: /^(Conectar API|Actualizar token)$/ });
  await expect(idle).toBeVisible({ timeout: 60_000 });
  const saidAfterSave = await drawer.innerText();

  const probe = drawer.getByRole("button", { name: "Probar conexión" });
  if (await probe.isEnabled()) await probe.click();
  // Dropi refuses the token; the drawer says so in a sentence, and once.
  const refusal = drawer.getByText(/Dropi rechazó el token de integración/);
  await expect(refusal).toHaveCount(1, { timeout: 60_000 });
  await expect(refusal).toContainText(/Genera uno nuevo en Dropi/);
  await expect(drawer.getByText("Dropi rechazó el token", { exact: true })).toBeVisible();
  expect(saidAfterSave).not.toMatch(/Guardando…/);
  const text = (await drawer.innerText()).toLowerCase();
  expect(text).not.toMatch(/traceback|exception|undefined|\[object|errno|httpx|status code \d{3}/);
  expect(problems().filter((problem) => problem.kind === "pageerror")).toEqual([]);
  expect(problems().filter((problem) => problem.kind === "console" && !/status of 4\d\d/.test(problem.text))).toEqual([]);
});

test("Effi: generar código, cuenta atrás, canje con sesión falsa y aviso claro", async ({ page, request }) => {
  await logIn(page, merchant.email, merchant.password);
  const problems = watchProblems(page);
  const drawer = await manage(page, /^Effi/);

  await expect(drawer.getByRole("heading", { name: "Conectar con la extensión" })).toBeVisible();
  const generate = drawer.getByRole("button", { name: "Generar código" });
  await expect(generate).toBeDisabled();
  await drawer.getByRole("checkbox", { name: /usar mi sesión/ }).check();
  await generate.click();

  const code = drawer.getByLabel(/^Código /);
  await expect(code).toBeVisible({ timeout: 15_000 });
  const value = (await code.textContent())?.trim() ?? "";
  expect(value.length).toBeGreaterThanOrEqual(8);

  const countdown = drawer.getByTestId("pairing-countdown");
  const first = await countdown.textContent();
  await page.waitForTimeout(2_200);
  expect(await countdown.textContent()).not.toBe(first);

  // What the extension would send, with a session Effi will not recognise.
  const redeem = await request.post(`${API_URL}/config/effi/pairing/redeem`, {
    data: {
      code: value,
      cookies: [{ name: "ci_session", value: "sesion-falsa-e2e-0123456789abcdef" }],
      user_agent: "Playwright e2e",
    },
  });
  expect(redeem.status(), await redeem.text()).toBeLessThan(500);

  // The panel stops waiting and says what happened, in words: Effi refused
  // the fake session (or, offline, that it could not be checked yet).
  const outcome = drawer.getByRole("status");
  await expect(outcome).toContainText(
    /Effi no aceptó la sesión|no se pudo comprobar|Effi no respondió/,
    { timeout: 30_000 },
  );
  const said = (await outcome.innerText()).toLowerCase();
  expect(said).not.toMatch(/traceback|exception|undefined|errno|httpx|usuario y contraseña/);
  const redeemed = await redeem.json();
  expect(redeemed.summary).not.toMatch(/usuario y contraseña/);
  await expect(drawer.getByRole("button", { name: "Generar otro código" })).toBeVisible();
  await expect(drawer.getByTestId("pairing-countdown")).toHaveCount(0);

  // The same code cannot be used twice.
  const again = await request.post(`${API_URL}/config/effi/pairing/redeem`, {
    data: { code: value, cookies: [{ name: "ci_session", value: "otra-sesion-falsa-0123" }] },
  });
  expect(again.status()).toBe(404);
  expect(problems()).toEqual([]);
});
