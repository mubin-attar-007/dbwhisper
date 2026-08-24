"""Naive single-prompt baseline for the golden set — the honest comparison point.

Same model (gemini-2.5-flash) and the SAME execution-accuracy scoring as harness.py,
but NO retrieval and NO validator: one prompt with the full schema, take whatever SQL
the model returns, execute it, compare result sets. This is the "just ask the LLM"
baseline that DBWhisper's retrieval + read-only validator pipeline is measured against.

Scored against eval_store.sqlite, whose rows are identical to the Postgres store the
DBWhisper pipeline was scored on (make_pg.py copies them verbatim), so the two numbers
are comparable. Both the model's SQL and the gold SQL run on the same store.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

HERE = Path(__file__).parent
_ENV = (HERE.parent / ".env").read_text(encoding="utf-8")


def _env(name: str) -> str:
    m = re.search(rf"^{name}=(.+)$", _ENV, re.M)
    return m.group(1).strip().strip('"').strip("'") if m else ""


os.environ["GROQ_API_KEY"] = _env("GROQ_API_KEY")

DB = HERE / "eval_store.sqlite"
GOLDEN = json.loads((HERE / "golden.json").read_text(encoding="utf-8"))


def schema_ddl() -> str:
    con = sqlite3.connect(DB)
    try:
        return "\n".join(r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE type='table'"))
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


def as_set(rows) -> frozenset:
    return frozenset(tuple(norm(x) for x in r) for r in rows)


def run_ro(sql: str):
    """Execute read-only against the eval store (baseline queries are SELECT-only)."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def clean_sql(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"(?is)<think>.*?</think>", " ", t).strip()  # qwen3 reasoning blocks, if any
    m = re.search(r"```(?:sql)?\s*(.+?)```", t, re.S | re.I)
    if m:
        t = m.group(1).strip()
    m2 = re.search(r"(?is)\b(select|with)\b.*", t)
    if m2:
        t = m2.group(0)
    return t.split(";")[0].strip()


def main() -> None:
    from langchain_groq import ChatGroq

    # Same model + temperature as DBWhisper's pipeline (Groq is its preferred provider,
    # qwen/qwen3-32b @ 0.1) — so the ONLY difference is naive prompting vs the full
    # retrieval + read-only-validator pipeline. That isolates the pipeline's contribution.
    llm = ChatGroq(model="qwen/qwen3-32b", temperature=0.1)
    ddl = schema_ddl()
    answerable = [g for g in GOLDEN if g["kind"] == "answerable"]
    ok = 0
    out = []
    for g in answerable:
        prompt = (
            "You are a text-to-SQL system for a SQLite database.\n\n"
            f"Schema:\n{ddl}\n\n"
            "Write a single SQLite SELECT query that answers the question. "
            "Return ONLY the SQL — no explanation, no markdown fences.\n\n"
            f"Question: {g['question']}"
        )
        gen, match = "", False
        # LLM call with quota/backoff retry — a rate-limit is NOT a baseline miss.
        raw = None
        for attempt in range(8):
            try:
                raw = llm.invoke(prompt).content
                break
            except Exception as e:
                msg = str(e).lower()
                if any(s in msg for s in ("quota", "429", "resourceexhausted", "exhausted", "rate limit")) and attempt < 7:
                    print(f"       quota hit — sleeping 62s (retry {attempt + 1})")
                    time.sleep(62)
                    continue
                raise
        try:
            gen = clean_sql(raw)
            got = as_set(run_ro(gen))
            gold = as_set(run_ro(g["gold_sql"]))
            match = got == gold
        except Exception as e:  # bad SQL from the model = a genuine miss
            gen = gen or f"ERR:{type(e).__name__}"
        ok += int(match)
        out.append({"id": g["id"], "q": g["question"], "gen_sql": gen, "ok": match})
        print(f"  [{'ok' if match else 'XX'}] #{g['id']:<2} {g['question'][:52]}")
        time.sleep(6)  # throttle under the free-tier per-minute limit
    n = len(answerable)
    res = {
        "model": "qwen/qwen3-32b (Groq)",
        "method": "naive single-prompt (full schema, no retrieval, no validator), temp 0.1, exec-accuracy vs gold on eval_store.sqlite — same model/temp as the DBWhisper pipeline",
        "n": n,
        "correct": ok,
        "exec_accuracy": round(ok / n, 4),
        "rows": out,
    }
    (HERE / "baseline_results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"\nBASELINE exec-accuracy: {ok}/{n} = {ok / n:.1%}")


if __name__ == "__main__":
    main()
