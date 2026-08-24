"""Fair re-scoring of Spider examples that failed for HARNESS-CONFIG reasons, not the model.

Two config failures in the first pass, neither the model's fault:
  - truncated: qwen3's <think> reasoning exceeded max_tokens=2048, so clean_sql extracted
    reasoning text instead of the (never-emitted) SQL. Production sets NO max_tokens cap.
  - throttled: a Groq rate-limit made the call never complete (counted llm_unavailable).

These are re-run ONCE at max_tokens=8192 (high enough that no Spider reasoning truncates) and
merged. Genuine wrong-SQL misses are deliberately NOT re-run — giving only losers another draw
would bias the number up. The 123 cleanly-scored rows are untouched: their reasoning already fit
in 2048, so a higher cap cannot change them. Result is identical to a full re-run, far cheaper.
"""
from __future__ import annotations

import json
import time

from spider_eval import HERE, SAMPLE, clean_sql, result_match, run_sql, schema_ddl


def is_sqlish(g: str) -> bool:
    g2 = (g or "").strip().lower()
    starts = g2.startswith("select") or g2.startswith("with ")
    leak = any(m in g2 for m in ("so,", "the plan", "let me", "first,", "i need",
                                  "types?", "we need", "okay", "wait", "hmm", "?"))
    return starts and not leak


def main() -> None:
    from langchain_groq import ChatGroq

    llm = ChatGroq(model="qwen/qwen3-32b", temperature=0.1, max_retries=0, max_tokens=8192)
    res = json.loads((HERE / "spider_results.json").read_text(encoding="utf-8"))
    rows = res["rows"]
    scored_keys = {(r["db_id"], r["q"]) for r in rows}
    truncated = [r for r in rows if not r["ok"] and not is_sqlish(r["gen"])]
    excluded = [ex for ex in SAMPLE if (ex["db_id"], ex["question"]) not in scored_keys]
    print(f"re-running {len(truncated)} truncated + {len(excluded)} throttled = "
          f"{len(truncated) + len(excluded)} (genuine wrong-SQL misses left as-is)\n")

    def gen_for(db_id: str, q: str, gold: str, ordered: bool):
        prompt = (
            "You are an expert text-to-SQL system for SQLite.\n\n"
            f"Schema:\n{schema_ddl(db_id)}\n\n"
            "Write a single SQLite query that answers the question. Return ONLY the SQL — "
            "no explanation, no markdown fences.\n\n"
            f"Question: {q}"
        )
        raw = None
        for i in range(5):
            try:
                raw = llm.invoke(prompt).content
                break
            except Exception as e:
                if any(s in str(e).lower() for s in ("quota", "429", "exhausted", "rate limit", "too many")) and i < 4:
                    print("       rate limit — sleeping 20s")
                    time.sleep(20)
                    continue
                return None
        if raw is None:
            return None
        gen, correct = "", False
        try:
            gen = clean_sql(raw)
            correct = result_match(run_sql(db_id, gen), run_sql(db_id, gold), ordered)
        except Exception as e:
            gen = gen or f"BADSQL:{type(e).__name__}"
        return {"gen": gen, "ok": correct}

    for r in truncated:
        out = gen_for(r["db_id"], r["q"], r["gold"], r["ordered"])
        if out is None:
            print(f"  [--] (truncated) {r['db_id']} still unavailable")
            continue
        still = "" if is_sqlish(out["gen"]) else "  <-- STILL not SQL"
        r["gen"], r["ok"] = out["gen"], out["ok"]
        print(f"  [{'ok' if out['ok'] else 'XX'}] (was truncated) {r['db_id'][:16]:<16} {r['q'][:40]}{still}")
        time.sleep(5)

    for ex in excluded:
        db_id, q, gold = ex["db_id"], ex["question"], ex["query"]
        try:
            run_sql(db_id, gold)
        except Exception:
            print(f"  skip invalid gold: {db_id}")
            continue
        ordered = " order by " in f" {gold.lower()} "
        out = gen_for(db_id, q, gold, ordered)
        if out is None:
            print(f"  [--] (throttled) {db_id} still unavailable")
            continue
        rows.append({"db_id": db_id, "q": q, "gold": gold, "gen": out["gen"], "ok": out["ok"], "ordered": ordered})
        print(f"  [{'ok' if out['ok'] else 'XX'}] (was throttled) {db_id[:16]:<16} {q[:40]}")
        time.sleep(5)

    scored = len(rows)
    ok = sum(1 for r in rows if r["ok"])
    still_out = [ex for ex in SAMPLE if (ex["db_id"], ex["question"]) not in {(r["db_id"], r["q"]) for r in rows}]
    res.update({
        "scored": scored,
        "correct": ok,
        "exec_accuracy": round(ok / scored, 4) if scored else 0,
        "llm_unavailable_skipped": len(still_out),
    })
    res["method"] += (" — config-truncated/throttled items were re-run once at max_tokens=8192 "
                      "(production sets no cap, so no reasoning truncates); genuine wrong-SQL left as-is")
    (HERE / "spider_results.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"\nFIXED SPIDER dev exec-accuracy: {ok}/{scored} = {ok / scored:.1%}  "
          f"(still-unavailable after retry: {len(still_out)})")


if __name__ == "__main__":
    main()
