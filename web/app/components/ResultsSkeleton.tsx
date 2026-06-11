/**
 * Loading placeholder mirroring the ResultsPanel layout: summary bar, SQL block,
 * and a handful of result rows. Purely presentational.
 */
export function ResultsSkeleton() {
  return (
    <div className="space-y-6" aria-hidden="true">
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
        <div className="h-4 w-2/3 rounded bg-slate-800/70 motion-safe:animate-pulse" />
      </div>

      <div className="space-y-2">
        <div className="h-4 w-28 rounded bg-slate-800/70 motion-safe:animate-pulse" />
        <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-900/80 p-4">
          <div className="h-4 w-3/4 rounded bg-slate-800/70 motion-safe:animate-pulse" />
          <div className="h-4 w-1/2 rounded bg-slate-800/70 motion-safe:animate-pulse" />
        </div>
      </div>

      <div className="space-y-2">
        <div className="h-4 w-full rounded bg-slate-800/70 motion-safe:animate-pulse" />
        <div className="h-4 w-11/12 rounded bg-slate-800/70 motion-safe:animate-pulse" />
        <div className="h-4 w-4/5 rounded bg-slate-800/70 motion-safe:animate-pulse" />
        <div className="h-4 w-9/12 rounded bg-slate-800/70 motion-safe:animate-pulse" />
        <div className="h-4 w-2/3 rounded bg-slate-800/70 motion-safe:animate-pulse" />
      </div>
    </div>
  );
}
