import { expect, test, type Page } from "@playwright/test";

import { logIn, skipWithoutStack, uniqueEmail, waitForLoginForm, watchProblems } from "./helpers";

/**
 * A new merchant from zero: registers, creates the first company, logs out and
 * back in, survives an expired access token, and changes the password.
 * One account per run, so the spec never depends on leftovers.
 */

skipWithoutStack();
test.describe.configure({ mode: "serial" });

const PASSWORD = "clave-e2e-segura-01";
const NEW_PASSWORD = "clave-e2e-segura-02";
const email = uniqueEmail("registro");

async function logOut(page: Page): Promise<void> {
  // On a phone the sidebar is a drawer; the button is there either way.
  const button = page.getByRole("button", { name: "Cerrar sesión" });
  if (!(await button.isVisible())) {
    await page.getByRole("button", { name: /menú/i }).first().click();
  }
  await button.click();
  await expect(page).toHaveURL(/\/login/, { timeout: 20_000 });
}

test("registro -> primera empresa -> conexiones", async ({ page }) => {
  const problems = watchProblems(page);
  await page.goto("/login");
  await waitForLoginForm(page);
  await page.getByRole("button", { name: "¿Primera vez? Inicia gratis 1 mes" }).click();
  await page.getByLabel("Tu nombre").fill("Comerciante E2E");
  await page.getByLabel("Correo").fill(email);
  await page.getByLabel("Contraseña").fill(PASSWORD);
  await page.getByRole("button", { name: "Iniciar gratis" }).click();

  await expect(page).toHaveURL(/\/empresas\/nueva/, { timeout: 20_000 });
  await expect(page.getByRole("heading", { name: "Crea tu primera empresa" })).toBeVisible();

  // The submit stays disabled until name, country and type are chosen.
  const submit = page.getByRole("button", { name: "Crear empresa y conectar" });
  await expect(submit).toBeDisabled();
  await page.getByPlaceholder("Distrilatam Ecuador").fill("Tienda E2E");
  await page.getByRole("group", { name: "País de la empresa" }).getByRole("button", { name: /Ecuador/ }).click();
  await page.getByRole("button", { name: /Tienda de dropshipping/ }).click();
  await submit.click();

  await expect(page).toHaveURL(/\/connections/, { timeout: 20_000 });
  // The new company is the active one, with Ecuador in the sidebar.
  await expect(page.locator('nav a[href="/ec"]').first()).toBeAttached({ timeout: 20_000 });
  expect(problems()).toEqual([]);
});

test("cerrar sesión y volver a entrar", async ({ page }) => {
  await logIn(page, email, PASSWORD);
  await logOut(page);
  // The session is really gone: a protected page sends back to the login.
  await page.goto("/global");
  await expect(page).toHaveURL(/\/login\?next=%2Fglobal/);
  await logIn(page, email, PASSWORD);
  await expect(page).not.toHaveURL(/\/login/);
});

test("contraseña equivocada muestra un error claro", async ({ page }) => {
  await page.goto("/login");
  await waitForLoginForm(page);
  await page.getByLabel("Correo").fill(email);
  await page.getByLabel("Contraseña").fill("no-es-la-clave");
  await page.getByRole("button", { name: "Entrar", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("alert")).not.toContainText(/error|exception|undefined/i);
  await expect(page).toHaveURL(/\/login/);
});

test("un access token vencido se renueva solo con el refresh", async ({ page, context }) => {
  await logIn(page, email, PASSWORD);
  const problems = watchProblems(page, [/\/auth\/refresh/]);

  // Drop the short-lived access cookie: exactly what the browser holds 15
  // minutes after the last request. The refresh cookie stays.
  const cookies = await context.cookies();
  const access = cookies.find((cookie) => cookie.name === "masterdata_access");
  expect(access, "la cookie de acceso existe tras el login").toBeTruthy();
  await context.clearCookies({ name: "masterdata_access" });

  await page.goto("/ec");
  await expect(page).toHaveURL(/\/ec$/);
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(2_000);
  const renewed = (await context.cookies()).find((cookie) => cookie.name === "masterdata_access");
  expect(renewed, "el proxy emitió una cookie de acceso nueva").toBeTruthy();
  // Each call made before the renewal answers 401 once, by design (the
  // client refreshes once and replays); nothing else may fail.
  expect(problems().filter((problem) => !/401/.test(problem.text))).toEqual([]);

  // With the refresh gone as well, the next navigation lands on the login.
  await context.clearCookies();
  await page.goto("/ec");
  await expect(page).toHaveURL(/\/login/);
});

test("cambiar la contraseña desde Mi cuenta", async ({ page }) => {
  await logIn(page, email, PASSWORD);
  await page.goto("/cuenta");
  await page.getByLabel("Contraseña actual").fill("otra-cualquiera-123");
  await page.getByLabel("Contraseña nueva").fill(NEW_PASSWORD);
  await page.getByLabel("Repite la nueva").fill(NEW_PASSWORD + "x");
  await expect(page.getByText("Las dos no coinciden.")).toBeVisible();
  const change = page.getByRole("button", { name: "Cambiar contraseña" });
  await expect(change).toBeDisabled();

  // Wrong current password: a message on the page, not a trip to the login.
  await page.getByLabel("Repite la nueva").fill(NEW_PASSWORD);
  await change.click();
  await expect(page.getByText(/contraseña actual/i).last()).toBeVisible();
  await expect(page).toHaveURL(/\/cuenta/);

  await page.getByLabel("Contraseña actual").fill(PASSWORD);
  await change.click();
  await expect(page).toHaveURL(/\/login/, { timeout: 20_000 });
  await waitForLoginForm(page);

  // The old password no longer works; the new one does.
  await page.getByLabel("Correo").fill(email);
  await page.getByLabel("Contraseña").fill(PASSWORD);
  await page.getByRole("button", { name: "Entrar", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await logIn(page, email, NEW_PASSWORD);
});
