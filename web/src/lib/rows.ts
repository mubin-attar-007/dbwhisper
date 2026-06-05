import type { QueryResultData } from "@/src/lib/api";

export type Row = Record<string, unknown>;

function isRowArray(value: unknown): value is Row[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) => item !== null && typeof item === "object" && !Array.isArray(item),
    )
  );
}

/**
 * Resolves the displayable rows for the results table.
 *
 * Prefers `data.results` when it is an array of plain objects. Otherwise it
 * falls back to parsing the `raw_json` string. Returns an empty array when no
 * tabular data can be derived.
 */
export function resolveRows(data: QueryResultData | null | undefined): Row[] {
  if (!data) return [];

  if (isRowArray(data.results)) {
    return data.results as Row[];
  }

  if (data.raw_json) {
    try {
      const parsed = JSON.parse(data.raw_json);
      if (isRowArray(parsed)) {
        return parsed as Row[];
      }
      // Some backends wrap rows under a key (e.g. { results: [...] }).
      if (parsed && typeof parsed === "object") {
        const candidate = (parsed as Record<string, unknown>).results;
        if (isRowArray(candidate)) {
          return candidate as Row[];
        }
      }
    } catch {
      // Ignore malformed JSON and fall through to an empty table.
    }
  }

  return [];
}

/** Derives an ordered, de-duplicated set of column keys across all rows. */
export function deriveColumns(rows: Row[]): string[] {
  const seen = new Set<string>();
  const columns: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key);
        columns.push(key);
      }
    }
  }
  return columns;
}

/** Formats a single cell value for display. */
export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}
