"use client";

import { useEffect, useRef, useState } from "react";
import type { HistoryEntry } from "@/src/lib/history";

function relativeTime(ts: number): string {
  const seconds = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/**
 * "History" trigger + dropdown listing recent queries. The panel anchors to the
 * nearest positioned ancestor (the question-field wrapper), so it spans the form
 * width up to max-w-md. Closes on Escape and on pointerdown outside.
 */
export function HistoryMenu({
  entries,
  onSelect,
  onClear,
}: {
  entries: HistoryEntry[];
  onSelect: (entry: HistoryEntry) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  if (entries.length === 0) return null;

  return (
    <div ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-haspopup="true"
        aria-expanded={open}
        className="rounded-md border border-slate-700 px-2 py-1 text-xs text-slate-300 transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
      >
        History
      </button>

      {open && (
        <div className="absolute right-0 z-20 mt-1 w-full max-w-md rounded-lg border border-slate-700 bg-slate-900 shadow-xl">
          <ul className="scrollbar-thin max-h-72 overflow-y-auto py-1">
            {entries.map((entry) => (
              <li key={`${entry.ts}-${entry.query}`}>
                <button
                  type="button"
                  onClick={() => {
                    onSelect(entry);
                    setOpen(false);
                  }}
                  className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm text-slate-200 transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
                >
                  <span className="min-w-0 flex-1 truncate">{entry.query}</span>
                  <span className="inline-flex shrink-0 items-center rounded border border-slate-700 bg-slate-800/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                    {entry.dbFlag}
                  </span>
                  <span className="shrink-0 text-[10px] text-slate-400">
                    {relativeTime(entry.ts)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="border-t border-slate-800 p-1">
            <button
              type="button"
              onClick={() => {
                onClear();
                setOpen(false);
              }}
              className="w-full rounded-md px-3 py-1.5 text-left text-xs text-slate-400 transition hover:bg-slate-800 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
            >
              Clear history
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
