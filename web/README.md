# DBWhisper Web

A Next.js (App Router) frontend for the **DBWhisper** natural-language-to-SQL API: a marketing page,
and a console where you ask a question in plain English and get back the generated SQL, a summary, a
sortable table, a chart — and the evidence for how the answer was produced.

## Stack

- Next.js 15 (App Router) + React 18 + TypeScript (strict)
- Tailwind CSS v3 + PostCSS + autoprefixer
- No UI component library — hand-rolled components with Tailwind
- Vitest + Testing Library (unit) and Playwright (end-to-end, against a stubbed API)
- Package manager: npm

## Getting started

```bash
npm install
npm run dev
```

Then open <http://localhost:3000>. A backend is only needed for the console to return real answers;
the marketing page and the console shell render without one.

### Environment

The browser never calls the backend directly on the default path. It calls **its own origin** at
`/api/*`, and Next rewrites that to the backend named by `API_PROXY_TARGET` (see
[`next.config.mjs`](./next.config.mjs)). That is why there is no CORS configuration here, and why the
site's own CSP can pin `connect-src 'self'`.

| Variable                   | Scope              | Default                        | Description                                                                                                |
| -------------------------- | ------------------ | ------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| `API_PROXY_TARGET`         | server, **build-time** | `http://localhost:8000`     | Origin that Next rewrites `/api/*` to. **This is the one to set on Vercel.** Needs a rebuild to change.   |
| `NEXT_PUBLIC_API_BASE`     | client, build-time | `/api`                         | Only to bypass the proxy and call the backend directly. Requires backend CORS **and** a CSP change.          |
| `NEXT_PUBLIC_SITE_URL`     | build              | `https://dbwhisper.vercel.app` | Canonical origin for metadata, robots and sitemap ([`src/lib/site.ts`](./src/lib/site.ts)).                  |
| `NEXT_PUBLIC_SENTRY_DSN`   | client             | unset                          | Optional error tracking; a no-op when unset.                                                                 |
| `NEXT_PUBLIC_AUTH_ENABLED` | client             | unset                          | Set to `true` to show the login UI; pair it with the backend's `USER_AUTH_ENABLED`.                          |

Copy the example file and adjust:

```bash
cp .env.example .env.local
```

**Every variable here is resolved at build time, including `API_PROXY_TARGET`.** Next calls
`rewrites()` during `next build` and freezes the resolved destination into
`.next/routes-manifest.json`; neither `next start` nor the standalone server re-reads
`next.config.mjs`. Setting `API_PROXY_TARGET` on an already-built container therefore does nothing
at all — the container comes up healthy and every `/api/*` call still goes wherever the build
pointed it, which is the most confusing possible failure. Change it, then rebuild. (Vercel rebuilds
on each deploy, so this is only a trap for a container you are trying to reconfigure in place.)

To see where an existing image actually points:

```bash
docker run --rm --entrypoint node <image>   -e 'console.log(require("./.next/routes-manifest.json").rewrites.afterFiles)'
```

Anything prefixed `NEXT_PUBLIC_` is inlined into the client bundle at build time for the same
reason.

## Running in Docker

The image is built by [`Dockerfile`](./Dockerfile) and wired into the repository's `compose.yaml` as
a `core`-profile service:

```bash
docker compose --profile core up -d --build
```

`--build` is not optional after changing `API_PROXY_TARGET` or any `NEXT_PUBLIC_*` value: they are
build arguments, for the reason given above. Inside compose the default target is `http://api:8000`
(the API service name) — `localhost` can never be right from inside a container.

## Scripts

| Script                  | Description                                                     |
| ----------------------- | --------------------------------------------------------------- |
| `npm run dev`           | Start the dev server                                             |
| `npm run build`         | Production build                                                 |
| `npm run start`         | Serve the production build                                       |
| `npm run lint`          | ESLint (`next/core-web-vitals`)                                  |
| `npm run typecheck`     | `tsc --noEmit` (strict type check, tests included)               |
| `npm test`              | Vitest unit suite (jsdom + Testing Library), single run          |
| `npm run test:watch`    | Vitest in watch mode                                             |
| `npm run test:coverage` | Vitest with V8 coverage                                          |
| `npm run e2e`           | Playwright end-to-end suite                                      |
| `npm run e2e:install`   | One-time Chromium download for Playwright                        |

## Testing

**Unit** (`tests/unit/`) covers the pure helpers in `src/lib/` — result-row normalisation, CSV/JSON/
Markdown export and its escaping, the auto-chart heuristic, local history — and the results
components, driven through the DOM with Testing Library. Config: [`vitest.config.ts`](./vitest.config.ts).

**End-to-end** (`tests/e2e/`) drives the console in a real browser with **every `/api/*` route stubbed
by Playwright**, so it needs neither a FastAPI process nor a database. `API_PROXY_TARGET` is pointed at
a closed port during the run, so a request a spec forgot to stub fails loudly instead of escaping to
whatever happens to be listening on `:8000`. Config: [`playwright.config.ts`](./playwright.config.ts).

Playwright builds and serves a **production** server (port 3210) rather than running `next dev`. That
is a correctness requirement, not a preference: the CSP in `next.config.mjs` omits `'unsafe-eval'`,
which the dev bundler needs in order to hydrate — under `next dev` the page renders but never becomes
interactive.

The browser binary is a large download and is not vendored, so run it once before the first e2e run:

```bash
npm run e2e:install   # or: npx playwright install chromium
npm run e2e
```

## API contract

The console calls:

- `GET /health` → `{ status, message, version }` — a static liveness literal, so the badge is labelled
  "API reachable" rather than "online".
- `GET /databases` → `{ databases: DatabaseSummary[] }`
- `GET /schemas/{db_flag}` → `SchemaResponse` (the schema browser)
- `POST /query` → `QueryResponse` (natural-language question)
- `POST /run_sql` → `QueryResponse` (user-edited SQL, through the same policy + execution path)
- `GET | POST /training/pairs`, `DELETE /training/pairs/{id}` → the verified question→SQL library
- `POST | GET /auth/*` → only when `NEXT_PUBLIC_AUTH_ENABLED=true`

All types live in [`src/lib/api.ts`](./src/lib/api.ts).

### Evidence metadata

`QueryResponse.metadata` carries the v2 evidence fields — `policy_version`, `policy_decision`,
`sql_fingerprint`, `tables_used`, `truncated`, `read_only_enforced`, `error_category` — and
`QueryResultData` adds `truncated` and `row_limit`. **Every one of them is optional**, so the UI
distinguishes three states, not two: reported true, reported false, and not reported at all. The
translation lives in [`src/lib/evidence.ts`](./src/lib/evidence.ts) and is rendered by
`EvidenceStrip`; an unreported mechanism draws as a dashed "not reported" chip and is never shown as
a tick.

## Deploying to Vercel

1. Import the repository in Vercel.
2. Set **Root Directory** to `web` (this folder).
3. Add the environment variable `API_PROXY_TARGET` and point it at the deployed API origin (e.g. the
   Hugging Face Space URL).
4. Build command `npm run build` and output are auto-detected for Next.js.

> The browser calls the same origin, so CORS is not involved on the default path. It becomes relevant
> only if you set `NEXT_PUBLIC_API_BASE` to an absolute URL, which also requires relaxing
> `connect-src` in the CSP in `next.config.mjs`.

## Project structure

```text
web/
├── app/
│   ├── (marketing)/         # Public landing page + its layout
│   ├── (app)/               # The console: query page, schema browser, verified pairs
│   ├── components/          # Hand-rolled UI (EvidenceStrip, ResultsPanel, ResultsTable,
│   │                        #   ResultChart, TruncationBanner, SqlCode, providers, nav)
│   ├── globals.css          # Tailwind directives + small theme tweaks
│   ├── layout.tsx           # Root layout (dark theme) + metadata
│   ├── opengraph-image.tsx  # Generated OG image, robots.ts, sitemap.ts, icons
│   └── error.tsx            # Route + global error boundaries
├── src/lib/
│   ├── api.ts               # Typed API client + contract types
│   ├── auth.ts              # Session endpoints (optional auth)
│   ├── chart.ts             # Auto-chart heuristic
│   ├── evidence.ts          # Execution metadata -> the claims the UI is willing to make
│   ├── export.ts            # CSV / JSON / Markdown export
│   ├── history.ts           # localStorage query history
│   ├── rows.ts              # Result-row normalisation
│   └── site.ts              # Canonical origin
├── tests/
│   ├── unit/                # Vitest + Testing Library
│   └── e2e/                 # Playwright, API fully stubbed
└── ...config files          # next.config.mjs, tailwind, tsconfig, vitest, playwright
```

## A note on the copy

Every claim on the marketing page is governed by [`../docs/v2/CLAIM_AUDIT.md`](../docs/v2/CLAIM_AUDIT.md):
no percentage without a numerator and a denominator, no metric without provenance, and no number that
cannot be reproduced from a clean checkout. If you edit user-visible copy, add a row to that document
first.
