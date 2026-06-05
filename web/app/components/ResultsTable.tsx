"use client";

import type { QueryResultData } from "@/src/lib/api";
import { deriveColumns, formatCell, resolveRows } from "@/src/lib/rows";

export function ResultsTable({ data }: { data: QueryResultData }) {
  const rows = resolveRows(data);
  const columns = deriveColumns(rows);

  const execLabel =
    typeof data.execution_time_ms === "number"
      ? `${data.execution_time_ms.toLocaleString()} ms`
      : "—";

  return (
    <section className="space-y-3" aria-label="Query results">
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

      {rows.length === 0 || columns.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-700 bg-slate-900/40 p-6 text-center text-sm text-slate-400">
          No rows to display.
        </div>
      ) : (
        <div className="scrollbar-thin max-h-[28rem] overflow-auto rounded-lg border border-slate-800">
          <table className="min-w-full border-collapse text-left text-sm">
            <thead className="sticky top-0 z-10 bg-slate-800/95 backdrop-blur">
              <tr>
                {columns.map((col) => (
                  <th
                    key={col}
                    scope="col"
                    className="whitespace-nowrap border-b border-slate-700 px-3 py-2 font-semibold text-slate-200"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
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
