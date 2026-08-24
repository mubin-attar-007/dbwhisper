# ADR 0008 — Egress is a rank-ordered enum, enforced where data would leave

**Status:** Accepted — 2026-08-21 (Phase 1 type, Phase 4 router enforcement, Phase 6 summary
enforcement).
**Related:** ADR 0004 (providers), ADR 0005 (modes).

## Context

"May we use a hosted model?" is not a boolean. A deployment might be happy for table and column
*names* to reach a remote provider while refusing to let a single customer row leave; another might
allow aggregates but not samples; a third might refuse remote providers entirely. A boolean flag
collapses those into one setting, and the collapse always resolves in the permissive direction,
because the person adding the feature needs it to work.

## Decision

`app/platform/modes.py::EgressPolicy` is an ordered enum, most restrictive first:

`LOCAL_ONLY` → `SCHEMA_ONLY_REMOTE` → `MASKED_METADATA_REMOTE` → `AGGREGATES_REMOTE` →
`REMOTE_ALLOWED`

with `rank` and `permits(required)` so a caller can ask "is the active policy at least as permissive
as what this step needs?". Local providers are never subject to it — nothing leaves the host.

It is enforced at the two places where data would actually leave:

* **Provider selection.** `app/llm/router.py::ModelRouter.candidates` excludes every profile whose
  kind is `REMOTE` when the policy is `LOCAL_ONLY`, recording the rejection reason
  (`"egress policy is LOCAL_ONLY"`) in the `RoutingDecision`.
* **Summary input.** `app/analysis/summary.py::SAMPLE_ROWS_BY_POLICY` maps the policy to how many
  result rows the summarising model may see: `SCHEMA_ONLY_REMOTE` and `AGGREGATES_REMOTE` get zero,
  `MASKED_METADATA_REMOTE` gets five, `LOCAL_ONLY` and `REMOTE_ALLOWED` get twenty.

The default comes from the mode (`AppModePolicy.default_egress_policy`) and can be overridden with
`EGRESS_POLICY` (`Settings.effective_egress_policy`).

## Consequences

**What it buys.** One name for a policy that used to be an unstated assumption, set once per
deployment, visible in the routing decision recorded on every run. `LOCAL_ONLY` is a genuine
air-gap for model traffic rather than a promise.

**What it costs.**

* **A total order cannot express every real policy.** "Aggregates yes, schema names no" is not
  representable, because `AGGREGATES_REMOTE` outranks `SCHEMA_ONLY_REMOTE`. The ordering encodes our
  judgement about what is more sensitive; a deployment that disagrees has no way to say so.
* **The row counts are a judgement, not a measurement.** Twenty rows under `LOCAL_ONLY` and five
  under `MASKED_METADATA_REMOTE` are numbers we chose. Nothing derives them.
* **Enforcement is at two named points only.** A future node that sends anything to a remote
  provider must consult the policy itself; there is no import contract or type that forces it. That
  is the main weakness of this design and the reason the enforcement points are listed explicitly
  above rather than described as "throughout".
* **`MASKED_METADATA_REMOTE` names masking that nothing currently performs.** A deterministic PII
  classifier exists (`app/security/pii.py`, `tests/test_pii.py`) but has no callers:
  `grep -rn "security.pii" app db` finds none outside its own tests. The summary path applies the
  row *budget* from this table; nothing masks those rows. Read the value as "the row budget an
  operator who masks elsewhere would want", and treat wiring the classifier into the result path as
  outstanding work.
