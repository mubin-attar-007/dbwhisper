/**
 * Client-side export helpers for query results (CSV / JSON / Markdown).
 *
 * The backend already returns `data.csv` and `data.raw_json`, so exporting is pure
 * frontend plumbing — we prefer those payloads and fall back to deriving from rows.
 */
import type { QueryResultData } from "@/src/lib/api";
import { deriveColumns, formatCell, resolveRows, type Row } from "@/src/lib/rows";

function triggerDownload(content: string, filename: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function csvEscape(value: string): string {
  return /[",\n\r]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

export function rowsToCsv(rows: Row[], columns: string[]): string {
  const header = columns.map(csvEscape).join(",");
  const lines = rows.map((row) =>
    columns.map((c) => csvEscape(formatCell(row[c]))).join(","),
  );
  return [header, ...lines].join("\r\n");
}

export function rowsToMarkdown(rows: Row[], columns: string[]): string {
  const header = `| ${columns.join(" | ")} |`;
  const sep = `| ${columns.map(() => "---").join(" | ")} |`;
  const body = rows.map(
    (row) =>
      `| ${columns.map((c) => formatCell(row[c]).replace(/\|/g, "\\|")).join(" | ")} |`,
  );
  return [header, sep, ...body].join("\n");
}

function stamp(): string {
  return new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
}

export function exportCsv(data: QueryResultData): void {
  const rows = resolveRows(data);
  const columns = deriveColumns(rows);
  const csv = data.csv && data.csv.trim() ? data.csv : rowsToCsv(rows, columns);
  triggerDownload(csv, `dbwhisper-${stamp()}.csv`, "text/csv;charset=utf-8");
}

export function exportJson(data: QueryResultData): void {
  const rows = resolveRows(data);
  const json =
    data.raw_json && data.raw_json.trim()
      ? data.raw_json
      : JSON.stringify(rows, null, 2);
  triggerDownload(json, `dbwhisper-${stamp()}.json`, "application/json");
}

export function toMarkdown(data: QueryResultData): string {
  const rows = resolveRows(data);
  const columns = deriveColumns(rows);
  return rowsToMarkdown(rows, columns);
}
