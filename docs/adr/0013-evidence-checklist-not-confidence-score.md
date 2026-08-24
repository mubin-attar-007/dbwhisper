# ADR 0013 — An evidence checklist, not a confidence score

**Status:** Accepted — 2026-08-21, Phase 6; the three-valued UI landed in Phase 12.
**Principle:** "Truthful evidence" — `docs/v2/TARGET_ARCHITECTURE.md` §2, §13.

## Context

The conventional way to tell a user how much to trust a generated answer is a number: 87% confident.
Nobody can defend such a number. It is not a calibrated probability, it is not derived from anything
measurable, and the one thing it reliably does is transfer responsibility from the system to the
reader, who now has to decide what 87% means. `docs/v2/CLAIM_AUDIT.md` exists because this project
had already shipped figures that could not be reproduced; a per-answer confidence score would be the
same failure repeated once per query.

## Decision

`app/graph/state.py::evidence_checklist(state)` returns seven **facts about what happened**, each
`True`, `False`, or `None`:

`schema_grounded` · `policy_passed` · `read_only_enforced` · `result_shape_verified` ·
`summary_grounded` · `human_approved` · `not_truncated`

Every item is derived from a recorded step, so each one is checkable against the trace rather than
asserted. `None` means "not applicable to this run" — a run that never needed approval is not a run
that failed to get one — and the UI renders the three states differently
(`web/app/components/EvidenceStrip.tsx`, `web/src/lib/evidence.ts`, tested in
`web/tests/unit/evidence.test.ts`). Collapsing "reported false" into "not reported" was a specific
bug this design was written to prevent.

## Consequences

**What it buys.** Every item can be traced to the step that produced it, so a sceptical reviewer can
audit the answer instead of believing it. The vocabulary is honest: the system reports what it did,
not how it feels. It composes with the claim rules — no percentage is published without a numerator
and a denominator, and there is no per-answer number to misquote.

**What it costs.**

* **Seven booleans are harder to consume than one number.** They cannot be averaged, sorted or put
  on a dashboard without someone deciding what the aggregate means — and the moment someone does,
  they have reinvented the confidence score.
* **The UI has to teach the reader what each item means.** A checklist item is only useful to
  someone who knows what "shape verified" covers; a number needs no explanation, which is precisely
  its appeal and its dishonesty.
* **A fully green checklist does not mean the answer is right.** Every item is about *process* —
  the schema was consulted, the policy passed, the transaction was read-only. A query can satisfy
  all seven and still answer a different question than the one asked. `result_shape_verified` is the
  only item pointed at correctness, and it is deliberately conservative (ADR 0012). This is the
  checklist's most important limitation and it belongs in the UI copy, not only here.
* **`read_only_enforced` reports what the session did, not what the credential can do**, and it is
  `False` on SQL Server, where no session-level read-only mode exists
  (`app/execution/connections.py`). Whether the role itself lacks write privileges is a separate,
  tri-state answer (`app/execution/readonly.py::ReadOnlyStatus`) established at enrollment. Two
  different questions, deliberately not merged into one green tick.
