"use client";

import { useEffect, useState } from "react";
import { ResultsSkeleton } from "./ResultsSkeleton";

const STAGES = [
  "Understanding your question",
  "Selecting relevant tables",
  "Generating SQL",
  "Running the query",
  "Preparing results",
];

/**
 * Optimistic staged progress for the (multi-second) query round-trip. The stages are
 * timed, not streamed — a later phase can wire real SSE stages. Perceived-performance
 * only: it makes a slow wait feel intentional instead of a bare spinner.
 */
export function StagedProgress() {
  const [active, setActive] = useState(0);

  useEffect(() => {
    // Advance through stages, then hold on the last one until the real response lands.
    const timers = STAGES.slice(1, -1).map((_, i) =>
      window.setTimeout(() => setActive(i + 1), (i + 1) * 1400),
    );
    const last = window.setTimeout(
      () => setActive(STAGES.length - 1),
      (STAGES.length - 1) * 1400,
    );
    return () => {
      timers.forEach((t) => window.clearTimeout(t));
      window.clearTimeout(last);
    };
  }, []);

  return (
    <div className="space-y-6">
      <ol
        className="space-y-2.5 rounded-lg border border-slate-800 bg-slate-900/40 p-4"
        aria-label="Query progress"
      >
        {STAGES.map((stage, i) => {
          const done = i < active;
          const current = i === active;
          return (
            <li key={stage} className="flex items-center gap-3 text-sm">
              <span
                className={`flex h-5 w-5 flex-none items-center justify-center rounded-full border text-[10px] ${
                  done
                    ? "border-emerald-600 bg-emerald-900/50 text-emerald-300"
                    : current
                      ? "border-indigo-500 text-indigo-300"
                      : "border-slate-700 text-slate-600"
                }`}
              >
                {done ? (
                  "✓"
                ) : current ? (
                  <span className="h-2 w-2 rounded-full bg-indigo-400 motion-safe:animate-pulse" />
                ) : (
                  i + 1
                )}
              </span>
              <span
                className={
                  done ? "text-slate-400" : current ? "text-slate-100" : "text-slate-600"
                }
              >
                {stage}
              </span>
            </li>
          );
        })}
      </ol>
      <ResultsSkeleton />
    </div>
  );
}
