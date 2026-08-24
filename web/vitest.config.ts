/**
 * Vitest configuration for the frontend unit suite.
 *
 * Two deliberate choices:
 *
 *  - `tests/e2e` is excluded. Playwright specs use `@playwright/test`'s own `test`/`expect`, and
 *    letting Vitest collect them produces a confusing "test.describe is not a function" instead of
 *    a clear "wrong runner". The two suites share a `tests/` root but never the same runner.
 *  - The `@/` alias mirrors `tsconfig.json` (`@/*` -> `./*`). It is duplicated rather than derived
 *    because pulling in a tsconfig-paths plugin would be a dependency for one line of config.
 */
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["tests/unit/**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**", "tests/e2e/**"],
    restoreMocks: true,
  },
});
