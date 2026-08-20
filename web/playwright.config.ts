import { defineConfig, devices } from "@playwright/test";

/**
 * The stack (API + database + web) is brought up by docker compose or
 * `npm run dev` OUTSIDE the test run. Playwright never starts it: a test run
 * that silently boots half a stack hides exactly the integration problems this
 * suite exists to catch. When nothing is listening, the specs skip themselves.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "list" : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // No `webServer` on purpose - see the note above.
});
