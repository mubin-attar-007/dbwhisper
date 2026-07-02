"use client";

import Link from "next/link";
import { HealthBadge } from "./HealthBadge";
import { Icon } from "./Icon";
import { useWorkspace } from "./WorkspaceProvider";

function DbSwitcher() {
  const { databases, dbFlag, setDbFlag } = useWorkspace();
  const selected = databases.find((d) => d.db_flag === dbFlag);
  const isPublic = selected?.is_public ?? dbFlag.trim() === "demo";

  return (
    <div className="flex items-center gap-2">
      <Icon name="database" className="hidden h-4 w-4 text-slate-500 sm:block" />
      {databases.length > 0 ? (
        <div className="relative">
          <select
            value={dbFlag}
            onChange={(e) => setDbFlag(e.target.value)}
            aria-label="Database"
            className="appearance-none rounded-md border border-slate-700 bg-slate-900/70 py-1.5 pl-3 pr-8 text-sm text-slate-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            {databases.map((d) => (
              <option key={d.db_flag} value={d.db_flag}>
                {d.db_flag}
                {d.is_public ? " (sample)" : ""}
              </option>
            ))}
          </select>
          <Icon
            name="chevronDown"
            className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500"
          />
        </div>
      ) : (
        <input
          value={dbFlag}
          onChange={(e) => setDbFlag(e.target.value)}
          placeholder="db_flag"
          aria-label="Database"
          className="w-32 rounded-md border border-slate-700 bg-slate-900/70 px-2 py-1.5 font-mono text-sm text-slate-100 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
      )}
      {isPublic && (
        <span
          className="hidden rounded-full border border-amber-600/50 bg-amber-950/40 px-2 py-0.5 text-[11px] font-medium text-amber-300 sm:inline"
          title="Built-in sample data. Enroll your own database to query it."
        >
          sample
        </span>
      )}
    </div>
  );
}

export function AppTopBar() {
  const { newQuery } = useWorkspace();
  return (
    <header className="z-10 flex h-14 shrink-0 items-center justify-between gap-3 border-b border-slate-800/80 bg-slate-950/80 px-3 backdrop-blur sm:px-4">
      <Link
        href="/"
        className="rounded text-base font-bold tracking-tight text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
      >
        DB<span className="text-brand-fg">Whisper</span>
      </Link>
      <div className="flex items-center gap-2 sm:gap-3">
        <DbSwitcher />
        <button
          type="button"
          onClick={newQuery}
          className="inline-flex items-center rounded-md border border-slate-700 px-2.5 py-1.5 text-sm font-medium text-slate-200 transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 md:hidden"
          aria-label="New query"
        >
          <Icon name="plus" className="h-4 w-4" />
        </button>
        <HealthBadge />
      </div>
    </header>
  );
}
