import * as Sentry from "@sentry/nextjs";

// Client-side Sentry. No-op unless NEXT_PUBLIC_SENTRY_DSN is set, so dev/unconfigured
// deploys send nothing. PII off (the backend already sanitizes; keep query text out of Sentry).
if (process.env.NEXT_PUBLIC_SENTRY_DSN) {
  Sentry.init({
    dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
    tracesSampleRate: 0.1,
    sendDefaultPii: false,
  });
}

// Instruments App Router client navigations (harmless if Sentry isn't initialized).
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
