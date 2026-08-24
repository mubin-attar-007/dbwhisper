"use client";

/**
 * The evidence strip: the short, checkable claims the UI makes about a result.
 *
 * The marketing page says the answer arrives with the mechanism that produced it. This is where
 * that promise is kept in the product — schema grounding, the policy decision and its ruleset
 * version, whether the database itself held the session read-only, and whether you are looking at
 * the whole result. Each chip renders exactly what the backend reported, including "not reported",
 * which is deliberately drawn as a muted, dashed chip so it can never be mistaken for a tick.
 *
 * All of the decision-making lives in `src/lib/evidence.ts`; this file is presentation only.
 */

import type { QueryResponse } from "@/src/lib/api";
import { buildEvidence, type EvidenceItem, type EvidenceState } from "@/src/lib/evidence";

const CHIP_STYLES: Record<EvidenceState, string> = {
  ok: "border-emerald-700/60 bg-emerald-950/40 text-emerald-300",
  warn: "border-amber-600/60 bg-amber-950/40 text-amber-200",
  risk: "border-rose-700/60 bg-rose-950/40 text-rose-200",
  info: "border-slate-700 bg-slate-900/70 text-slate-300",
  unknown: "border-dashed border-slate-700 bg-slate-950/60 text-slate-400",
};

/** A glyph rather than a colour, so the state survives a monochrome or colour-blind reading. */
const CHIP_GLYPH: Record<EvidenceState, string> = {
  ok: "✓",
  warn: "!",
  risk: "✕",
  info: "·",
  unknown: "?",
};

function Chip({ item }: { item: EvidenceItem }) {
  return (
    <li
      data-evidence={item.id}
      data-state={item.state}
      title={item.detail}
      className={`inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${CHIP_STYLES[item.state]}`}
    >
      <span aria-hidden="true" className="font-bold leading-none opacity-80">
        {CHIP_GLYPH[item.state]}
      </span>
      <span className="truncate">{item.label}</span>
      {/* The detail is the substance of the claim; keep it available to a screen reader even
          though sighted users get it as a tooltip. */}
      <span className="sr-only"> — {item.detail}</span>
    </li>
  );
}

export function EvidenceStrip({ response }: { response: QueryResponse }) {
  const items = buildEvidence(response);
  if (items.length === 0) return null;

  return (
    <section aria-labelledby="evidence-heading" className="space-y-2">
      <h2
        id="evidence-heading"
        className="text-xs font-semibold uppercase tracking-wide text-slate-400"
      >
        Evidence
      </h2>
      <ul className="flex flex-wrap gap-2">
        {items.map((item) => (
          <Chip key={item.id} item={item} />
        ))}
      </ul>
    </section>
  );
}
