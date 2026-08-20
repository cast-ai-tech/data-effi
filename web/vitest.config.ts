import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

const projectRoot = fileURLToPath(new URL(".", import.meta.url));

/**
 * JSX is compiled by esbuild with React's automatic runtime rather than by
 * `@vitejs/plugin-react`.
 *
 * The installed plugin is v6, which declares `vite: ^8`, while vitest 2 runs on
 * its own bundled vite 5. Under that mismatch the plugin loads but its
 * transform never applies, and every .tsx test dies with "React is not
 * defined". esbuild's automatic runtime produces the same output for tests -
 * the plugin's extra value is Fast Refresh and the React Compiler, neither of
 * which a test run uses. If the plugin is ever needed here, pin
 * `@vitejs/plugin-react@^4` (the version that supports vite 5) and add it to
 * `plugins` instead.
 *
 * tsconfig.json says `"jsx": "preserve"` for Next's own compiler, so the value
 * has to be overridden here rather than inherited.
 */
export default defineConfig({
  esbuild: {
    jsx: "automatic",
    jsxImportSource: "react",
  },
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
