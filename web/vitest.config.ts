import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

/**
 * JSX is compiled by `@vitejs/plugin-react` with React's automatic runtime.
 *
 * Vitest 4 runs on vite 8 (rolldown), which is exactly what plugin-react v6
 * declares - so the plugin applies cleanly here, where under vitest 2's
 * bundled vite 5 it silently did not and JSX had to go through the `esbuild`
 * option instead. That option no longer exists on the rolldown pipeline.
 *
 * tsconfig.json says `"jsx": "preserve"` for Next's own compiler, so the
 * runtime has to be chosen here rather than inherited.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    // Mirrors the `@/*` -> `./*` mapping in tsconfig.json.
    alias: {
      "@": projectRoot,
    },
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./vitest.setup.ts"],
    // Unit tests only. Playwright specs live in tests/e2e and must never be
    // picked up by Vitest - they would fail on the missing `test` fixtures.
    include: ["tests/unit/**/*.test.ts", "tests/unit/**/*.test.tsx"],
    exclude: ["node_modules/**", ".next/**", "tests/e2e/**"],
  },
});
