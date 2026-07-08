"use client";

import { ResultsSkeleton } from "./ResultsSkeleton";

/**
 * Honest working indicator for the (multi-second) query round-trip. Real per-stage
 * streaming isn't wired yet (that's a later SSE phase), so we show a single truthful
 * "working" state plus the real pipeline as context — never a fake per-step timer.
 */
export function StagedProgress() {
  return (
    <div className="space-y-6" role="status" aria-live="polite">
      <div className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        <span
          className="h-4 w-4 flex-none rounded-full border-2 border-indigo-500/40 border-t-indigo-400 motion-safe:animate-spin"
          aria-hidden="true"
        />
        <div>
          <p className="text-sm font-medium text-slate-200">Working on your query…</p>
          <p className="mt-0.5 text-xs text-slate-400">
            Understanding · selecting tables · generating SQL · running · preparing results
          </p>
        </div>
      </div>
      <ResultsSkeleton />
    </div>
  );
}
