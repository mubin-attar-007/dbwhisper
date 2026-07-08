import type { Metadata } from "next";
import Link from "next/link";
import { Icon, type IconName } from "../components/Icon";

export const metadata: Metadata = {
  title: "DBWhisper — Ask your database anything",
  description:
    "Connect Postgres or MySQL and query in plain English. DBWhisper writes safe, read-only SQL, runs it, and shows the answer with a chart. No SQL required.",
};

const STEPS = [
  {
    n: "1",
    title: "Connect your database",
    body: "Point DBWhisper at Postgres or MySQL with a read-only role. It introspects the schema and learns your tables.",
  },
  {
    n: "2",
    title: "Ask in plain English",
    body: "“Top 5 products by revenue this quarter.” No SQL, no schema-hunting — just the question.",
  },
  {
    n: "3",
    title: "Get SQL + results you trust",
    body: "It writes validated read-only SQL, shows it, runs it, and returns a table, a chart, and a plain-English summary.",
  },
];

const FEATURES: { icon: IconName; title: string; body: string }[] = [
  {
    icon: "table",
    title: "SQL you can read & edit",
    body: "The generated SQL is always shown — and you can edit and re-run it, still through the read-only validator.",
  },
  {
    icon: "spark",
    title: "Auto-charts & summaries",
    body: "Results come back as a sortable table, an auto-selected chart, and a one-line plain-English summary.",
  },
  {
    icon: "history",
    title: "Conversational follow-ups",
    body: "It suggests follow-up questions and keeps context across a session so you can drill in.",
  },
  {
    icon: "database",
    title: "Multi-engine & exportable",
    body: "Postgres · MySQL · SQL Server. Export any result to CSV, JSON, or Markdown in one click.",
  },
];

const TRUST: { icon: IconName; title: string; body: string }[] = [
  {
    icon: "database",
    title: "Read-only by construction",
    body: "Every query — generated or hand-edited — passes a read-only validator. Writes, drops, and DDL are always rejected.",
  },
  {
    icon: "table",
    title: "SQL shown before it runs",
    body: "You always see the exact SQL before results. Nothing touches your data that you can't inspect first.",
  },
  {
    icon: "star",
    title: "Least-privilege connections",
    body: "Connect with a read-only database role; DBWhisper refuses to enroll a writable connection.",
  },
  {
    icon: "spark",
    title: "No black-box code execution",
    body: "The model outputs SQL and structured JSON — never arbitrary code that gets executed on your server.",
  },
];

const PROOF: { value: string; label: string; note: string }[] = [
  {
    value: "82%",
    label: "Execution accuracy",
    note: "18 of 22 golden business questions returned the exact correct result set.",
  },
  {
    value: "100%",
    label: "Fail-closed on unsafe input",
    note: "All 4 unsafe or out-of-scope prompts were refused — never a hallucinated query.",
  },
  {
    value: "0",
    label: "Writes to your data",
    note: "Every query — generated or hand-edited — passes a read-only validator. Writes & DDL are always rejected.",
  },
];

const PIPELINE: { icon: IconName; title: string; tag?: string; body: string }[] = [
  {
    icon: "spark",
    title: "You ask in plain English",
    body: "“Top 5 products by revenue this quarter.” No SQL, no schema-hunting — just the question.",
  },
  {
    icon: "table",
    title: "Relevant tables are retrieved",
    tag: "pgvector",
    body: "Embedding similarity pulls only the tables your question needs, so the model sees a focused schema — not a 200-table dump. It scales to large databases and cuts invented columns.",
  },
  {
    icon: "code",
    title: "SQL is generated",
    tag: "LangGraph agent",
    body: "A LangGraph agent writes the query, with automatic fallback across six LLM providers (OpenAI → OpenRouter → DeepSeek → Groq → Anthropic → Gemini).",
  },
  {
    icon: "shield",
    title: "It’s proven read-only",
    tag: "fail-closed validator",
    body: "Before anything runs, a deterministic validator inspects the SQL. Writes, DDL, and multi-statements are rejected — this is code between generation and execution, not a prompt the model can ignore.",
  },
  {
    icon: "database",
    title: "Run & explained",
    body: "The query runs on your read-only connection and returns a sortable table, an auto-selected chart, and a one-line plain-English summary.",
  },
];

const DECISIONS: { title: string; body: string }[] = [
  {
    title: "Schema-grounded, not schema-dumped",
    body: "Instead of pasting your whole schema into every prompt, DBWhisper retrieves only the relevant tables via pgvector embeddings. Smaller prompts, fewer hallucinated columns, and it works on databases with hundreds of tables.",
  },
  {
    title: "Fail-closed, not prompt-please",
    body: "“Read-only” isn’t an instruction the model might ignore — it’s a deterministic validator sitting between generation and execution. If a query can’t be proven read-only, it never runs.",
  },
  {
    title: "Six providers, always answers",
    body: "If one provider is down or rate-limited, the agent falls through OpenRouter, DeepSeek, Groq, and Anthropic. Gemini’s free tier is always wired as the final fallback, so the live demo never goes dark.",
  },
];

function SectionTitle({ eyebrow, title }: { eyebrow: string; title: string }) {
  return (
    <div className="mx-auto max-w-2xl text-center">
      <p className="text-xs font-semibold uppercase tracking-widest text-indigo-400">{eyebrow}</p>
      <h2 className="mt-2 text-2xl font-bold tracking-tight text-white sm:text-3xl">{title}</h2>
    </div>
  );
}

function ProductMock() {
  const rows: [string, string][] = [
    ["Aeron Chair", "$128,400"],
    ["Standing Desk", "$96,220"],
    ["Monitor Arm", "$54,900"],
    ["Desk Lamp", "$31,050"],
    ["Cable Kit", "$18,700"],
  ];
  return (
    <div className="overflow-hidden rounded-xl border border-slate-700/70 bg-slate-900/80 text-left shadow-2xl">
      <div className="flex items-center gap-2 border-b border-slate-800 bg-slate-950/60 px-4 py-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-rose-500/70" />
        <span className="h-2.5 w-2.5 rounded-full bg-amber-400/70" />
        <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/70" />
        <span className="ml-3 truncate rounded bg-slate-800/70 px-2 py-0.5 font-mono text-[11px] text-slate-400">
          dbwhisper.app/app
        </span>
      </div>
      <div className="space-y-3 p-4">
        <div className="rounded-lg border border-slate-700 bg-slate-950/50 px-3 py-2 text-sm text-slate-300">
          Top 5 products by total revenue
        </div>
        <div className="rounded-lg border border-indigo-700/40 bg-indigo-950/30 p-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-indigo-300">Summary</p>
          <p className="mt-1 text-xs leading-relaxed text-indigo-100">
            Aeron Chair leads with $128,400 in revenue, followed by the Standing Desk at $96,220.
          </p>
        </div>
        <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-3">
          <span className="rounded-full border border-emerald-700/60 bg-emerald-900/30 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
            validation passed
          </span>
          <pre className="mt-2 overflow-x-auto font-mono text-[11px] leading-relaxed text-slate-300">
            <code>{`SELECT p.name, SUM(oi.qty * oi.price) AS revenue
FROM order_items oi
JOIN products p ON p.id = oi.product_id
GROUP BY p.name ORDER BY revenue DESC LIMIT 5;`}</code>
          </pre>
        </div>
        <div className="overflow-hidden rounded-lg border border-slate-800">
          <table className="w-full text-left text-[11px]">
            <thead className="bg-slate-800/70 text-slate-300">
              <tr>
                <th className="px-3 py-1.5 font-semibold">product</th>
                <th className="px-3 py-1.5 text-right font-semibold">revenue</th>
              </tr>
            </thead>
            <tbody className="text-slate-400">
              {rows.map(([a, b]) => (
                <tr key={a} className="border-t border-slate-800/70">
                  <td className="px-3 py-1.5">{a}</td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums">{b}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

const PRIMARY_CTA =
  "inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950";
const GHOST_CTA =
  "inline-flex items-center gap-2 rounded-lg border border-slate-700 px-5 py-2.5 text-sm font-semibold text-slate-200 transition hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950";

export default function Landing() {
  return (
    <div>
      {/* Nav */}
      <header className="sticky top-0 z-20 border-b border-slate-800/60 bg-slate-950/70 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
          <span className="text-base font-bold tracking-tight text-white">
            DB<span className="text-brand-fg">Whisper</span>
          </span>
          <nav className="hidden items-center gap-6 text-sm text-slate-400 sm:flex">
            <a href="#how" className="transition hover:text-slate-200">How it works</a>
            <a href="#features" className="transition hover:text-slate-200">Features</a>
            <a href="#architecture" className="transition hover:text-slate-200">Architecture</a>
            <a href="#security" className="transition hover:text-slate-200">Security</a>
          </nav>
          <Link
            href="/app"
            className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-semibold text-white transition hover:bg-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
          >
            Open the console
          </Link>
        </div>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
          <div className="absolute left-1/2 top-[-12%] h-[420px] w-[720px] max-w-[95vw] -translate-x-1/2 rounded-full bg-indigo-600/20 blur-[120px]" />
          <div
            className="absolute inset-0"
            style={{
              backgroundImage:
                "radial-gradient(rgb(148 163 184 / 0.06) 1px, transparent 1px)",
              backgroundSize: "18px 18px",
              maskImage: "radial-gradient(ellipse 70% 55% at 50% 25%, black, transparent)",
              WebkitMaskImage: "radial-gradient(ellipse 70% 55% at 50% 25%, black, transparent)",
            }}
          />
        </div>
        <div className="mx-auto max-w-6xl px-4 pb-16 pt-20 text-center sm:px-6 sm:pt-28">
          <div className="motion-safe:animate-fade-up">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-900/60 px-3 py-1 text-xs text-slate-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> Read-only by default · open source
            </span>
            <h1 className="mx-auto mt-5 max-w-3xl bg-gradient-to-br from-white via-brand-fg to-brand bg-clip-text text-4xl font-bold leading-[1.05] tracking-tight text-transparent sm:text-6xl">
              Ask your database anything.
            </h1>
            <p className="mx-auto mt-5 max-w-xl text-base leading-relaxed text-slate-400 sm:text-lg">
              Connect Postgres or MySQL and query in plain English. DBWhisper writes safe,
              read-only SQL, runs it, and shows the answer with a chart — no SQL required.
            </p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
              <Link href="/app" className={PRIMARY_CTA}>
                Open the console →
              </Link>
              <a href="#how" className={GHOST_CTA}>
                See how it works
              </a>
            </div>
          </div>
          <div
            className="mx-auto mt-14 max-w-3xl motion-safe:animate-fade-up"
            style={{ animationDelay: "120ms" }}
          >
            <ProductMock />
          </div>
        </div>
      </section>

      {/* Proof strip — measured, not claimed */}
      <section aria-labelledby="proof-heading" className="border-y border-slate-800/60 bg-slate-950/40">
        <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
          <div className="mx-auto max-w-2xl text-center">
            <p className="text-xs font-semibold uppercase tracking-widest text-indigo-400">
              Measured, not claimed
            </p>
            <h2
              id="proof-heading"
              className="mt-2 text-2xl font-bold tracking-tight text-white sm:text-3xl"
            >
              Evaluated on a real golden set
            </h2>
          </div>
          <dl className="mt-10 grid gap-4 sm:grid-cols-3">
            {PROOF.map((p) => (
              <div
                key={p.label}
                className="rounded-xl border border-slate-800 bg-slate-900/40 p-6 text-center"
              >
                <dt className="sr-only">{p.label}</dt>
                <dd>
                  <span className="block bg-gradient-to-br from-white to-brand-fg bg-clip-text text-4xl font-bold tracking-tight text-transparent">
                    {p.value}
                  </span>
                  <span className="mt-2 block text-sm font-semibold text-slate-200">{p.label}</span>
                  <span className="mt-2 block text-xs leading-relaxed text-slate-400">{p.note}</span>
                </dd>
              </div>
            ))}
          </dl>
          <p className="mx-auto mt-6 max-w-2xl text-center text-xs leading-relaxed text-slate-400">
            Golden-set eval: 22 answerable + 4 unsafe / out-of-scope business questions, run end-to-end
            against a Postgres store with a read-only role. Execution accuracy = exact result-set match
            (order-insensitive), Spider-style.
          </p>
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
        <SectionTitle eyebrow="How it works" title="From question to answer in three steps" />
        <div className="mt-10 grid gap-5 sm:grid-cols-3">
          {STEPS.map((s) => (
            <div key={s.n} className="rounded-xl border border-slate-800 bg-slate-900/40 p-6">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600/15 text-sm font-bold text-indigo-300">
                {s.n}
              </div>
              <h3 className="mt-4 text-base font-semibold text-slate-100">{s.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-400">{s.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Features */}
      <section id="features" className="border-y border-slate-800/60 bg-slate-950/40">
        <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
          <SectionTitle eyebrow="Features" title="Everything you need to trust the answer" />
          <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {FEATURES.map((f) => (
              <div key={f.title} className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-600/15 text-indigo-300">
                  <Icon name={f.icon} className="h-5 w-5" />
                </div>
                <h3 className="mt-4 text-sm font-semibold text-slate-100">{f.title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-slate-400">{f.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Under the hood — technical architecture */}
      <section id="architecture" className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
        <SectionTitle eyebrow="Architecture" title="What happens when you hit run" />
        <div className="mt-12 grid gap-10 lg:grid-cols-2 lg:gap-16">
          {/* The request pipeline */}
          <ol className="relative">
            {PIPELINE.map((s, i) => (
              <li key={s.title} className="relative flex gap-4 pb-8 last:pb-0">
                {i < PIPELINE.length - 1 && (
                  <span
                    aria-hidden
                    className="absolute left-5 top-11 h-[calc(100%-1rem)] w-px bg-gradient-to-b from-indigo-700/50 to-slate-800"
                  />
                )}
                <span className="relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-indigo-700/50 bg-indigo-950/50 text-indigo-300">
                  <Icon name={s.icon} className="h-5 w-5" />
                </span>
                <div className="pt-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-semibold text-slate-100">{s.title}</h3>
                    {s.tag && (
                      <span className="rounded-full border border-slate-700 bg-slate-900/70 px-2 py-0.5 font-mono text-[10px] text-slate-400">
                        {s.tag}
                      </span>
                    )}
                  </div>
                  <p className="mt-1.5 text-sm leading-relaxed text-slate-400">{s.body}</p>
                </div>
              </li>
            ))}
          </ol>

          {/* Why it's built this way */}
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-indigo-400">
              Why it’s built this way
            </p>
            <div className="mt-4 space-y-4">
              {DECISIONS.map((d) => (
                <div key={d.title} className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
                  <h3 className="text-sm font-semibold text-slate-100">{d.title}</h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-slate-400">{d.body}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Security */}
      <section id="security" className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
        <SectionTitle eyebrow="Security" title="Read-only by default. Your data stays safe." />
        <div className="mt-10 grid gap-5 sm:grid-cols-2">
          {TRUST.map((t) => (
            <div
              key={t.title}
              className="flex gap-4 rounded-xl border border-slate-800 bg-slate-900/40 p-5"
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-300">
                <Icon name={t.icon} className="h-5 w-5" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-slate-100">{t.title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-slate-400">{t.body}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Final CTA */}
      <section className="mx-auto max-w-6xl px-4 pb-24 sm:px-6">
        <div className="relative overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/40 px-6 py-14 text-center">
          <div
            aria-hidden
            className="pointer-events-none absolute left-1/2 top-0 h-40 w-96 max-w-[90vw] -translate-x-1/2 rounded-full bg-indigo-600/15 blur-3xl"
          />
          <h2 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
            Query your data in plain English.
          </h2>
          <p className="mx-auto mt-3 max-w-md text-sm text-slate-400">
            Try it on the built-in sample database — no signup required.
          </p>
          <Link href="/app" className={`mt-6 ${PRIMARY_CTA}`}>
            Open the console →
          </Link>
        </div>
      </section>
    </div>
  );
}
