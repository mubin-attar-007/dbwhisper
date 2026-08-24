import type { Metadata } from "next";
import Link from "next/link";
import { SITE_URL } from "@/src/lib/site";
import { Icon, type IconName } from "../components/Icon";

/**
 * The public marketing page.
 *
 * Every number and safety claim here is governed by `docs/v2/CLAIM_AUDIT.md`. Two rules from that
 * document shape the copy and should survive any redesign:
 *
 *  1. A percentage never appears without its numerator, denominator and the version of the thing
 *     that produced it. The proof strip therefore reads "343 / 343" against a named corpus and a
 *     named policy version, not "100%".
 *  2. The page names mechanisms, not outcomes. The policy engine *checks*, *admits* and *rejects*;
 *     it does not "prove", and nothing is "always" or "never" unless a test enumerates the space.
 *
 * The previous version of this file carried an "82% execution accuracy" card and a "100%
 * fail-closed" card. Both were retired on 2026-08-24. The first was measured under a prompt that has
 * since been deleted and its harness is untracked, so a stranger cannot reproduce it. The second
 * named a property its own run never observed: in all four recorded unsafe rows the model declined
 * in prose and no statement ever reached the validator, so that run contains zero observations of
 * the validator blocking anything. See CLAIM_AUDIT §3.2 and §4.4.
 */

export const metadata: Metadata = {
  title: "DBWhisper — Ask your database anything",
  description:
    "Connect PostgreSQL, MySQL or SQL Server and query in plain English. DBWhisper writes read-only SQL, checks it against an AST policy engine, runs it, and shows the answer with a chart.",
};

/** One engine list, used everywhere on the page so the copy cannot contradict itself. */
const ENGINES = "PostgreSQL, MySQL and SQL Server (SQLite for local runs)";

const STEPS = [
  {
    n: "1",
    title: "Enroll a database",
    body: `Point DBWhisper at ${ENGINES} using a read-only role. Enrollment is an API call (POST /schemas/enroll) that introspects the schema through SQLAlchemy reflection; the hosted demo ships with a sample database already enrolled.`,
  },
  {
    n: "2",
    title: "Ask in plain English",
    body: "“Top 5 products by revenue this quarter.” No SQL, no schema-hunting — just the question.",
  },
  {
    n: "3",
    title: "Get SQL + results you can check",
    body: "It writes read-only SQL, runs it through the policy engine, shows you the statement, and returns a table, a chart, and a plain-English summary.",
  },
];

const FEATURES: { icon: IconName; title: string; body: string }[] = [
  {
    icon: "table",
    title: "SQL you can read & edit",
    body: "The generated SQL is shown with every answer — and you can edit it and re-run it, still through the same read-only policy engine.",
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
    body: "PostgreSQL · MySQL · SQL Server. Download any result as CSV or JSON, or copy it as a Markdown table.",
  },
];

const TRUST: { icon: IconName; title: string; body: string }[] = [
  {
    icon: "database",
    title: "Read-only enforced in code",
    body: "Generated and hand-edited SQL take the same path through the policy engine; writes, drops and DDL are rejected there. Execution runs through a least-privilege database role inside a transaction that is rolled back.",
  },
  {
    icon: "table",
    title: "The exact SQL, every time",
    body: "Every answer ships with the statement that produced it, so you can read it, edit it and re-run it through the same policy check.",
  },
  {
    icon: "star",
    title: "Least-privilege connections",
    body: "Connect with a read-only database role; DBWhisper refuses to enroll a connection that probes as writable. The probe is a backstop for the role you configure, not a substitute for it.",
  },
  {
    icon: "spark",
    title: "No black-box code execution",
    body: "The model emits SQL and structured JSON, and calls a fixed set of named retrieval tools. There is no path that executes model-authored code on the server.",
  },
];

/**
 * The proof strip. Every figure comes from CLAIM_AUDIT §4.1 and is reproducible from a clean
 * checkout with one command — that is the bar for putting a number on this page. Values carry
 * their own denominators so no card can be read as a bare percentage.
 */
const PROOF: { value: string; label: string; note: string }[] = [
  {
    value: "343 / 343",
    label: "Adversarial deny cases denied",
    note: "Every statement in the deny corpus — 343 case×dialect combinations drawn from 255 cases — was denied by policy sql_policy@2.0.0.",
  },
  {
    value: "210 / 210",
    label: "Benign queries still admitted",
    note: "The corpus checks the other direction too: 210 legitimate read-only expansions were admitted, so the deny figure is not bought with false positives.",
  },
  {
    value: "0",
    label: "Write paths to your data",
    note: "Generated and hand-edited SQL take one route: an AST policy check, then execution inside a transaction that is rolled back. On PostgreSQL, MySQL and SQLite that transaction is opened read-only at the database.",
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
    body: "Embedding similarity pulls the tables a question needs, so the model sees a focused schema instead of the whole catalog — smaller prompts and fewer invented columns. Retrieval is top-k over per-table summaries; no benchmark on a large schema has been published yet.",
  },
  {
    icon: "code",
    title: "SQL is generated",
    tag: "tool-calling agent",
    body: "An LLM agent writes the query with the retrieved tables in context, retrying across whichever of six configured providers have credentials (OpenAI → OpenRouter → DeepSeek → Groq → Anthropic → Gemini).",
  },
  {
    icon: "shield",
    title: "Checked read-only before it runs",
    tag: "sql_policy@2.0.0",
    body: "A SQLGlot AST policy engine parses the statement and rejects anything that is not a single read-only SELECT — writes, DDL, multi-statements, system-catalog access and blocked functions. This is code between generation and execution, not an instruction in the prompt.",
  },
  {
    icon: "database",
    title: "Run & explained",
    body: "The statement runs through one audited execution path — least-privilege role, statement timeout, row cap, rolled-back transaction — and comes back as a sortable table, an auto-selected chart, and a one-line summary.",
  },
];

const DECISIONS: { title: string; body: string }[] = [
  {
    title: "Schema-grounded, not schema-dumped",
    body: "Instead of pasting a whole schema into every prompt, DBWhisper retrieves only the relevant tables via pgvector embeddings. Smaller prompts, fewer hallucinated columns — and the tables the statement actually touched come back with the answer, so you can check the grounding yourself.",
  },
  {
    title: "Fail-closed, not prompt-please",
    body: "“Read-only” is not an instruction the model might ignore — it is a policy engine between generation and execution. A statement the engine will not classify as read-only is not executed. It is a structural filter over a parsed AST, not a proof, so pair it with a least-privilege role.",
  },
  {
    title: "Provider fallback, not a single point of failure",
    body: "Six providers are wired in priority order (OpenAI → OpenRouter → DeepSeek → Groq → Anthropic → Gemini); a generation call that fails or is rate-limited moves to the next provider that has credentials, so how many are live depends on which keys you set. The v2 router goes further — it picks a model by the capability a step needs and opens a circuit breaker on one that keeps failing — and its local Ollama profile needs no API key at all.",
  },
  {
    title: "A real graph, with real pauses",
    body: "The /v2 API runs the workflow as a LangGraph StateGraph — retrieve → understand → generate → validate → execute → verify → summarize — where a repair loop re-enters validation instead of trusting its own fix. Clarification and approval use LangGraph interrupts against a durable checkpointer, so a run genuinely pauses and resumes after a restart. The console above still calls the v1 tool-calling endpoint.",
  },
];

/** Shown in the mock's URL chrome. Derived so it can never name a domain the project does not use. */
const MOCK_URL = `${SITE_URL.replace(/^https?:\/\//, "")}/app`;

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
          {MOCK_URL}
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
            policy: allow
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
              Connect PostgreSQL, MySQL or SQL Server and query in plain English. DBWhisper writes
              read-only SQL, checks it before it runs, and shows the answer with a chart — no SQL
              required.
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

      {/* Proof strip — reproducible policy-corpus figures, provenance in CLAIM_AUDIT §4.1 */}
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
              Evaluated on an adversarial policy corpus
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

          <div className="mx-auto mt-8 max-w-3xl space-y-4 text-xs leading-relaxed text-slate-400">
            <p>
              <span className="font-semibold text-slate-300">Provenance.</span> Corpus{" "}
              <code className="rounded bg-slate-900 px-1 font-mono text-[11px] text-slate-300">
                app/evaluation/datasets/adversarial/sql_policy_cases.yaml
              </code>{" "}
              v2.0.0 — 255 distinct cases expanded across PostgreSQL, MySQL, SQL Server and SQLite to
              577 case×dialect runs (343 deny, 210 allow, 24 needs-approval). System under test:{" "}
              <code className="rounded bg-slate-900 px-1 font-mono text-[11px] text-slate-300">
                sql_policy@2.0.0
              </code>{" "}
              on sqlglot 30.17.0. No model is involved — this measures a deterministic decision
              function, so there is no prompt version and no temperature. Reproduce it with{" "}
              <code className="rounded bg-slate-900 px-1 font-mono text-[11px] text-slate-300">
                uv run pytest tests/sqlpolicy
              </code>{" "}
              (616 tests, verified 2026-08-24).
            </p>
            <p>
              <span className="font-semibold text-slate-300">Limitations, stated plainly.</span> This
              measures the policy decision only — nothing in the corpus is sent to a real database. It
              is a structural filter over a parsed AST, not a proof: the guarantee is bounded by
              sqlglot&rsquo;s parse fidelity per dialect. And a corpus measures the attacks someone
              thought to write down, so 343 of 343 is evidence of no <em>known</em> bypass, not of no
              bypass.
            </p>
            <p>
              <span className="font-semibold text-slate-300">
                Why there is no accuracy percentage here.
              </span>{" "}
              Query-accuracy figures are tracked separately and are re-published only when a run can
              be reproduced from a clean checkout. Unsafe prompts are handled in two independent
              places — the model declines, and if it does not, the policy engine denies — and the one
              recorded end-to-end run only ever exercised the first of those, so it is not evidence
              about the second. The policy layer is measured on its own corpus above. Full audit:{" "}
              <code className="rounded bg-slate-900 px-1 font-mono text-[11px] text-slate-300">
                docs/v2/CLAIM_AUDIT.md
              </code>
              .
            </p>
          </div>
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
          <SectionTitle eyebrow="Features" title="Everything you need to check the answer" />
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
        <SectionTitle eyebrow="Security" title="Read-only by default. You can see exactly what runs." />
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
        <p className="mx-auto mt-8 max-w-3xl text-center text-xs leading-relaxed text-slate-400">
          Scope, because it varies by engine: PostgreSQL, MySQL and SQLite additionally open each
          query inside a transaction the database itself marks read-only. SQL Server has no
          session-level read-only mode — there the controls are the policy engine, a least-privilege
          login and the driver query timeout. Every connection rolls back in a{" "}
          <code className="rounded bg-slate-900 px-1 font-mono text-[11px] text-slate-300">
            finally
          </code>{" "}
          block.
        </p>
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
            Try it on the built-in sample database — no signup required on the hosted demo.
          </p>
          <Link href="/app" className={`mt-6 ${PRIMARY_CTA}`}>
            Open the console →
          </Link>
        </div>
      </section>
    </div>
  );
}
