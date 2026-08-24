/**
 * End-to-end configuration.
 *
 * The defining constraint is that these specs must run with **no backend**. Every `/api/*` call is
 * intercepted in the spec via `page.route`, which is possible only because the app is same-origin
 * by design (`next.config.mjs` rewrites `/api/*` server-side). A suite that needed a live FastAPI
 * process and a live Postgres would be run once and then quietly disabled; this one runs in CI on
 * a clean checkout.
 *
 * Playwright still needs the Next server itself, so `webServer` builds and starts one on a port of
 * its own (3210) to avoid colliding with a dev server the author already has running.
 *
 * It is a **production** server, not `next dev`, and that is not a performance preference: the site
 * sets `script-src 'self' 'unsafe-inline'` with no `'unsafe-eval'` (next.config.mjs), and the dev
 * bundler needs `eval` to hydrate. Under `next dev` the page renders but never hydrates, so a form
 * submit falls through to a native navigation and every spec fails in a way that looks like a
 * selector problem. Testing the build is also what a user actually gets.
 *
 * `API_PROXY_TARGET` is pointed at a closed port on purpose: if a spec ever forgets to stub a route,
 * the request fails loudly instead of silently reaching whatever happens to be listening on :8000.
 *
 * Note: `npx playwright install chromium` must be run once before `npm run e2e` — the browser
 * binary is a large download and is deliberately not vendored.
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = 3210;
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run build && npx next start --port ${PORT}`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 300_000, // a cold `next build` on CI is the slow part, not the tests
    env: {
      // Unroutable on purpose: an unstubbed request must fail, not escape to a real backend.
      API_PROXY_TARGET: "http://127.0.0.1:9",
    },
  },
});
