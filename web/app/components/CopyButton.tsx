"use client";

import { useState } from "react";

export function CopyButton({
  value,
  label = "Copy",
}: {
  value: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        // Fallback for non-secure contexts.
        const ta = document.createElement("textarea");
        ta.value = value;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`inline-flex items-center gap-1.5 rounded-md border bg-slate-800 px-2.5 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950 ${
        copied
          ? "border-emerald-700/60 text-emerald-300"
          : "border-slate-700 text-slate-200 hover:bg-slate-700"
      }`}
      aria-label={copied ? "Copied" : label}
    >
      <svg
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="h-3.5 w-3.5"
        aria-hidden="true"
      >
        {copied ? (
          <path d="M3 8.5 6.5 12 13 4.5" />
        ) : (
          <>
            <rect x="5.5" y="5.5" width="8" height="9" rx="1.5" />
            <path d="M10.5 5.5V4A1.5 1.5 0 0 0 9 2.5H4A1.5 1.5 0 0 0 2.5 4v5A1.5 1.5 0 0 0 4 10.5h1.5" />
          </>
        )}
      </svg>
      {/* Stack both labels in one grid cell so the button width never shifts. */}
      <span aria-live="polite" className="inline-grid text-left">
        <span className={`col-start-1 row-start-1 ${copied ? "invisible" : ""}`}>
          {label}
        </span>
        <span className={`col-start-1 row-start-1 ${copied ? "" : "invisible"}`}>
          Copied!
        </span>
      </span>
    </button>
  );
}
