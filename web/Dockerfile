# syntax=docker/dockerfile:1
#
# The DBWhisper web image: the marketing page and the query console (Next.js 15, App Router).
#
# Four decisions are encoded here, and as in `Dockerfile` and `Dockerfile.worker` each one was a
# choice between defensible options rather than the only way to do it:
#
# 1. **`API_PROXY_TARGET` is a build ARG, not a runtime environment variable.** This is the one
#    thing about this image that surprises people, so it is the first thing documented. The browser
#    never calls the API directly: it calls this origin at `/api/*` and `next.config.mjs` rewrites
#    that to the backend. But Next evaluates `rewrites()` during `next build` and freezes the
#    resolved destination into `.next/routes-manifest.json`; neither `next start` nor the standalone
#    server re-reads the config. Setting `API_PROXY_TARGET` on a *running* container therefore does
#    nothing at all - the container comes up healthy and every `/api/*` call still goes wherever the
#    build pointed it. Passing it as a build arg makes that visible instead of silent. Verify what a
#    given image actually points at, rather than trusting the tag:
#
#      docker run --rm --entrypoint node <image> \
#        -e 'console.log(require("./.next/routes-manifest.json").rewrites.afterFiles)'
#
#    The default is `http://api:8000` - the compose service, not `next.config.mjs`'s
#    `http://localhost:8000`, because inside a container `localhost` is the container itself and
#    that default can never be right here.
#
# 2. **The `NEXT_PUBLIC_*` args exist because those values are inlined into the client bundle at
#    build time.** They are configuration, not secrets: everything prefixed `NEXT_PUBLIC_` is
#    readable in the shipped JavaScript by design, and a build arg is readable in `docker history`.
#    Those two facts agree, which is why it is safe to pass these this way and why no real secret
#    may ever join them. There is nothing to pass here that the browser does not already receive.
#
# 3. **`NEXT_PUBLIC_SITE_URL` gets a concrete default rather than being left empty.** `src/lib/site.ts`
#    reads it with `??`, so an *empty* string wins over the code default and `new URL("")` in
#    `app/layout.tsx` throws - an empty build arg would fail the build rather than fall back. The
#    default below is the address compose publishes, since that is where this image is designed to
#    run; a real deployment passes its own origin.
#
# 4. **Node 22, while CI builds on Node 20.** `Dockerfile` pins uv to the version CI uses precisely
#    so the two agree, and this diverges from that principle on purpose: Node 20 left security
#    support in April 2026 and shipping an end-of-life runtime in the image that faces the internet
#    is the worse trade. Next 15.5 supports both. The gap is worth closing by moving CI to 22
#    (.github/workflows/ci.yml), not by moving this back to 20.
#
# No GPU, no network calls at runtime beyond the API proxy, and telemetry is off during the build.

# ── deps: install the locked tree, cached independently of the source ────────────────────────────
FROM node:22-alpine3.22 AS deps
WORKDIR /app

# Manifest and lock only, so editing a component does not re-run the install. `npm ci` (not
# `npm install`) because it fails on a lockfile that disagrees with package.json instead of quietly
# resolving something new, and dev dependencies are kept: typescript, tailwind, postcss and
# eslint-config-next all run during `next build`.
COPY package.json package-lock.json ./
RUN npm ci

# ── builder: produce .next/standalone ────────────────────────────────────────────────────────────
FROM node:22-alpine3.22 AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# See decisions (1)-(3) in the header for why each of these is a build arg.
ARG API_PROXY_TARGET=http://api:8000
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ARG NEXT_PUBLIC_AUTH_ENABLED=
ARG NEXT_PUBLIC_SENTRY_DSN=

ENV API_PROXY_TARGET=${API_PROXY_TARGET} \
    NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL} \
    NEXT_PUBLIC_AUTH_ENABLED=${NEXT_PUBLIC_AUTH_ENABLED} \
    NEXT_PUBLIC_SENTRY_DSN=${NEXT_PUBLIC_SENTRY_DSN} \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

RUN npm run build

# ── runtime ──────────────────────────────────────────────────────────────────────────────────────
FROM node:22-alpine3.22 AS web

LABEL org.opencontainers.image.title="DBWhisper web" \
      org.opencontainers.image.description="Next.js console and marketing site for the DBWhisper API." \
      org.opencontainers.image.source="https://github.com/mubin-attar-007/dbwhisper" \
      org.opencontainers.image.licenses="MIT"

# HOSTNAME is what the standalone server binds. It has defaulted to `localhost` on some Next
# releases, and bound to the loopback interface the server answers from inside the container while
# refusing every published port - which looks exactly like a crashed app. Set explicitly so that a
# change in the generated server.js cannot reintroduce it.
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app

# No apt/apk layer at all: the standalone bundle needs nothing the base image does not already have,
# and busybox `wget` covers the HEALTHCHECK below, so there is no curl to install. Compare the ODBC
# stack in `Dockerfile` - the web image touches no database and inherits none of that surface.

# node:*-alpine already ships a `node` user at uid 1000, so this reuses it rather than adding a
# second uid-1000 account (useradd would fail on the collision). Same uid as `appuser` in the api
# and worker images, which keeps ownership consistent if a volume is ever shared across them.
#
# WORKDIR was created by root and stays root-owned; only the copied trees below are chowned. So
# /app/.next is writable by the app and /app itself is not, which is the right way round. Nothing
# here writes at runtime today - there is no ISR and no next/image, whose optimiser is the usual
# reason a standalone container needs a writable .next/cache.
USER node

# The standalone tree carries server.js, its own package.json and only the traced node_modules.
# The two things it deliberately does NOT carry, and so must be copied alongside it:
#   .next/static - hashed JS/CSS, which server.js serves but the tracer does not treat as an import;
#   public/      - does not exist in this app (icons, robots and the OG image are generated route
#                  handlers under app/), which is why there is no COPY for it. Add one if that
#                  changes: a missing public/ is silent, it just 404s.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static

EXPOSE 3000

# `/robots.txt` rather than `/`: both are prerendered and either would prove the Node process is
# answering, but the landing page is ~88 KB per probe against a few hundred bytes, and this runs
# every 30s for the life of the container. What matters more is what is NOT probed: nothing under
# `/api/*`. That path proxies to the backend, so probing it would mark this container unhealthy
# whenever the API is down - reporting the backend's health under the frontend's name, and taking
# the web container down with it. Shell form so ${PORT} expands, as in `Dockerfile`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${PORT:-3000}/robots.txt" || exit 1

# Next's standalone server installs its own SIGTERM/SIGINT handlers, so it stops on `docker stop`
# without an init shim despite running as pid 1 (where unhandled signals are ignored rather than
# defaulted). It holds no lease and finishes in-flight requests quickly; the default 10s grace
# period is enough, unlike the worker's.
STOPSIGNAL SIGTERM

CMD ["node", "server.js"]
