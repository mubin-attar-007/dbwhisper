"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ApiError,
  listDatabases,
  runQuery,
  runSql,
  type DatabaseSummary,
  type QueryRequest,
  type QueryResponse,
} from "@/src/lib/api";
import {
  addHistory,
  clearHistory as clearHistoryStore,
  loadHistory,
  type HistoryEntry,
} from "@/src/lib/history";

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
    return { userId: `anon-${crypto.randomUUID()}`, sessionId: crypto.randomUUID() };
  }
}

interface WorkspaceValue {
  query: string;
  setQuery: (v: string) => void;
  dbFlag: string;
  setDbFlag: (v: string) => void;
  databases: DatabaseSummary[];
  loading: boolean;
  error: string | null;
  response: QueryResponse | null;
  history: HistoryEntry[];
  submit: () => Promise<void>;
  runEditedSql: (sql: string) => Promise<void>;
  cancel: () => void;
  newQuery: () => void;
  selectHistory: (entry: HistoryEntry) => void;
  clearHistory: () => void;
}

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

export function useWorkspace(): WorkspaceValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used within <WorkspaceProvider>");
  return ctx;
}

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [query, setQuery] = useState("");
  const [dbFlag, setDbFlag] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<QueryResponse | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [databases, setDatabases] = useState<DatabaseSummary[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setHistory(loadHistory());
  }, []);

  useEffect(() => {
    let cancelled = false;
    listDatabases()
      .then((dbs) => {
        if (cancelled) return;
        setDatabases(dbs);
        setDbFlag((prev) => {
          if (prev.trim()) return prev;
          const preferred =
            dbs.find((d) => d.db_flag === "demo") ?? dbs.find((d) => d.is_public) ?? dbs[0];
          return preferred?.db_flag ?? prev;
        });
      })
      .catch(() => {
        /* endpoint unavailable → keep the free-text fallback */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = useCallback(async () => {
    const trimmedQuery = query.trim();
    const trimmedFlag = dbFlag.trim();
    if (!trimmedQuery) {
      setError("Please enter a question to run.");
      return;
    }
    if (!trimmedFlag) {
      setError("Please choose a database.");
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
        setHistory(addHistory({ query: trimmedQuery, dbFlag: trimmedFlag, ts: Date.now() }));
      }
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setResponse(null);
      if (err instanceof ApiError) setError(err.message);
      else if (err instanceof Error) setError(`Network error: ${err.message}`);
      else setError("An unexpected error occurred.");
    } finally {
      if (abortRef.current === controller) {
        setLoading(false);
        abortRef.current = null;
      }
    }
  }, [query, dbFlag]);

  const runEditedSql = useCallback(
    async (sqlText: string) => {
      const trimmedSql = sqlText.trim();
      const trimmedFlag = dbFlag.trim();
      if (!trimmedSql || !trimmedFlag) return;

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      setError(null);

      const { userId, sessionId } = getConversationIds();
      try {
        const res = await runSql(
          {
            sql: trimmedSql,
            db_flag: trimmedFlag,
            output_format: "json",
            user_id: userId,
            session_id: sessionId,
          },
          controller.signal,
        );
        setResponse(res);
        if (res.status === "error") setError(res.error || "The query returned an error.");
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setResponse(null);
        if (err instanceof ApiError) setError(err.message);
        else if (err instanceof Error) setError(`Network error: ${err.message}`);
        else setError("An unexpected error occurred.");
      } finally {
        if (abortRef.current === controller) {
          setLoading(false);
          abortRef.current = null;
        }
      }
    },
    [dbFlag],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const newQuery = useCallback(() => {
    abortRef.current?.abort();
    setQuery("");
    setResponse(null);
    setError(null);
  }, []);

  const selectHistory = useCallback((entry: HistoryEntry) => {
    setQuery(entry.query);
    setDbFlag(entry.dbFlag);
  }, []);

  const clearHistory = useCallback(() => {
    clearHistoryStore();
    setHistory([]);
  }, []);

  return (
    <WorkspaceContext.Provider
      value={{
        query,
        setQuery,
        dbFlag,
        setDbFlag,
        databases,
        loading,
        error,
        response,
        history,
        submit,
        runEditedSql,
        cancel,
        newQuery,
        selectHistory,
        clearHistory,
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}
