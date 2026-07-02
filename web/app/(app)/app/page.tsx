"use client";

import { useCallback, useRef } from "react";
import { ResultsPanel } from "../../components/ResultsPanel";
import { StagedProgress } from "../../components/StagedProgress";
import { useWorkspace } from "../../components/WorkspaceProvider";

const EXAMPLE_QUERIES = [
  "How many customers signed up in the last 30 days?",
  "Top 5 products by total revenue",
  "Show order counts by status this month",
  "Which city has the most customers?",
];

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-slate-700 bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-slate-300">
      {children}
    </kbd>
  );
}

/** Turn raw backend/DB errors into a plain-English, recoverable message. */
function humanizeError(message: string): string {
  const m = message.toLowerCase();
  if (m.includes("timeout") || m.includes("timed out"))
    return "The query took too long. Try narrowing it — add a filter or a smaller date range.";
  if (
    m.includes("permission") ||
    m.includes("denied") ||
    m.includes("not allowed") ||
    m.includes("read-only")
  )
    return "That action isn't permitted. DBWhisper is read-only, so it only answers SELECT-style questions.";
  if (
    m.includes("no such") ||
    m.includes("not found") ||
    m.includes("unknown database") ||
    m.includes("does not exist")
  )
    return `${message.trim()} — double-check the database.`;
  if (m.includes("connect") || m.includes("network") || m.includes("failed to fetch"))
    return "Couldn't reach the database. It may be waking up — try again in a moment.";
  return message.trim();
}

export default function ConsolePage() {
  const { query, setQuery, loading, error, response, submit, cancel, runEditedSql } =
    useWorkspace();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        void submit();
      }
    },
    [submit],
  );

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      void submit();
    },
    [submit],
  );

  const handleFollowUp = useCallback(
    (question: string) => {
      setQuery(question);
      if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
      textareaRef.current?.focus({ preventScroll: true });
    },
    [setQuery],
  );

  const handleExample = useCallback(
    (question: string) => {
      setQuery(question);
      textareaRef.current?.focus();
    },
    [setQuery],
  );

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8 sm:px-6">
      <div className="motion-safe:animate-fade-up">
        <h1 className="text-xl font-semibold tracking-tight text-slate-100">Ask your database</h1>
        <p className="mt-1 text-sm text-slate-400">
          Plain-English in, validated <span className="text-slate-300">read-only</span> SQL and
          results out.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="space-y-3 motion-safe:animate-fade-up"
        style={{ animationDelay: "75ms" }}
      >
        <textarea
          id="query"
          ref={textareaRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={4}
          placeholder="e.g. How many active customers signed up last month?"
          className="scrollbar-thin w-full resize-y rounded-lg border border-slate-700 bg-slate-900/70 px-3.5 py-3 text-sm text-slate-100 shadow-sm placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-slate-500">
            <Kbd>Ctrl</Kbd>/<Kbd>⌘</Kbd> + <Kbd>Enter</Kbd> to run
          </p>
          <div className="flex gap-2">
            {loading && (
              <button
                type="button"
                onClick={cancel}
                className="inline-flex items-center justify-center rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-300 transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
              >
                Cancel
              </button>
            )}
            <button
              type="submit"
              disabled={loading}
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-5 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading && (
                <span
                  className="h-4 w-4 rounded-full border-2 border-white/40 border-t-white motion-safe:animate-spin"
                  aria-hidden="true"
                />
              )}
              {loading ? "Running…" : "Run query"}
            </button>
          </div>
        </div>
      </form>

      <div
        aria-busy={loading}
        className="space-y-6 motion-safe:animate-fade-up"
        style={{ animationDelay: "150ms" }}
      >
        <p role="status" className="sr-only">
          {loading
            ? "Running query…"
            : response?.data
              ? `Query complete: ${response.data.row_count.toLocaleString()} row${
                  response.data.row_count === 1 ? "" : "s"
                }`
              : ""}
        </p>

        {error && (
          <div
            role="alert"
            className="space-y-3 rounded-lg border border-rose-700/60 bg-rose-950/40 p-4 text-sm text-rose-200"
          >
            <p>
              <span className="font-semibold">Something went wrong. </span>
              {humanizeError(error)}
            </p>
            <button
              type="button"
              onClick={() => void submit()}
              className="inline-flex items-center gap-1.5 rounded-md border border-rose-700/60 bg-rose-900/40 px-3 py-1.5 text-xs font-semibold text-rose-100 transition hover:bg-rose-900/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
            >
              ↻ Try again
            </button>
          </div>
        )}

        {loading && <StagedProgress />}

        {!loading && response && response.status === "success" && (
          <ResultsPanel response={response} onFollowUp={handleFollowUp} onRerunSql={runEditedSql} />
        )}

        {!response && !error && !loading && (
          <div className="space-y-3">
            <h2 className="text-sm font-medium text-slate-300">Try one of these</h2>
            <div className="flex flex-wrap gap-2">
              {EXAMPLE_QUERIES.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => handleExample(question)}
                  className="inline-flex items-center rounded-full border border-indigo-700/60 bg-indigo-950/30 px-3 py-1 text-left text-xs text-indigo-200 transition hover:border-indigo-500 hover:bg-indigo-900/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                >
                  {question}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
