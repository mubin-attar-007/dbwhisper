"use client";

/**
 * Says out loud that you are not looking at the whole answer.
 *
 * A silently capped result set is the most dangerous thing this UI can render: "3 orders failed
 * last month" reads as a fact, not as "the first 1,000 rows contained 3 failures". The backend
 * reports the cap, so the frontend refuses to draw a truncated table without this banner above it.
 *
 * It renders only when truncation was actually reported as true — an absent field means the
 * backend did not say, and inventing an "everything is fine" message from silence is the same
 * mistake in the other direction.
 */

import type { QueryResponse } from "@/src/lib/api";
import { resolveTruncation } from "@/src/lib/evidence";

export function TruncationBanner({ response }: { response: QueryResponse }) {
  const { truncated, rowLimit } = resolveTruncation(response);
  if (!truncated) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="truncation-banner"
      className="rounded-lg border border-amber-600/60 bg-amber-950/40 p-4 text-sm text-amber-100"
    >
      <p className="font-semibold">Showing a partial result.</p>
      <p className="mt-1 leading-relaxed text-amber-200/90">
        {rowLimit
          ? `The row cap of ${rowLimit.toLocaleString()} was reached, so rows matching your question are not shown here.`
          : "The row cap was reached, so rows matching your question are not shown here."}{" "}
        Totals, counts and charts below describe only the rows returned. Narrow the question — add a
        filter, a date range, or an explicit aggregate — to get a complete answer.
      </p>
    </div>
  );
}
