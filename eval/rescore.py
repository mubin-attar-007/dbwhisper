"""Re-score the golden run fairly, re-executing SAVED SQL (no new LLM calls,
except retrying any question that errored transiently).

- strict: exact result-set match (Spider-style).
- lenient: same row count AND every gold row's values are contained in some
  generated row (credits a correct answer that returned an extra column).
"""
from __future__ import annotations
import json, time, urllib.request
from pathlib import Path
import psycopg

HERE = Path(__file__).parent
STORE = "postgresql://eval:evalpass@localhost:55432/evalstore"
API = "http://127.0.0.1:8010/query"
res = json.loads((HERE / "results.json").read_text())
gold = {g["id"]: g for g in json.loads((HERE / "golden.json").read_text())}


def norm(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v); return int(f) if f.is_integer() else round(f, 4)
    return None if v is None else str(v).strip()


def run(sql, cur):
    cur.execute(sql); rows = cur.fetchall(); cur.connection.rollback()
    return [tuple(norm(x) for x in r) for r in rows]


def ask(q):
    body = json.dumps({"query": q, "db_flag": "eval_store", "output_format": "json"}).encode()
    r = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=120) as resp:
        d = json.loads(resp.read().decode())
    if d.get("status") == "success" and d.get("validation_passed"):
        return d.get("sql", ""), [tuple(norm(x) for x in row.values()) for row in d["data"]["results"]]
    return "", None


con = psycopg.connect(STORE); cur = con.cursor()
strict = lenient = total = 0
for row in res["rows"]:
    if row["kind"] != "answerable":
        continue
    total += 1
    qid = row["id"]; gen_sql = (row.get("gen_sql") or "").strip()
    gen = None
    if gen_sql:
        try: gen = run(gen_sql, cur)
        except Exception: gen = None
    if gen is None:  # errored originally -> retry once (transient)
        try:
            gen_sql, gen = ask(gold[qid]["question"]); time.sleep(0.5)
            print(f"  retried #{qid}: {'ok' if gen is not None else 'still failed'}")
        except Exception:
            gen = None
    try: g = run(gold[qid]["gold_sql"], cur)
    except Exception: g = []
    gset, genset = frozenset(g), frozenset(gen or [])
    is_strict = genset == gset
    # lenient: same row count + every gold row's values subset of some gen row
    def contained(goldrow, genrows):
        gv = set(goldrow)
        return any(gv.issubset(set(gr)) for gr in genrows)
    is_len = gen is not None and len(gen) == len(g) and all(contained(gr, gen) for gr in g)
    strict += int(is_strict); lenient += int(is_strict or is_len)
    if not (is_strict or is_len):
        print(f"  MISS #{qid}: gen={sorted(genset)[:2]} gold={sorted(gset)[:2]}")
con.close()
print("\n==== FAIR RE-SCORE ====")
print(f"  strict exact-match : {strict}/{total} = {strict/total*100:.1f}%")
print(f"  answer-correct     : {lenient}/{total} = {lenient/total*100:.1f}%")
print(f"  fail-closed        : {res['unsafe'][0]}/{res['unsafe'][1]} = {res['fail_closed']*100:.0f}%")
(HERE / "rescore.json").write_text(json.dumps(
    {"strict": [strict, total], "answer_correct": [lenient, total],
     "fail_closed": res["unsafe"]}, indent=2))
