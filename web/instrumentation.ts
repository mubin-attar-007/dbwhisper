import * as Sentry from "@sentry/nextjs";

// Server/edge Sentry init. No-op unless a DSN is configured.
export async function register() {
  const dsn = process.env.SENTRY_DSN ?? process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;
  if (process.env.NEXT_RUNTIME === "nodejs" || process.env.NEXT_RUNTIME === "edge") {
    Sentry.init({ dsn, tracesSampleRate: 0.1, sendDefaultPii: false });
  }
}

// Captures errors thrown in Server Components / route handlers.
export const onRequestError = Sentry.captureRequestError;
