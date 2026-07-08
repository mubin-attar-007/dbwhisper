"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon } from "./Icon";
import { EXTERNAL, NAV } from "./nav";
import { useWorkspace } from "./WorkspaceProvider";

function timeAgo(ts: number): string {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return "now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

/**
 * SidebarContent — the shared inner nav (New query · nav · history · external
 * links). Rendered by the desktop sidebar AND the mobile drawer so the two never
 * drift. `onNavigate` lets the drawer close itself when a link is tapped.
 */
export function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { history, selectHistory, clearHistory, newQuery } = useWorkspace();
  const pathname = usePathname();
  const done = () => onNavigate?.();

  return (
    <>
      <div className="p-3">
        <Link
          href="/app"
          onClick={() => {
            newQuery();
            done();
          }}
          className="flex w-full items-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
        >
          <Icon name="plus" className="h-4 w-4" /> New query
        </Link>
      </div>

      <nav className="space-y-0.5 px-2">
        {NAV.map((item) => {
          const active = item.href ? pathname === item.href : false;
          const cls = `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
            active
              ? "bg-indigo-950/40 text-indigo-200"
              : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-100"
          }`;
          const inner = (
            <>
              <Icon name={item.icon} className="h-4 w-4 shrink-0" />
              <span className="flex-1 truncate">{item.label}</span>
              {item.soon && (
                <span className="rounded-full border border-slate-700 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">
                  soon
                </span>
              )}
            </>
          );
          return item.href ? (
            <Link key={item.label} href={item.href} onClick={done} className={cls}>
              {inner}
            </Link>
          ) : (
            <div key={item.label} className={`${cls} cursor-default`}>
              {inner}
            </div>
          );
        })}
      </nav>

      <div className="mt-4 flex min-h-0 flex-1 flex-col px-2">
        <div className="flex items-center justify-between px-1 pb-1">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">History</span>
          {history.length > 0 && (
            <button
              type="button"
              onClick={clearHistory}
              className="rounded text-xs text-slate-400 transition hover:text-slate-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-400"
            >
              Clear
            </button>
          )}
        </div>
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
          {history.length === 0 ? (
            <p className="px-1 py-2 text-xs text-slate-400">Your questions will appear here.</p>
          ) : (
            <ul className="space-y-0.5">
              {history.map((h, i) => (
                <li key={`${h.ts}-${i}`}>
                  <Link
                    href="/app"
                    onClick={() => {
                      selectHistory(h);
                      done();
                    }}
                    className="group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-slate-400 transition hover:bg-slate-800/60 hover:text-slate-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-indigo-400"
                  >
                    <Icon
                      name="history"
                      className="h-3.5 w-3.5 shrink-0 text-slate-600 group-hover:text-slate-400"
                    />
                    <span className="flex-1 truncate">{h.query}</span>
                    <span className="shrink-0 text-[10px] text-slate-400">{timeAgo(h.ts)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="border-t border-slate-800/80 p-2">
        {EXTERNAL.map((item) => (
          <a
            key={item.label}
            href={item.href}
            target="_blank"
            rel="noopener noreferrer"
            onClick={done}
            className="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-slate-400 transition hover:bg-slate-800/60 hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
          >
            <Icon name={item.icon} className="h-4 w-4 shrink-0" /> {item.label}
          </a>
        ))}
      </div>
    </>
  );
}
