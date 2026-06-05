"use client";

import type { QueryResponse } from "@/src/lib/api";
import { CopyButton } from "./CopyButton";
import { ResultsTable } from "./ResultsTable";

function ValidationBadge({ passed }: { passed: boolean | null }) {
  if (passed === null) return null;
  return passed ? (
    <span className="inline-flex items-center gap-1 rounded-full border border-emerald-700/60 bg-emerald-900/40 px-2.5 py-0.5 text-xs font-medium text-emerald-300">
      validation passed
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full border border-rose-700/60 bg-rose-900/40 px-2.5 py-0.5 text-xs font-medium text-rose-300">
      validation failed
    </span>
  );
}

export function ResultsPanel({
  response,
  onFollowUp,
}: {
  response: QueryResponse;
  onFollowUp: (question: string) => void;
}) {
  const sql = response.sql ?? response.data?.sql ?? null;
  const followUps = response.follow_up_questions ?? [];
  const selectedTables = response.selected_tables ?? [];

  return (
    <div className="space-y-6">
      {response.natural_summary && (
        <div className="rounded-lg border border-indigo-700/50 bg-indigo-950/40 p-4">
          <h2 className="mb-1 text-xs font-semibold uppercase tracking-wide text-indigo-300">
            Summary
          </h2>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-indigo-100">
            {response.natural_summary}
          </p>
        </div>
      )}

      {sql && (
        <section aria-label="Generated SQL" className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                Generated SQL
              </h2>
              <ValidationBadge passed={response.validation_passed} />
            </div>
            <CopyButton value={sql} label="Copy SQL" />
          </div>
          <pre className="scrollbar-thin overflow-auto rounded-lg border border-slate-800 bg-slate-900/80 p-4 font-mono text-sm leading-relaxed text-slate-200">
            <code>{sql}</code>
          </pre>
        </section>
      )}

      {selectedTables.length > 0 && (
        <section aria-label="Selected tables" className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Selected tables
          </h2>
          <div className="flex flex-wrap gap-2">
            {selectedTables.map((table) => (
              <span
                key={table}
                className="inline-flex items-center rounded-md border border-slate-700 bg-slate-800/70 px-2.5 py-1 font-mono text-xs text-slate-300"
              >
                {table}
              </span>
            ))}
          </div>
        </section>
      )}

      {response.data && <ResultsTable data={response.data} />}

      {followUps.length > 0 && (
        <section aria-label="Follow-up questions" className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Follow-up questions
          </h2>
          <div className="flex flex-wrap gap-2">
            {followUps.map((question, i) => (
              <button
                key={`${i}-${question}`}
                type="button"
                onClick={() => onFollowUp(question)}
                className="inline-flex items-center rounded-full border border-indigo-700/60 bg-indigo-950/30 px-3 py-1 text-left text-xs text-indigo-200 transition hover:border-indigo-500 hover:bg-indigo-900/40 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                {question}
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
