"""Scoped Spider dev execution-accuracy eval for DBWhisper's generation model.

For each sampled Spider dev question: prompt qwen3-32b (DBWhisper's generation model,
temp 0.1) with the database's real schema, generate SQL, execute both it and the gold
SQL against the real Spider SQLite database, and compare result sets — ORDERED when the
gold has ORDER BY, multiset otherwise (standard execution match).

Scope honesty: this measures the NL->SQL *generation* core. The deployed DBWhisper agent
additionally does schema retrieval (which matters on large schemas — Spider's per-DB
schemas are small) and a read-only validator (which gates writes, not SELECT correctness).
Same model + temperature as the pipeline, so the number reflects the generation quality.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

HERE = Path(__file__).parent
SPIDER = HERE / "spider" / "spider_data"
_ENV = (HERE.parent / ".env").read_text(encoding="utf-8")


def _env(n: str) -> str:
    m = re.search(rf"^{n}=(.+)$", _ENV, re.M)
    return m.group(1).strip().strip('"').strip("'") if m else ""


os.environ["GROQ_API_KEY"] = _env("GROQ_API_KEY")
os.environ["GOOGLE_API_KEY"] = _env("GEMINI_API_KEY")  # fallback provider

DEV = json.loads((SPIDER / "dev.json").read_text(encoding="utf-8"))
STEP = int(os.environ.get("SPIDER_STEP", "7"))       # deterministic DB-diverse sample (~148 of 1034)
LIMIT = int(os.environ.get("SPIDER_LIMIT", "0"))     # >0 = quick test on the first N sampled
SAMPLE = DEV[::STEP]
if LIMIT:
    SAMPLE = SAMPLE[:LIMIT]


def db_path(db_id: str) -> Path:
    return SPIDER / "database" / db_id / f"{db_id}.sqlite"


def schema_ddl(db_id: str) -> str:
    con = sqlite3.connect(f"file:{db_path(db_id)}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL").fetchall()
        return "\n".join(r[0] for r in rows)
    finally:
        con.close()


def run_sql(db_id: str, sql: str):
    con = sqlite3.connect(f"file:{db_path(db_id)}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def norm(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return int(f) if f.is_integer() else round(f, 4)
    if v is None:
        return None
    return str(v).strip()


def result_match(gen_rows, gold_rows, ordered: bool) -> bool:
    g = [tuple(norm(x) for x in r) for r in gen_rows]
    gd = [tuple(norm(x) for x in r) for r in gold_rows]
    if ordered:
        return g == gd
    return sorted(map(str, g)) == sorted(map(str, gd))  # multiset, order-insensitive


def clean_sql(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"(?is)<think>.*?</think>", " ", t).strip()
    m = re.search(r"```(?:sql)?\s*(.+?)```", t, re.S | re.I)
    if m:
        t = m.group(1).strip()
    m2 = re.search(r"(?is)\b(select|with)\b.*", t)
    if m2:
        t = m2.group(0)
    return t.split(";")[0].strip()


def invoke_retry(llm, prompt, tries=5):
    """Return the model's text, or None if the call could not be completed
    (rate-limit exhausted / transport error). None is EXCLUDED from scoring —
    a quota failure must never be counted as a wrong SQL answer."""
    for i in range(tries):
        try:
            return llm.invoke(prompt).content
        except Exception as e:
            msg = str(e).lower()
            if any(s in msg for s in ("quota", "429", "resourceexhausted", "exhausted", "rate limit", "too many")) and i < tries - 1:
                print(f"       rate limit — sleeping 20s (retry {i + 1})")
                time.sleep(20)
                continue
            return None


def main() -> None:
    from langchain_groq import ChatGroq

    # qwen/qwen3-32b on Groq — DBWhisper's ACTUAL generation model, at the same temp (0.1) as the
    # deployed pipeline, so the number reflects real production generation quality. max_retries=0 so
    # our own backoff handles throttling; max_tokens bounds the reasoning trace (qwen3 emits
    # <think>…</think>, which clean_sql strips before the SQL is executed/scored).
    llm = ChatGroq(model="qwen/qwen3-32b", temperature=0.1, max_retries=0, max_tokens=2048)
    ok = invalid_gold = llm_failed = 0
    scored = 0
    rows = []

    def _save():
        res = {
            "model": "qwen/qwen3-32b (Groq)",
            "benchmark": "Spider dev",
            "method": (
                "execution accuracy (result-set match, ordered when gold has ORDER BY); "
                "DBWhisper's generation model (qwen/qwen3-32b) at temp 0.1 with the database "
                "schema in context; only examples the model actually answered are scored — "
                "transient transport/throttle failures are excluded, never counted as wrong"
            ),
            "sampled": len(SAMPLE),
            "scored": scored,
            "invalid_gold_skipped": invalid_gold,
            "llm_unavailable_skipped": llm_failed,
            "correct": ok,
            "exec_accuracy": round(ok / scored, 4) if scored else 0,
            "rows": rows,
        }
        (HERE / "spider_results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

    for ex in SAMPLE:
        db_id, q, gold = ex["db_id"], ex["question"], ex["query"]
        try:
            gold_rows = run_sql(db_id, gold)  # gold must run; if it doesn't, exclude from denominator
        except Exception:
            invalid_gold += 1
            continue
        ordered = " order by " in f" {gold.lower()} "
        prompt = (
            "You are an expert text-to-SQL system for SQLite.\n\n"
            f"Schema:\n{schema_ddl(db_id)}\n\n"
            "Write a single SQLite query that answers the question. Return ONLY the SQL — "
            "no explanation, no markdown fences.\n\n"
            f"Question: {q}"
        )
        raw = invoke_retry(llm, prompt)
        if raw is None:  # rate-limit/transport failure — EXCLUDE, never score as wrong
            llm_failed += 1
            print(f"  [--]  ·  {db_id[:18]:<18} LLM unavailable — excluded ({llm_failed} so far)")
            continue
        gen, correct = "", False
        try:
            gen = clean_sql(raw)
            correct = result_match(run_sql(db_id, gen), gold_rows, ordered)
        except Exception as e:
            gen = gen or f"BADSQL:{type(e).__name__}"  # model emitted unrunnable SQL = a genuine miss
        scored += 1
        ok += int(correct)
        rows.append({"db_id": db_id, "q": q, "gold": gold, "gen": gen, "ok": correct, "ordered": ordered})
        print(f"  [{'ok' if correct else 'XX'}] {scored:>3} {db_id[:18]:<18} {q[:44]}")
        _save()  # incremental — whatever completes is captured even if quota dies mid-run
        time.sleep(5)
    _save()
    acc = f"{ok / scored:.1%}" if scored else "n/a (0 scored)"
    print(
        f"\nSPIDER dev exec-accuracy: {ok}/{scored} = {acc}  "
        f"(excluded: {invalid_gold} unrunnable gold, {llm_failed} LLM-unavailable)"
    )


if __name__ == "__main__":
    main()
