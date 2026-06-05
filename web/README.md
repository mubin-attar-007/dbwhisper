# DBWhisper Web

A production-ready Next.js (App Router) frontend for the **DBWhisper** natural-language-to-SQL API. It provides a single-page console where you ask questions in plain English and get back the generated SQL, a natural-language summary, and the result set as a table.

## Stack

- Next.js 15 (App Router) + React 18 + TypeScript (strict)
- Tailwind CSS v3 + PostCSS + autoprefixer
- No UI component library — hand-rolled components with Tailwind
- Package manager: npm

## Getting started

```bash
npm install
npm run dev
```

Then open <http://localhost:3000>.

### Environment

The frontend talks to the DBWhisper API at `NEXT_PUBLIC_API_BASE_URL`.

| Variable                   | Default                 | Description                          |
| -------------------------- | ----------------------- | ------------------------------------ |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Base origin of the DBWhisper backend |

Copy the example file and adjust as needed:

```bash
cp .env.example .env.local
```

Because the variable is prefixed with `NEXT_PUBLIC_`, it is inlined into the client bundle **at build time**. When deploying, set it before/at build and rebuild if it changes.

## Scripts

| Script              | Description                          |
| ------------------- | ------------------------------------ |
| `npm run dev`       | Start the dev server                 |
| `npm run build`     | Production build                     |
| `npm run start`     | Serve the production build           |
| `npm run lint`      | ESLint (`next/core-web-vitals`)      |
| `npm run typecheck` | `tsc --noEmit` (strict type check)   |

## API contract

- `GET /health` → `{ status, message, version }`
- `POST /query` with a JSON body (`query`, `db_flag`, `output_format: "json"`, optional `user_id`, `session_id`, `page`, `page_size`, `include_total`) → a `QueryResponse`.

All types live in [`src/lib/api.ts`](./src/lib/api.ts).

## Deploying to Vercel

1. Import the repository in Vercel.
2. Set **Root Directory** to `web` (this folder).
3. Add the environment variable `NEXT_PUBLIC_API_BASE_URL` and point it at the deployed API origin (e.g. `https://your-dbwhisper-api.example.com`).
4. Build command `npm run build` and output are auto-detected for Next.js.

> The backend must allow CORS from the Vercel domain for browser requests to succeed.

## Project structure

```text
web/
├── app/
│   ├── components/      # HealthBadge, ResultsPanel, ResultsTable, CopyButton
│   ├── globals.css      # Tailwind directives + small theme tweaks
│   ├── layout.tsx       # Root layout (dark theme)
│   └── page.tsx         # The NL → SQL console
├── src/lib/
│   ├── api.ts           # Typed API client + contract types
│   └── rows.ts          # Result-row normalization helpers
└── ...config files
```
