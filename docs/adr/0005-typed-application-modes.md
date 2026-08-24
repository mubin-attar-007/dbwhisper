# ADR 0005 — Three application modes resolving to one immutable policy object

**Status:** Accepted — 2026-08-21, Phase 1.
**Principle:** `docs/v2/TARGET_ARCHITECTURE.md` §5.

## Context

Behaviour that differs between a public demo, somebody's laptop and a multi-tenant deployment was
expressed in v1 as `APP_ENV` checks scattered across modules: whether auth is required, whether the
session cookie is `Secure`, whether logs are JSON, whether CORS may be `*`. Two problems follow.
Nobody can answer "what does the demo permit?" without grepping, and the axes are conflated —
`APP_ENV` is about how the process is deployed (logging, cookies), while the interesting questions
are about what the *product* may do (accept an arbitrary connection string, store a secret, reset
itself).

## Decision

`APP_MODE ∈ {demo, self_hosted, production}` resolves to exactly one frozen policy object.

* `app/platform/modes.py::AppModePolicy` is a `@dataclass(frozen=True, slots=True)` with 18 fields
  — `auth_required`, `allow_arbitrary_connections`, `network_policy`, `refuse_writable_connections`,
  `secret_encryption_required`, `default_egress_policy`, `demo_reset_enabled`,
  `persist_sensitive_run_data`, and so on.
* `_POLICIES` holds one instance per mode, written out in full rather than derived, so the three
  columns can be read side by side and diffed.
* `resolve_mode(app_mode, app_env)` prefers an explicit `APP_MODE`, maps a legacy
  `APP_ENV=production` to production mode with a logged deprecation, and otherwise defaults to
  `self_hosted` — the safe default for someone who just cloned the repository.
* Callers ask the object, not the mode: `settings.mode_policy.secret_encryption_required`,
  `settings.mode_policy.allow_remote_providers`, `settings.effective_egress_policy`.

## Consequences

**What it buys.** One file answers what each mode permits, and `tests/test_modes.py` asserts every
field for every mode, so a change to demo behaviour cannot be made silently. Adding a policy field
forces the question "what does this mean in the other two modes?" at the point the field is added,
rather than the first time someone deploys.

**What it costs.**

* **Three fixed points, not a matrix.** A deployment that wants production authentication with
  self-hosted network rules cannot express that by combining modes; it needs an explicit override
  field (as `EGRESS_POLICY` and `DBW_CSRF_ENFORCED` already are) or a new mode. This is deliberate —
  a free-form matrix is how a misconfiguration becomes reachable — but it is a real constraint on
  operators.
* **Adding a field means editing three constants.** Friction is the feature; it is still friction.
* **The separation is not complete, and pretending otherwise would be the failure mode this ADR
  exists to prevent.** `Settings.is_production` still reads `APP_ENV`, and it is what decides
  `effective_auth_required`, `effective_log_json`, part of `cookie_secure`, and the wildcard-CORS
  refusal in `app/main.py`. `app/security/csrf.py::policy_for_settings` branches on the `AppMode`
  enum directly rather than reading a policy field, because its rationale strings differ per mode.
  Both are known exceptions to "no scattered `if mode ==` checks", not oversights, and they are the
  first candidates if this decision is revisited.
* **Nothing structurally prevents a new module from reading `settings.mode`** instead of the policy
  object. The contract is a convention with tests behind it, not an import rule.
