# Historical evaluation results (v1) — archived, not publishable

These four JSON files are **copies** of the v1 evaluation output that lived in the untracked `eval/`
directory. The originals were left in place; nothing was moved.

They are here for one reason: so the numbers that circulated in the project's own README and
marketing copy have a permanent, citable home next to the reasons they cannot be published. Deleting
them would make the correction unverifiable.

## What each file is

| File | Headline it produced | Status |
|---|---|---|
| `results.json` | "82% execution accuracy", "18 of 22", "100% fail-closed" | **NOT PUBLISHABLE** — see `results.provenance.md` |
| `rescore.json` | "3 of 22" | **UNPROVABLE (harness defect)** — see `rescore.provenance.md` |
| `baseline_results.json` | "22/22, exec_accuracy 1.0" for a no-retrieval baseline | **NOT PUBLISHABLE ALONE** — see `baseline_results.provenance.md` |
| `spider_results.json` | "72.7% on Spider dev" | **CONDITIONALLY PUBLISHABLE** — see `spider_results.provenance.md` |

Each file has a sibling `*.provenance.md` carrying the full block `docs/v2/CLAIM_AUDIT.md` §4
requires — dataset, n, model, prompt version, date, environment, exclusions, limitations — plus a
plain statement of whether the run can be reproduced today.

## The short version

Three of the four are unusable, and the reasons are structural rather than a matter of taste:

1. **The harness was never committed.** `git ls-files eval` returned `0` when the audit ran, and
   returns `0` today. A measurement a stranger cannot re-run from a clean checkout does not exist for
   publication purposes.
2. **They measure software that has been deleted.** The runs date from 2026-07-08/10 and used prompt
   `sql_agent_prompt@1.0`, retired on 2026-08-21 (`app/agent/prompt.py:5-7`). The contamination is
   visible inside the data: row 1 of `results.json` generated
   `SELECT COUNT(*) AS total_customers FROM CompanyMaster;` against a four-table store that has no
   such table — a table name leaked from the hard-coded schema in the old prompt.
3. **The scoring was looser than advertised.** `eval/harness.py:35-41` compared result sets as a
   `frozenset`, which collapses duplicate rows and ignores ordering, while describing itself as
   "Spider-style". Standard Spider execution match compares multisets and respects order under
   `ORDER BY`.

The v2 harness in `app/evaluation/` is the response to all three: it is committed, it runs offline
from a clean checkout with generated fixtures, it stamps prompt and component versions onto every
run, and its primary correctness metric is a multiset comparison that respects `ORDER BY`
(`app/evaluation/metrics.py`).

## Replacement

Run `uv run python -m app.evaluation.cli smoke` (or `scripts/eval-smoke.sh` / `.ps1`). It needs no
credentials, no network and no listening port, and every number it prints arrives with its
numerator, its denominator and its provenance block.
