"use client";

import { useCallback, useEffect, useState } from "react";
import { getSchema, type SchemaTable } from "@/src/lib/api";
import { Icon } from "../../../components/Icon";
import { useWorkspace } from "../../../components/WorkspaceProvider";

export default function SchemaPage() {
  const { dbFlag } = useWorkspace();
  const [tables, setTables] = useState<SchemaTable[]>([]);
  const [dbName, setDbName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!dbFlag) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    getSchema(dbFlag)
      .then((s) => {
        setTables(s.tables);
        setDbName(s.database_name);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load schema."))
      .finally(() => setLoading(false));
  }, [dbFlag]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <div className="flex items-center gap-3">
        <Icon name="table" className="h-5 w-5 text-indigo-400" />
        <h1 className="text-xl font-semibold tracking-tight text-slate-100">Schema</h1>
      </div>
      <p className="mt-1 text-sm text-slate-400">
        Tables DBWhisper knows about in{" "}
        <span className="font-mono text-slate-300">{dbFlag || "—"}</span>
        {dbName ? <> · {dbName}</> : null}. Primary keys are highlighted.
      </p>

      <div className="mt-6 space-y-3">
        {loading && <p className="text-sm text-slate-400">Loading…</p>}
        {error && <p className="text-sm text-rose-300">{error}</p>}
        {!loading && !error && tables.length === 0 && (
          <div className="rounded-lg border border-dashed border-slate-700 bg-slate-900/40 p-6 text-center text-sm text-slate-400">
            No schema indexed for this database yet.
          </div>
        )}
        {tables.map((t) => (
          <div
            key={`${t.schema_name}.${t.table}`}
            className="rounded-lg border border-slate-800 bg-slate-900/40 p-4"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Icon name="table" className="h-4 w-4 text-slate-500" />
              <span className="font-mono text-sm font-semibold text-slate-100">
                {t.schema_name ? `${t.schema_name}.` : ""}
                {t.table}
              </span>
              {t.has_foreign_keys && (
                <span className="rounded-full border border-slate-700 bg-slate-800/60 px-2 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">
                  has FKs
                </span>
              )}
              <span className="text-[11px] text-slate-400">{t.columns.length} cols</span>
            </div>
            {t.description && <p className="mt-1 text-xs text-slate-400">{t.description}</p>}
            <div className="mt-3 flex flex-wrap gap-1.5">
              {t.columns.map((c) => {
                const isPk = t.primary_key.includes(c);
                return (
                  <span
                    key={c}
                    title={isPk ? "primary key" : undefined}
                    className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono text-[11px] ${
                      isPk
                        ? "border-indigo-700/50 bg-indigo-950/30 text-indigo-200"
                        : "border-slate-700 bg-slate-800/50 text-slate-300"
                    }`}
                  >
                    {isPk && <span className="text-[9px]">🔑</span>}
                    {c}
                  </span>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
