import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "DBWhisper — Ask your database anything",
  description:
    "Connect Postgres or MySQL and query in plain English — DBWhisper writes safe, read-only SQL, runs it, and shows the answer. No SQL required.",
};

export default function Landing() {
  return (
    <main className="mx-auto flex min-h-[72vh] max-w-4xl flex-col items-center justify-center gap-8 px-4 py-16 text-center sm:px-6">
      <div className="space-y-4 motion-safe:animate-fade-up">
        <p className="text-lg font-bold tracking-tight text-white">
          DB<span className="text-brand-fg">Whisper</span>
        </p>
        <h1 className="bg-gradient-to-r from-white via-brand-fg to-brand bg-clip-text text-4xl font-bold tracking-tight text-transparent sm:text-5xl">
          Ask your database anything.
        </h1>
        <p className="mx-auto max-w-xl text-base leading-relaxed text-slate-400">
          Connect Postgres or MySQL and query in plain English. DBWhisper writes{" "}
          <span className="text-slate-200">safe, read-only SQL</span>, runs it, and shows the
          answer with a chart — no SQL required.
        </p>
      </div>

      <div
        className="flex flex-wrap items-center justify-center gap-3 motion-safe:animate-fade-up"
        style={{ animationDelay: "75ms" }}
      >
        <Link
          href="/app"
          className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
        >
          Open the console →
        </Link>
        <a
          href="https://github.com/mubin-attar-007/dbwhisper"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-5 py-2.5 text-sm font-semibold text-slate-200 transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
        >
          View on GitHub
        </a>
      </div>

      <p
        className="text-xs text-slate-500 motion-safe:animate-fade-up"
        style={{ animationDelay: "150ms" }}
      >
        Read-only by default · LangGraph agent · pgvector RAG · multi-LLM fallback
      </p>
    </main>
  );
}
