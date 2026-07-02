"use client";

import { useCallback, useEffect, useState } from "react";
import { deleteVerifiedPair, listVerifiedPairs, type VerifiedPair } from "@/src/lib/api";
import { Icon } from "../../../components/Icon";
import { useWorkspace } from "../../../components/WorkspaceProvider";

export default function TrainingPage() {
  const { dbFlag } = useWorkspace();
  const [pairs, setPairs] = useState<VerifiedPair[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listVerifiedPairs(dbFlag || undefined)
      .then(setPairs)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load."))
      .finally(() => setLoading(false));
  }, [dbFlag]);

  useEffect(() => {
    load();
  }, [load]);

  async function remove(id: number) {
    const prev = pairs;
    setPairs((p) => p.filter((x) => x.id !== id));
    try {
      await deleteVerifiedPair(id);
    } catch {
      setPairs(prev);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <div className="flex items-center gap-3">
        <Icon name="star" className="h-5 w-5 text-indigo-400" />
        <h1 className="text-xl font-semibold tracking-tight text-slate-100">Verified queries</h1>
      </div>
      <p className="mt-1 text-sm text-slate-400">
        Human-approved question → SQL examples the agent reuses to answer more accurately
        {dbFlag ? (
          <>
            {" "}
            for <span className="font-mono text-slate-300">{dbFlag}</span>
          </>
        ) : null}
        . Save one from any answer with “👍 Save as verified”.
      </p>

      <div className="mt-6 space-y-3">
        {loading && <p className="text-sm text-slate-500">Loading…</p>}
        {error && <p className="text-sm text-rose-300">{error}</p>}
        {!loading && !error && pairs.length === 0 && (
          <div className="rounded-lg border border-dashed border-slate-700 bg-slate-900/40 p-6 text-center text-sm text-slate-400">
            No verified queries yet. Run a question, then click{" "}
            <span className="text-slate-200">👍 Save as verified</span> to teach DBWhisper.
          </div>
        )}
        {pairs.map((p) => (
          <div key={p.id} className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-medium text-slate-100">{p.question}</p>
              <button
                type="button"
                onClick={() => remove(p.id)}
                title="Delete this verified query"
                aria-label="Delete"
                className="shrink-0 rounded-md p-1.5 text-slate-500 transition hover:bg-rose-950/40 hover:text-rose-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-400"
              >
                <Icon name="trash" className="h-4 w-4" />
              </button>
            </div>
            <pre className="scrollbar-thin mt-2 overflow-x-auto rounded-md border border-slate-800 bg-slate-950/60 p-3 font-mono text-xs leading-relaxed text-slate-300">
              <code>{p.sql}</code>
            </pre>
            <div className="mt-2 flex items-center gap-2 text-[11px] text-slate-500">
              <span className="rounded-full border border-slate-700 bg-slate-800/60 px-2 py-0.5 font-mono">
                {p.db_flag}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
