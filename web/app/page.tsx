"use client";

import { useCallback, useRef, useState } from "react";
import {
  ApiError,
  runQuery,
  type QueryRequest,
  type QueryResponse,
} from "@/src/lib/api";
import { HealthBadge } from "./components/HealthBadge";
import { ResultsPanel } from "./components/ResultsPanel";

export default function Home() {
  const [query, setQuery] = useState("");
  const [dbFlag, setDbFlag] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<QueryResponse | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  const submit = useCallback(async () => {
    const trimmedQuery = query.trim();
    const trimmedFlag = dbFlag.trim();

    if (!trimmedQuery) {
      setError("Please enter a question to run.");
      return;
    }
    if (!trimmedFlag) {
      setError("Please enter a target database (db_flag).");
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);

    const req: QueryRequest = {
      query: trimmedQuery,
      db_flag: trimmedFlag,
      output_format: "json",
    };

    try {
      const res = await runQuery(req, controller.signal);
      setResponse(res);
      if (res.status === "error") {
        setError(res.error || "The query returned an error.");
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      setResponse(null);
      if (err instanceof ApiError) {
        setError(err.message);
      } else if (err instanceof Error) {
        setError(`Network error: ${err.message}`);
      } else {
        setError("An unexpected error occurred.");
      }
    } finally {
      if (abortRef.current === controller) {
        setLoading(false);
        abortRef.current = null;
      }
    }
  }, [query, dbFlag]);

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

  const handleFollowUp = useCallback((question: string) => {
    setQuery(question);
    if (typeof window !== "undefined") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, []);

  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col gap-8 px-4 py-8 sm:px-6 lg:py-12">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
            DB<span className="text-indigo-400">Whisper</span>
          </h1>
          <span className="hidden text-sm text-slate-500 sm:inline">
            natural language → SQL
          </span>
        </div>
        <HealthBadge />
      </header>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label
            htmlFor="query"
            className="mb-1.5 block text-sm font-medium text-slate-300"
          >
            Your question
          </label>
          <textarea
            id="query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={4}
            placeholder="e.g. How many active customers signed up last month?"
            className="scrollbar-thin w-full resize-y rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
          <p className="mt-1 text-xs text-slate-500">
            Press{" "}
            <kbd className="rounded border border-slate-700 bg-slate-800 px-1 py-0.5 font-mono text-[10px] text-slate-300">
              Ctrl
            </kbd>{" "}
            /{" "}
            <kbd className="rounded border border-slate-700 bg-slate-800 px-1 py-0.5 font-mono text-[10px] text-slate-300">
              ⌘
            </kbd>{" "}
            +{" "}
            <kbd className="rounded border border-slate-700 bg-slate-800 px-1 py-0.5 font-mono text-[10px] text-slate-300">
              Enter
            </kbd>{" "}
            to run.
          </p>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="sm:max-w-xs sm:flex-1">
            <label
              htmlFor="db_flag"
              className="mb-1.5 block text-sm font-medium text-slate-300"
            >
              Database
            </label>
            <input
              id="db_flag"
              type="text"
              value={dbFlag}
              onChange={(e) => setDbFlag(e.target.value)}
              placeholder="crm_db"
              autoComplete="off"
              spellCheck={false}
              className="w-full rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2.5 font-mono text-sm text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 focus:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading && (
              <span
                className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
                aria-hidden="true"
              />
            )}
            {loading ? "Running…" : "Run query"}
          </button>
        </div>
      </form>

      {error && (
        <div
          role="alert"
          className="rounded-lg border border-rose-700/60 bg-rose-950/40 p-4 text-sm text-rose-200"
        >
          <span className="font-semibold">Error: </span>
          {error}
        </div>
      )}

      {response && response.status === "success" && (
        <ResultsPanel response={response} onFollowUp={handleFollowUp} />
      )}

      {!response && !error && !loading && (
        <div className="rounded-lg border border-dashed border-slate-800 bg-slate-900/30 p-8 text-center text-sm text-slate-500">
          Ask a question above to generate SQL and see results.
        </div>
      )}
    </main>
  );
}
