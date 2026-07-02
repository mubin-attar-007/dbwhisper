"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  runQuery,
  type QueryRequest,
  type QueryResponse,
} from "@/src/lib/api";
import {
  addHistory,
  clearHistory,
  loadHistory,
  type HistoryEntry,
} from "@/src/lib/history";
import { HealthBadge } from "./components/HealthBadge";
import { HistoryMenu } from "./components/HistoryMenu";
import { ResultsPanel } from "./components/ResultsPanel";
import { StagedProgress } from "./components/StagedProgress";

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
    return `${message.trim()} — double-check the database name.`;
  if (m.includes("connect") || m.includes("network") || m.includes("failed to fetch"))
    return "Couldn't reach the database. It may be waking up — try again in a moment.";
  return message.trim();
}

/** Stable per-browser (anon) + per-tab identifiers so the backend keeps conversation memory. */
function getConversationIds(): { userId: string; sessionId: string } {
  if (typeof window === "undefined") return { userId: "", sessionId: "" };
  try {
    let userId = localStorage.getItem("dbwhisper.uid") ?? "";
    if (!userId) {
      userId = `anon-${crypto.randomUUID()}`;
      localStorage.setItem("dbwhisper.uid", userId);
    }
    let sessionId = sessionStorage.getItem("dbwhisper.sid") ?? "";
    if (!sessionId) {
      sessionId = crypto.randomUUID();
      sessionStorage.setItem("dbwhisper.sid", sessionId);
    }
    return { userId, sessionId };
  } catch {
    // Storage unavailable (e.g. private mode) — fall back to ephemeral ids.
    return { userId: `anon-${crypto.randomUUID()}`, sessionId: crypto.randomUUID() };
  }
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [dbFlag, setDbFlag] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<QueryResponse | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  const abortRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    setHistory(loadHistory());
  }, []);

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

    const { userId, sessionId } = getConversationIds();
    const req: QueryRequest = {
      query: trimmedQuery,
      db_flag: trimmedFlag,
      output_format: "json",
      user_id: userId,
      session_id: sessionId,
    };

    try {
      const res = await runQuery(req, controller.signal);
      setResponse(res);
      if (res.status === "error") {
        setError(res.error || "The query returned an error.");
      } else {
        setHistory(
          addHistory({ query: trimmedQuery, dbFlag: trimmedFlag, ts: Date.now() }),
        );
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
    textareaRef.current?.focus({ preventScroll: true });
  }, []);

  const handleExample = useCallback((question: string) => {
    setQuery(question);
    setDbFlag((prev) => (prev.trim() ? prev : "demo"));
    textareaRef.current?.focus();
  }, []);

  const handleHistorySelect = useCallback((entry: HistoryEntry) => {
    setQuery(entry.query);
    setDbFlag(entry.dbFlag);
    textareaRef.current?.focus();
  }, []);

  const handleHistoryClear = useCallback(() => {
    clearHistory();
    setHistory([]);
  }, []);

  return (
    <main className="mx-auto flex max-w-4xl flex-col gap-8 px-4 py-8 sm:px-6 lg:py-12">
      <header className="space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-lg font-bold tracking-tight text-white">
            DB<span className="text-brand-fg">Whisper</span>
          </p>
          <HealthBadge />
        </div>

        <div
          className="motion-safe:animate-fade-up"
          style={{ animationDelay: "0ms" }}
        >
          <h1 className="bg-gradient-to-r from-white via-brand-fg to-brand bg-clip-text text-3xl font-bold tracking-tight text-transparent sm:text-4xl">
            Ask your database anything.
          </h1>
          <p className="mt-2 text-sm text-slate-400">
            Plain-English questions in, validated SQL and results out — no query
            language required.{" "}
            <a
              href="/api/docs"
              target="_blank"
              rel="noopener noreferrer"
              className="rounded text-indigo-400 transition hover:text-indigo-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
            >
              API docs →
            </a>
          </p>
        </div>
      </header>

      <form
        onSubmit={handleSubmit}
        className="space-y-4 motion-safe:animate-fade-up"
        style={{ animationDelay: "75ms" }}
      >
        <div className="relative">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <label
              htmlFor="query"
              className="block text-sm font-medium text-slate-300"
            >
              Your question
            </label>
            <HistoryMenu
              entries={history}
              onSelect={handleHistorySelect}
              onClear={handleHistoryClear}
            />
          </div>
          <textarea
            id="query"
            ref={textareaRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={4}
            placeholder="e.g. How many active customers signed up last month?"
            className="scrollbar-thin w-full resize-y rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
          <p className="mt-1 text-xs text-slate-400">
            <Kbd>Ctrl</Kbd>/<Kbd>⌘</Kbd> + <Kbd>Enter</Kbd> to run
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

          <div className="flex gap-3">
            <button
              type="submit"
              disabled={loading}
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading && (
                <span
                  className="h-4 w-4 rounded-full border-2 border-white/40 border-t-white motion-safe:animate-spin"
                  aria-hidden="true"
                />
              )}
              {loading ? "Running…" : "Run query"}
            </button>

            {loading && (
              <button
                type="button"
                onClick={() => abortRef.current?.abort()}
                className="inline-flex items-center justify-center rounded-lg border border-slate-700 px-5 py-2.5 text-sm font-semibold text-slate-300 transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
              >
                Cancel
              </button>
            )}
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
          <ResultsPanel response={response} onFollowUp={handleFollowUp} />
        )}

        {!response && !error && !loading && (
          <div className="space-y-3">
            <h2 className="text-sm font-medium text-slate-300">
              Try one of these
            </h2>
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
    </main>
  );
}
