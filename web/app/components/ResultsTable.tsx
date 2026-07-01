"use client";

import { useMemo, useState } from "react";
import type { QueryResultData } from "@/src/lib/api";
import { deriveColumns, formatCell, resolveRows } from "@/src/lib/rows";
import { ExportMenu } from "./ExportMenu";

type Sort = { col: string; dir: "asc" | "desc" } | null;

function compareValues(a: unknown, b: unknown): number {
  const na = typeof a === "number" ? a : Number(a);
  const nb = typeof b === "number" ? b : Number(b);
  const bothNumeric =
    a !== null &&
    a !== undefined &&
    a !== "" &&
    b !== null &&
    b !== undefined &&
    b !== "" &&
    Number.isFinite(na) &&
    Number.isFinite(nb);
  if (bothNumeric) return na - nb;
  return formatCell(a).localeCompare(formatCell(b), undefined, { numeric: true });
}

export function ResultsTable({ data }: { data: QueryResultData }) {
  const rows = useMemo(() => resolveRows(data), [data]);
  const columns = useMemo(() => deriveColumns(rows), [rows]);
  const [sort, setSort] = useState<Sort>(null);

  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const copy = [...rows];
    copy.sort((r1, r2) => {
      const c = compareValues(r1[sort.col], r2[sort.col]);
      return sort.dir === "asc" ? c : -c;
    });
    return copy;
  }, [rows, sort]);

  function toggleSort(col: string) {
    setSort((prev) => {
      if (prev?.col !== col) return { col, dir: "asc" };
      return prev.dir === "asc" ? { col, dir: "desc" } : null;
    });
  }

  const execLabel =
    typeof data.execution_time_ms === "number"
      ? `${data.execution_time_ms.toLocaleString()} ms`
      : "—";

  return (
    <section className="space-y-3" aria-label="Query results">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
          <span>
            <span className="font-semibold text-slate-200">
              {data.row_count.toLocaleString()}
            </span>{" "}
            row{data.row_count === 1 ? "" : "s"}
          </span>
          <span>
            execution time:{" "}
            <span className="font-semibold text-slate-200">{execLabel}</span>
          </span>
          {typeof data.total_rows === "number" && (
            <span>
              total rows:{" "}
              <span className="font-semibold text-slate-200">
                {data.total_rows.toLocaleString()}
              </span>
            </span>
          )}
        </div>
        {rows.length > 0 && <ExportMenu data={data} />}
      </div>

      {rows.length === 0 || columns.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-700 bg-slate-900/40 p-6 text-center text-sm text-slate-400">
          No rows to display.
        </div>
      ) : (
        <div className="scrollbar-thin max-h-[clamp(16rem,55vh,32rem)] overflow-auto rounded-lg border border-slate-800">
          <table className="min-w-full border-collapse text-left text-sm">
            <thead className="sticky top-0 z-10 bg-slate-800/95 backdrop-blur">
              <tr>
                {columns.map((col) => {
                  const dir = sort && sort.col === col ? sort.dir : null;
                  return (
                    <th
                      key={col}
                      scope="col"
                      aria-sort={
                        dir === "asc"
                          ? "ascending"
                          : dir === "desc"
                            ? "descending"
                            : "none"
                      }
                      className="whitespace-nowrap border-b border-slate-700 px-3 py-2 font-semibold text-slate-200"
                    >
                      <button
                        type="button"
                        onClick={() => toggleSort(col)}
                        className="inline-flex items-center gap-1 rounded transition-colors hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                        aria-label={`Sort by ${col}`}
                      >
                        {col}
                        <span
                          className={`text-[10px] ${dir ? "text-indigo-400" : "text-slate-600"}`}
                          aria-hidden="true"
                        >
                          {dir === "asc" ? "▲" : dir === "desc" ? "▼" : "⇅"}
                        </span>
                      </button>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row, rowIndex) => (
                <tr
                  key={rowIndex}
                  className="odd:bg-slate-900/40 even:bg-slate-900/10 hover:bg-slate-800/60"
                >
                  {columns.map((col) => (
                    <td
                      key={col}
                      className="max-w-xs truncate border-b border-slate-800/70 px-3 py-1.5 align-top text-slate-300"
                      title={formatCell(row[col])}
                    >
                      {formatCell(row[col])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
