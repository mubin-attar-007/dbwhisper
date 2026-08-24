"""AST-derived complexity metrics and the limits that apply at each policy level."""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from app.sqlpolicy.types import ComplexityLimits, RuleResult


def _subquery_depth(node: exp.Expression, depth: int = 0) -> int:
    best = depth
    for child in node.iter_expressions():
        child_depth = depth + 1 if isinstance(child, (exp.Select, exp.SetOperation)) else depth
        best = max(best, _subquery_depth(child, child_depth))
    return best


def _trivially_true(cond: exp.Expression) -> bool:
    node = cond.unnest() if isinstance(cond, exp.Paren) else cond
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if (
        isinstance(node, exp.EQ)
        and isinstance(node.this, exp.Literal)
        and isinstance(node.expression, exp.Literal)
    ):
        return node.this.this == node.expression.this
    return False


def _is_cartesian(join: exp.Join) -> bool:
    on = join.args.get("on")
    if on is not None:
        return _trivially_true(on)
    if join.args.get("using"):
        return False
    # LATERAL / CROSS APPLY correlate to the outer row (sqlglot models both as a Lateral source),
    # and NATURAL joins carry implicit predicates. Comma joins and plain CROSS JOIN do not.
    if isinstance(join.this, exp.Lateral) or join.args.get("lateral"):
        return False
    return not (join.kind == "NATURAL" or join.method == "NATURAL")


def _selected_columns(root: exp.Expression) -> int:
    if isinstance(root, exp.Select):
        return len(root.expressions)
    if isinstance(root, exp.SetOperation):
        return _selected_columns(root.this)
    return 0


def _root_limit_value(root: exp.Expression) -> int | None:
    limit = root.args.get("limit")
    if limit is None:
        return None
    literal = limit.find(exp.Literal)
    if literal is None or not literal.is_int:
        return None
    try:
        return int(literal.this)
    except ValueError:  # pragma: no cover
        return None


def measure(root: exp.Expression, sql_length: int) -> dict[str, Any]:
    joins = list(root.find_all(exp.Join))
    with_nodes = list(root.find_all(exp.With))
    return {
        "sql_length": sql_length,
        "joins": len(joins),
        "cartesian_joins": sum(1 for j in joins if _is_cartesian(j)),
        "subquery_depth": _subquery_depth(root),
        "cte_count": sum(len(w.expressions) for w in with_nodes),
        "recursive_cte": any(bool(w.args.get("recursive")) for w in with_nodes),
        "set_operations": sum(1 for _ in root.find_all(exp.SetOperation)),
        "selected_columns": _selected_columns(root),
        "window_functions": sum(1 for _ in root.find_all(exp.Window)),
        "aggregate_functions": sum(1 for _ in root.find_all(exp.AggFunc)),
        "row_limit": _root_limit_value(root),
    }


def check(metrics: dict[str, Any], limits: ComplexityLimits) -> list[RuleResult]:
    results: list[RuleResult] = []

    def fail(rule: str, message: str, **details: Any) -> None:
        results.append(RuleResult(f"complexity.{rule}", False, "deny", message, details))

    if metrics["sql_length"] > limits.max_sql_length:
        fail(
            "sql_length",
            f"SQL statement too long ({metrics['sql_length']} > {limits.max_sql_length} chars)",
        )
    if metrics["joins"] > limits.max_joins:
        fail("joins", f"Too many joins ({metrics['joins']} > {limits.max_joins})")
    if metrics["subquery_depth"] > limits.max_subquery_depth:
        fail(
            "subquery_depth",
            f"Subqueries nested too deeply ({metrics['subquery_depth']} > {limits.max_subquery_depth})",
        )
    if metrics["cte_count"] > limits.max_cte_count:
        fail("cte_count", f"Too many CTEs ({metrics['cte_count']} > {limits.max_cte_count})")
    if metrics["set_operations"] > limits.max_set_operations:
        fail(
            "set_operations",
            f"Too many UNION/INTERSECT/EXCEPT branches ({metrics['set_operations']} > {limits.max_set_operations})",
        )
    if metrics["selected_columns"] > limits.max_selected_columns:
        fail(
            "selected_columns",
            f"Too many selected columns ({metrics['selected_columns']} > {limits.max_selected_columns})",
        )
    if metrics["recursive_cte"] and not limits.allow_recursive_cte:
        fail("recursive_cte", "Recursive CTEs are not permitted at this policy level")
    if metrics["cartesian_joins"] and not limits.allow_cartesian:
        fail(
            "cartesian",
            f"Cartesian (unconstrained) joins are not permitted ({metrics['cartesian_joins']} found); add an ON clause",
        )
    if metrics["window_functions"] and not limits.allow_window_functions:
        fail("window_functions", "Window functions are not permitted at this policy level")
    if metrics["row_limit"] is not None and metrics["row_limit"] > limits.max_row_limit:
        results.append(
            RuleResult(
                "complexity.row_limit",
                True,
                "info",
                f"Requested LIMIT {metrics['row_limit']} exceeds the policy maximum {limits.max_row_limit}; it will be capped",
                {"requested": metrics["row_limit"], "cap": limits.max_row_limit},
            )
        )
    if not results:
        results.append(
            RuleResult("complexity", True, "info", "Within complexity limits", dict(metrics))
        )
    return results


__all__ = ["check", "measure"]
