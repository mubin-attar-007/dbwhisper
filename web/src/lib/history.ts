/**
 * Local query history persisted to localStorage. Client-only: every function
 * guards on `typeof window` and swallows storage/parse errors, so callers can
 * use it unconditionally (it just no-ops during SSR or with storage disabled).
 */

export interface HistoryEntry {
  query: string;
  dbFlag: string;
  ts: number;
}

const KEY = "dbwhisper.history.v1";
const MAX = 20;

function isHistoryEntry(value: unknown): value is HistoryEntry {
  if (!value || typeof value !== "object") return false;
  const obj = value as Record<string, unknown>;
  return (
    typeof obj.query === "string" &&
    typeof obj.dbFlag === "string" &&
    typeof obj.ts === "number"
  );
}

/** Reads the saved history, dropping anything that doesn't match the shape. */
export function loadHistory(): HistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isHistoryEntry).slice(0, MAX);
  } catch {
    return [];
  }
}

/**
 * Prepends an entry (deduped on query + dbFlag, capped at MAX), persists, and
 * returns the updated list so callers can mirror it into state.
 */
export function addHistory(entry: HistoryEntry): HistoryEntry[] {
  const next = [
    entry,
    ...loadHistory().filter(
      (e) => e.query !== entry.query || e.dbFlag !== entry.dbFlag,
    ),
  ].slice(0, MAX);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      // Storage full or unavailable — the in-memory list still works.
    }
  }
  return next;
}

export function clearHistory(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // Nothing to clean up if storage is unavailable.
  }
}
