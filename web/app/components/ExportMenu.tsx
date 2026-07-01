"use client";

import { useState } from "react";
import type { QueryResultData } from "@/src/lib/api";
import { exportCsv, exportJson, toMarkdown } from "@/src/lib/export";

const BTN =
  "inline-flex items-center gap-1.5 rounded-md border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950";

export function ExportMenu({ data }: { data: QueryResultData }) {
  const [copied, setCopied] = useState(false);

  async function copyMarkdown() {
    try {
      await navigator.clipboard.writeText(toMarkdown(data));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (non-secure context) */
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[11px] uppercase tracking-wide text-slate-500">Export</span>
      <button type="button" onClick={() => exportCsv(data)} className={BTN}>
        CSV
      </button>
      <button type="button" onClick={() => exportJson(data)} className={BTN}>
        JSON
      </button>
      <button type="button" onClick={copyMarkdown} className={BTN} aria-live="polite">
        {copied ? "Copied!" : "Copy Markdown"}
      </button>
    </div>
  );
}
