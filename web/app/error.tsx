"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
    Sentry.captureException(error); // no-op unless a DSN is configured
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-6 text-center">
      <h2 className="text-xl font-semibold text-slate-100">Something went wrong</h2>
      <p className="max-w-md text-sm text-slate-400">
        An unexpected error occurred while rendering this page. Try again, and reload if it
        persists.
      </p>
      <button
        onClick={() => reset()}
        className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-500"
      >
        Try again
      </button>
    </div>
  );
}
