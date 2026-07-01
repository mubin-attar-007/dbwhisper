/**
 * Heuristic auto-charting: infer a sensible chart from the shape of a result set.
 *
 * A table of numbers reads as a demo; a chart + summary reads as a product. This picks
 * a KPI (single measure), a time-series line, or a categorical bar — or nothing, when
 * the data doesn't chart cleanly. Purely client-side over the already-returned rows.
 */
import type { Row } from "@/src/lib/rows";

export type ChartSpec =
  | { kind: "kpi"; label: string; value: number }
  | {
      kind: "bar" | "line";
      x: string;
      y: string;
      points: { label: string; value: number }[];
    }
  | null;

function toNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value.replace(/[$,%\s]/g, ""));
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function isNumericColumn(rows: Row[], col: string): boolean {
  let seen = 0;
  for (const row of rows) {
    const v = row[col];
    if (v === null || v === undefined || v === "") continue;
    if (toNumber(v) === null) return false;
    seen += 1;
  }
  return seen > 0;
}

const DATE_HINT = /(date|time|day|month|year|week|_at|created|updated|period|quarter)/i;

function isDateColumn(rows: Row[], col: string): boolean {
  if (!DATE_HINT.test(col)) return false;
  for (const row of rows) {
    const v = row[col];
    if (v === null || v === undefined || v === "") continue;
    if (typeof v === "number") return false; // a numeric year is treated as a measure
    if (!Number.isNaN(Date.parse(String(v)))) return true;
  }
  return false;
}

export function pickChart(rows: Row[], columns: string[]): ChartSpec {
  if (rows.length === 0 || columns.length === 0) return null;

  const numericCols = columns.filter((c) => isNumericColumn(rows, c));
  const dateCols = columns.filter((c) => isDateColumn(rows, c));
  const categoricalCols = columns.filter(
    (c) => !numericCols.includes(c) && !dateCols.includes(c),
  );

  // Single row + a single measure -> headline KPI.
  if (rows.length === 1 && numericCols.length >= 1) {
    const col = numericCols[0];
    const value = toNumber(rows[0][col]);
    if (value !== null) return { kind: "kpi", label: col, value };
  }

  if (numericCols.length === 0) return null;
  const y = numericCols[0];

  // Time-series -> line.
  if (dateCols.length >= 1 && rows.length >= 2 && rows.length <= 400) {
    const x = dateCols[0];
    const points = rows
      .map((row) => ({ label: String(row[x] ?? ""), value: toNumber(row[y]) ?? 0 }))
      .filter((p) => p.label !== "");
    if (points.length >= 2) return { kind: "line", x, y, points };
  }

  // Few categories -> bar.
  if (categoricalCols.length >= 1 && rows.length >= 2 && rows.length <= 30) {
    const x = categoricalCols[0];
    const points = rows.map((row) => ({
      label: String(row[x] ?? ""),
      value: toNumber(row[y]) ?? 0,
    }));
    return { kind: "bar", x, y, points };
  }

  return null;
}
