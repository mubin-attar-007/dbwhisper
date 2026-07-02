"use client";

import { useState } from "react";
import { saveVerifiedPair, type QueryResponse } from "@/src/lib/api";
import { CopyButton } from "./CopyButton";
import { ResultChart } from "./ResultChart";
import { ResultsTable } from "./ResultsTable";
import { useWorkspace } from "./WorkspaceProvider";

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
  onRerunSql,
}: {
  response: QueryResponse;
  onFollowUp: (question: string) => void;
  onRerunSql?: (sql: string) => void;
}) {
  const sql = response.sql ?? response.data?.sql ?? null;
  const followUps = response.follow_up_questions ?? [];
  const selectedTables = response.selected_tables ?? [];
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const { query, dbFlag } = useWorkspace();
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");

  async function saveVerified() {
    if (!sql || !query.trim() || !dbFlag.trim()) return;
    setSaveState("saving");
    try {
      await saveVerifiedPair({ db_flag: dbFlag, question: query.trim(), sql });
      setSaveState("saved");
    } catch {
      setSaveState("error");
    }
  }

  return (
    <div className="space-y-6">
      {/* Lead with the plain-language answer. */}
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

      {/* Auto-chart (renders only when the data charts cleanly). */}
      {response.data && <ResultChart data={response.data} />}

      {/* The data. */}
      {response.data && <ResultsTable data={response.data} />}

      {/* The SQL — never hidden, shown open but collapsible (trust mechanic). */}
      {sql && (
        <details
          open
          className="group rounded-lg border border-slate-800 bg-slate-900/40"
        >
          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-slate-400 marker:content-none [&::-webkit-details-marker]:hidden">
            <span className="text-slate-500 transition-transform group-open:rotate-90">
              ▸
            </span>
            Generated SQL
            <ValidationBadge passed={response.validation_passed} />
          </summary>
          <div className="space-y-3 border-t border-slate-800 p-4">
            <div className="flex items-center justify-end gap-2">
              {!editing && (
                <button
                  type="button"
                  onClick={saveVerified}
                  disabled={saveState === "saving" || saveState === "saved"}
                  title="Save this question + SQL as a verified example so the agent reuses it."
                  className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed ${
                    saveState === "saved"
                      ? "border-emerald-700/60 bg-emerald-900/20 text-emerald-300"
                      : "border-slate-700 bg-slate-800 text-slate-200 hover:bg-slate-700"
                  }`}
                >
                  {saveState === "saved"
                    ? "✓ Verified"
                    : saveState === "saving"
                      ? "Saving…"
                      : saveState === "error"
                        ? "Retry save"
                        : "👍 Save as verified"}
                </button>
              )}
              {onRerunSql && !editing && (
                <button
                  type="button"
                  onClick={() => {
                    setDraft(sql ?? "");
                    setEditing(true);
                  }}
                  className="inline-flex items-center gap-1.5 rounded-md border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                >
                  Edit &amp; run
                </button>
              )}
              <CopyButton value={sql} label="Copy SQL" />
            </div>
            {editing ? (
              <div className="space-y-2">
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  rows={Math.min(14, Math.max(4, draft.split("\n").length + 1))}
                  spellCheck={false}
                  aria-label="Edit SQL"
                  className="scrollbar-thin w-full resize-y rounded-lg border border-slate-700 bg-slate-950/60 p-4 font-mono text-sm leading-relaxed text-slate-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setEditing(false);
                      onRerunSql?.(draft);
                    }}
                    disabled={!draft.trim()}
                    className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    ▸ Run this SQL
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditing(false)}
                    className="inline-flex items-center rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                  >
                    Cancel
                  </button>
                  <span className="text-xs text-slate-500">
                    Runs through the same read-only validator — writes are always rejected.
                  </span>
                </div>
              </div>
            ) : (
              <pre className="scrollbar-thin overflow-auto rounded-lg border border-slate-800 bg-slate-950/60 p-4 font-mono text-sm leading-relaxed text-slate-200">
                <code>{sql}</code>
              </pre>
            )}
            {selectedTables.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Tables used
                </p>
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
              </div>
            )}
          </div>
        </details>
      )}

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
                className="inline-flex items-center rounded-full border border-indigo-700/60 bg-indigo-950/30 px-3 py-1 text-left text-xs text-indigo-200 transition hover:border-indigo-500 hover:bg-indigo-900/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
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
