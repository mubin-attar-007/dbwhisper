"""The policy engine: ``PolicyEngine.evaluate(sql, ctx) -> PolicyDecision``.

Stages (each producing typed :class:`RuleResult` entries, all recorded on the decision):

1. length + invisible-character checks; parse exactly one statement in the declared dialect;
2. root statement class must be SELECT / set operation; denied node types anywhere in the tree;
3. blocked functions (file, network, sleep, lock, sequence, config introspection);
4. object allowlist: system catalogs, cross-database refs, unknown tables, unresolvable columns,
   sensitive columns (deny / approval);
5. complexity limits for the policy level;
6. AST row-limit enforcement; optional parameterisation;
7. independent legacy heuristic layer; stricter result wins;
8. fingerprint + normalised SQL.

This engine is a structural filter, not a proof: it is one layer of defence in depth together with a
least-privilege database role, read-only transactions, timeouts and row caps.
"""

from __future__ import annotations

import contextlib

from sqlglot import exp

from app.sqlpolicy import allowlist, complexity, limits, parameters, rules
from app.sqlpolicy.legacy_heuristics import legacy_validate
from app.sqlpolicy.parse import (
    PolicyParseError,
    fingerprint,
    normalized_sql,
    parse_single_statement,
)
from app.sqlpolicy.types import (
    Decision,
    PolicyContext,
    PolicyDecision,
    RuleResult,
)

POLICY_VERSION = f"sql_policy@{rules.RULESET_VERSION}"


class PolicyEngine:
    def evaluate(self, sql: str, ctx: PolicyContext) -> PolicyDecision:
        decision = PolicyDecision(
            decision=Decision.DENY,
            policy_version=POLICY_VERSION,
            dialect=ctx.dialect,
            original_sql=sql or "",
        )
        lim = ctx.effective_limits()

        def deny(rule: RuleResult) -> PolicyDecision:
            decision.rules.append(rule)
            decision.reasons.append(rule.message)
            decision.decision = Decision.DENY
            decision.transformed_sql = None
            decision.parameters = {}
            decision.parameterized = False
            return decision

        # 1. size + parse ---------------------------------------------------------------
        if len(sql or "") > lim.max_sql_length:
            return deny(
                RuleResult(
                    "parse.sql_length",
                    False,
                    "deny",
                    f"SQL statement too long ({len(sql)} > {lim.max_sql_length} chars)",
                )
            )
        try:
            parsed = parse_single_statement(sql, ctx.dialect)
        except PolicyParseError as exc:
            return deny(RuleResult("parse.single_statement", False, "deny", str(exc)))
        root = parsed.expression
        decision.statement_type = parsed.statement_type
        decision.rules.append(
            RuleResult(
                "parse.single_statement", True, "info", f"Parsed one {parsed.statement_type}"
            )
        )

        # 2. statement classes ----------------------------------------------------------
        if not isinstance(root, rules.ALLOWED_ROOT_TYPES):
            return deny(
                RuleResult(
                    "statement.root_type",
                    False,
                    "deny",
                    f"Only SELECT statements are permitted (got {parsed.statement_type})",
                )
            )
        for node in root.walk():
            if isinstance(node, rules.DENIED_NODE_TYPES):
                return deny(
                    RuleResult(
                        "statement.denied_node",
                        False,
                        "deny",
                        rules.denied_node_message(node),
                        {"node": type(node).__name__},
                    )
                )
        decision.rules.append(
            RuleResult("statement.read_only", True, "info", "Read-only statement")
        )

        # 3. functions ------------------------------------------------------------------
        blocked = _blocked_functions(root, ctx)
        if blocked:
            return deny(
                RuleResult(
                    "functions.blocked",
                    False,
                    "deny",
                    "Blocked function(s): " + ", ".join(sorted(blocked)),
                    {"functions": sorted(blocked)},
                )
            )
        decision.rules.append(RuleResult("functions", True, "info", "No blocked functions"))

        # 4. objects --------------------------------------------------------------------
        report = allowlist.collect_objects(
            root, ctx.dialect, ctx.scope, require_scope=ctx.require_scope
        )
        decision.rules.extend(report.rules)
        decision.tables, decision.columns, decision.ctes = (
            report.tables,
            report.columns,
            report.ctes,
        )
        decision.sensitive_columns_touched = report.sensitive
        if report.denied:
            decision.reasons.extend(r.message for r in report.rules if not r.passed)
            return decision
        needs_approval = False
        if report.sensitive:
            names = ", ".join(str(c) for c in report.sensitive)
            if ctx.sensitive_action == "deny":
                return deny(
                    RuleResult(
                        "objects.sensitive_columns",
                        False,
                        "deny",
                        f"Sensitive columns are not permitted: {names}",
                    )
                )
            if ctx.sensitive_action == "approval":
                needs_approval = True
                decision.rules.append(
                    RuleResult(
                        "objects.sensitive_columns",
                        False,
                        "approval",
                        f"Sensitive columns require approval: {names}",
                    )
                )

        # 5. complexity -----------------------------------------------------------------
        metrics = complexity.measure(root, len(parsed.cleaned_sql))
        decision.complexity = metrics
        results = complexity.check(metrics, lim)
        decision.rules.extend(results)
        failed = [r for r in results if not r.passed]
        if failed:
            decision.reasons.extend(r.message for r in failed)
            return decision

        # 6. limit + parameters ---------------------------------------------------------
        transformed = root
        if ctx.inject_limit:
            outcome = limits.apply_row_limit(
                root, ctx.dialect, min(ctx.default_row_limit, lim.max_row_limit)
            )
            transformed = outcome.expression
            decision.existing_limit = outcome.existing_limit
            decision.limit_applied = outcome.applied_limit
            decision.rules.append(RuleResult("limits.row_limit", True, "info", outcome.reason))
        if ctx.parameterize:
            pz = parameters.parameterize(transformed, ctx.dialect)
            decision.transformed_sql, decision.parameters = pz.sql, pz.params
            decision.parameterized = pz.applied
            decision.rules.append(
                RuleResult(
                    "parameters",
                    True,
                    "info",
                    "literals bound as parameters"
                    if pz.applied
                    else "parameterisation not applicable",
                )
            )
        else:
            decision.transformed_sql = transformed.sql(
                dialect=ctx.dialect.sqlglot_name, comments=False
            )

        # 7. legacy layer ---------------------------------------------------------------
        if ctx.run_legacy_layer:
            allowed_tables = None
            if ctx.scope is not None:
                allowed_tables = [t for ts in ctx.scope.tables.values() for t in ts]
            legacy = legacy_validate(parsed.cleaned_sql, allowed_tables)
            decision.legacy_agrees = bool(legacy["valid"])
            if not legacy["valid"]:
                return deny(
                    RuleResult(
                        "legacy.heuristics",
                        False,
                        "deny",
                        f"Heuristic layer disagreed: {legacy['reason']}",
                    )
                )
            decision.rules.append(
                RuleResult("legacy.heuristics", True, "info", "Heuristic layer agrees")
            )

        # 8. identity -------------------------------------------------------------------
        decision.normalized_sql = normalized_sql(root, ctx.dialect)
        decision.fingerprint = fingerprint(root, ctx.dialect)
        decision.decision = Decision.NEEDS_APPROVAL if needs_approval else Decision.ALLOW
        if needs_approval:
            decision.reasons.append("Approval required before execution")
        return decision


def _function_names(node: exp.Expression) -> set[str]:
    names: set[str] = set()
    if isinstance(node, exp.Anonymous):
        names.add(str(node.this).lower())
    elif isinstance(node, exp.Func):
        names.add(type(node).__name__.lower())
        with contextlib.suppress(Exception):
            names.update(n.lower() for n in type(node).sql_names())
    return names


def _blocked_functions(root: exp.Expression, ctx: PolicyContext) -> set[str]:
    blocked = rules.blocked_functions_for(ctx.dialect)
    prefixes = rules.blocked_prefixes_for(ctx.dialect)
    found: set[str] = set()
    for node in root.walk():
        if not isinstance(node, exp.Func):
            continue
        for name in _function_names(node):
            if name in blocked or name.startswith(prefixes):
                found.add(name)
    return found


_DEFAULT_ENGINE = PolicyEngine()


def evaluate(sql: str, ctx: PolicyContext) -> PolicyDecision:
    return _DEFAULT_ENGINE.evaluate(sql, ctx)


__all__ = ["POLICY_VERSION", "PolicyEngine", "evaluate"]
