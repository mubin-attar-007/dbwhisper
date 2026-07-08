"use client";

import { useMemo, useState } from "react";
import type { QueryResultData } from "@/src/lib/api";
import { pickChart } from "@/src/lib/chart";
import { deriveColumns, resolveRows } from "@/src/lib/rows";

function fmtNum(n: number): string {
  if (!Number.isFinite(n)) return "";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n * 100) / 100}`;
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export function ResultChart({ data }: { data: QueryResultData }) {
  const spec = useMemo(() => {
    const rows = resolveRows(data);
    return pickChart(rows, deriveColumns(rows));
  }, [data]);

  const [override, setOverride] = useState<"bar" | "line" | null>(null);
  const [hover, setHover] = useState<number | null>(null);

  if (!spec) return null;

  if (spec.kind === "kpi") {
    return (
      <section
        aria-label="Result highlight"
        className="rounded-lg border border-slate-800 bg-slate-900/40 p-6"
      >
        <p className="text-xs uppercase tracking-wide text-slate-400">{spec.label}</p>
        <p className="mt-1 text-4xl font-bold text-white">{spec.value.toLocaleString()}</p>
      </section>
    );
  }

  const kind = override ?? spec.kind;
  const points = spec.points.slice(0, 24);
  const max = Math.max(...points.map((p) => p.value), 0);
  const min = Math.min(...points.map((p) => p.value), 0);

  const W = 720;
  const H = 280;
  const padL = 48;
  const padR = 16;
  const padT = 16;
  const padB = 46;
  const iw = W - padL - padR;
  const ih = H - padT - padB;
  const range = max - min || 1;
  const base = min < 0 ? 0 : min;
  const yOf = (v: number) => padT + ih - ((v - min) / range) * ih;
  const xOf = (i: number) =>
    padL + (points.length === 1 ? iw / 2 : (i / (points.length - 1)) * iw);
  const bw = iw / points.length;

  // Faint gridlines (top / mid / bottom) for readability.
  const ticks = [max, (max + min) / 2, min].filter((v, i, a) => a.indexOf(v) === i);

  // Hover tooltip geometry.
  const hp = hover != null ? points[hover] : null;
  const centerX = (i: number) => (kind === "bar" ? padL + i * bw + bw / 2 : xOf(i));
  const topY = (v: number) => (kind === "bar" ? Math.min(yOf(v), yOf(base)) : yOf(v));
  const tipText = hp ? truncate(hp.label, 18) : "";
  const tipVal = hp ? hp.value.toLocaleString() : "";
  const tipW = Math.max(tipText.length, tipVal.length + 2) * 6.1 + 20;
  const tipH = 34;
  const tipCX =
    hover != null
      ? Math.max(padL + tipW / 2, Math.min(W - padR - tipW / 2, centerX(hover)))
      : 0;
  const anchorY = hp ? topY(hp.value) : 0;
  const tipY = anchorY - tipH - 10 < padT ? anchorY + 12 : anchorY - tipH - 10;

  return (
    <section
      aria-label={`${spec.y} by ${spec.x}`}
      className="space-y-2 rounded-lg border border-slate-800 bg-slate-900/40 p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          {spec.y} by {spec.x}
        </h3>
        <div className="flex gap-1 rounded-md border border-slate-700 p-0.5">
          {(["bar", "line"] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setOverride(k)}
              className={`rounded px-2 py-0.5 text-xs capitalize transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 ${
                kind === k ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {k}
            </button>
          ))}
        </div>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={`${kind} chart of ${spec.y} by ${spec.x}`}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id="rc-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#6366f1" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#6366f1" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Gridlines + y labels */}
        {ticks.map((v, gi) => (
          <g key={`g-${gi}`}>
            <line
              x1={padL}
              y1={yOf(v)}
              x2={W - padR}
              y2={yOf(v)}
              stroke={v === base ? "#334155" : "#1e293b"}
              strokeWidth="1"
              strokeDasharray={v === base ? undefined : "3 4"}
            />
            <text x={padL - 6} y={yOf(v)} textAnchor="end" dominantBaseline="middle" fill="#64748b" fontSize="10">
              {fmtNum(v)}
            </text>
          </g>
        ))}

        {/* Crosshair on the hovered column (line mode) */}
        {hover != null && kind === "line" && (
          <line
            x1={xOf(hover)}
            y1={padT}
            x2={xOf(hover)}
            y2={yOf(base)}
            stroke="#6366f1"
            strokeWidth="1"
            strokeDasharray="3 3"
            opacity="0.5"
          />
        )}

        {kind === "bar"
          ? points.map((p, i) => {
              const yTop = Math.min(yOf(p.value), yOf(base));
              const h = Math.max(Math.abs(yOf(p.value) - yOf(base)), 1);
              const x = padL + i * bw + bw * 0.15;
              const w = bw * 0.7;
              return (
                <g key={i}>
                  <rect
                    x={x}
                    y={yTop}
                    width={w}
                    height={h}
                    rx="2"
                    fill={hover === i ? "#818cf8" : "#6366f1"}
                  />
                  {points.length <= 12 && hover == null && (
                    <text x={x + w / 2} y={yTop - 3} textAnchor="middle" fill="#94a3b8" fontSize="9">
                      {fmtNum(p.value)}
                    </text>
                  )}
                  <text
                    x={padL + i * bw + bw / 2}
                    y={H - padB + 14}
                    textAnchor="middle"
                    fill="#64748b"
                    fontSize="9"
                  >
                    {truncate(p.label, 8)}
                  </text>
                </g>
              );
            })
          : (
              <>
                <polygon
                  fill="url(#rc-area)"
                  points={`${xOf(0)},${yOf(base)} ${points
                    .map((p, i) => `${xOf(i)},${yOf(p.value)}`)
                    .join(" ")} ${xOf(points.length - 1)},${yOf(base)}`}
                />
                <polyline
                  fill="none"
                  stroke="#6366f1"
                  strokeWidth="2"
                  points={points.map((p, i) => `${xOf(i)},${yOf(p.value)}`).join(" ")}
                />
                {points.map((p, i) => (
                  <g key={i}>
                    <circle
                      cx={xOf(i)}
                      cy={yOf(p.value)}
                      r={hover === i ? 4 : 2.5}
                      fill={hover === i ? "#c7d2fe" : "#818cf8"}
                    />
                    {(i === 0 || i === points.length - 1 || points.length <= 8) && (
                      <text
                        x={xOf(i)}
                        y={H - padB + 14}
                        textAnchor="middle"
                        fill="#64748b"
                        fontSize="9"
                      >
                        {truncate(p.label, 8)}
                      </text>
                    )}
                  </g>
                ))}
              </>
            )}

        {/* Transparent per-point hover targets */}
        {points.map((_, i) => (
          <rect
            key={`hit-${i}`}
            x={padL + i * bw}
            y={padT}
            width={bw}
            height={ih}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}

        {/* Tooltip */}
        {hp && (
          <g transform={`translate(${tipCX}, ${tipY})`} pointerEvents="none">
            <rect
              x={-tipW / 2}
              y={0}
              width={tipW}
              height={tipH}
              rx="5"
              fill="#0b1220"
              stroke="#334155"
            />
            <text x={0} y={13} textAnchor="middle" fill="#cbd5e1" fontSize="10">
              {tipText}
            </text>
            <text x={0} y={27} textAnchor="middle" fill="#ffffff" fontSize="11" fontWeight="600">
              {tipVal}
            </text>
          </g>
        )}
      </svg>
    </section>
  );
}
