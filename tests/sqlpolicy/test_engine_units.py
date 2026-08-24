"""Unit tests for the individual stages of the policy engine."""

from __future__ import annotations

from sqlglot import parse_one
from tests.sqlpolicy.conftest import make_ctx

from app.core import sql_validator
from app.sqlpolicy import Decision, Dialect, PolicyContext, PolicyLevel, evaluate
from app.sqlpolicy.legacy_heuristics import legacy_validate
from app.sqlpolicy.limits import (
    apply_pagination,
    apply_row_limit,
    count_wrapper,
    existing_limit,
    is_aggregate_only,
)
from app.sqlpolicy.parameters import parameterize
from app.sqlpolicy.parse import find_invisible_characters, fingerprint


class TestLimits:
    def test_adds_limit_when_missing(self):
        out = apply_row_limit(parse_one("SELECT id FROM customers"), Dialect.POSTGRES, 100)
        assert out.applied_limit == 100 and "LIMIT 100" in out.expression.sql("postgres")

    def test_keeps_stricter_existing_limit(self):
        out = apply_row_limit(parse_one("SELECT id FROM customers LIMIT 5"), Dialect.POSTGRES, 100)
        assert out.applied_limit is None and out.existing_limit == 5
        assert "LIMIT 5" in out.expression.sql("postgres")

    def test_lowers_looser_existing_limit(self):
        out = apply_row_limit(
            parse_one("SELECT id FROM customers LIMIT 9999"), Dialect.POSTGRES, 100
        )
        assert out.applied_limit == 100 and out.existing_limit == 9999
        assert "LIMIT 100" in out.expression.sql("postgres")

    def test_skips_aggregate_only(self):
        root = parse_one("SELECT count(*), max(id) FROM customers")
        assert is_aggregate_only(root)
        out = apply_row_limit(root, Dialect.POSTGRES, 100)
        assert out.applied_limit is None and "LIMIT" not in out.expression.sql("postgres")

    def test_group_by_is_not_aggregate_only(self):
        root = parse_one("SELECT status, count(*) FROM orders GROUP BY status")
        assert not is_aggregate_only(root)
        assert apply_row_limit(root, Dialect.POSTGRES, 50).applied_limit == 50

    def test_mssql_uses_top(self):
        root = parse_one("SELECT name FROM customers ORDER BY id", read="tsql")
        out = apply_row_limit(root, Dialect.MSSQL, 25)
        assert out.expression.sql("tsql").upper().startswith("SELECT TOP 25")

    def test_mssql_existing_top_detected(self):
        root = parse_one("SELECT TOP 3 name FROM customers", read="tsql")
        assert existing_limit(root) == 3
        assert apply_row_limit(root, Dialect.MSSQL, 25).applied_limit is None

    def test_union_limit_and_mssql_wrapper(self):
        root = parse_one("SELECT id FROM customers UNION SELECT id FROM orders")
        assert (
            apply_row_limit(root, Dialect.POSTGRES, 10)
            .expression.sql("postgres")
            .endswith("LIMIT 10")
        )
        wrapped = apply_row_limit(
            parse_one("SELECT id FROM customers UNION SELECT id FROM orders", read="tsql"),
            Dialect.MSSQL,
            10,
        ).expression.sql("tsql")
        assert "TOP 10" in wrapped and "_dbw_limited" in wrapped

    def test_pagination(self):
        root = parse_one("SELECT id FROM customers ORDER BY id")
        sql = apply_pagination(root, Dialect.POSTGRES, page=3, page_size=20).sql("postgres")
        assert sql.endswith("LIMIT 20 OFFSET 40") and "_dbw_page" in sql
        tsql = apply_pagination(
            parse_one("SELECT id FROM customers ORDER BY id", read="tsql"), Dialect.MSSQL, 2, 10
        ).sql("tsql")
        assert "OFFSET 10 ROWS" in tsql and "FETCH" in tsql

    def test_mssql_pagination_requires_order_by(self):
        import pytest

        with pytest.raises(ValueError, match="ORDER BY"):
            apply_pagination(
                parse_one("SELECT id FROM customers", read="tsql"), Dialect.MSSQL, 1, 10
            )

    def test_count_wrapper_strips_limit(self):
        sql = count_wrapper(parse_one("SELECT id FROM customers LIMIT 5")).sql("postgres")
        assert sql.startswith("SELECT COUNT(*) FROM (SELECT id FROM customers) AS _dbw_count")


class TestParameters:
    def test_binds_comparison_literals(self):
        out = parameterize(
            parse_one("SELECT id FROM customers WHERE city = 'Pune' AND id > 5"), Dialect.POSTGRES
        )
        assert out.applied and out.params == {"p0": "Pune", "p1": 5}
        assert out.sql == "SELECT id FROM customers WHERE city = :p0 AND id > :p1"

    def test_in_and_between(self):
        out = parameterize(
            parse_one(
                "SELECT id FROM products WHERE category IN ('a', 'b') AND price BETWEEN 1 AND 9.5"
            ),
            Dialect.MYSQL,
        )
        assert out.params == {"p0": "a", "p1": "b", "p2": 1, "p3": 9.5}
        assert ":p0" in out.sql and ":p3" in out.sql

    def test_leaves_structural_literals_alone(self):
        out = parameterize(
            parse_one("SELECT date_trunc('month', order_date) FROM orders LIMIT 5"),
            Dialect.POSTGRES,
        )
        assert not out.applied and "'MONTH'" in out.sql and "LIMIT 5" in out.sql


class TestParseHelpers:
    def test_invisible_characters_detected_outside_literals(self):
        assert find_invisible_characters("SELECT id") == ["U+00A0"]
        assert find_invisible_characters("SELECT ' ' AS s") == []
        assert find_invisible_characters("SELECT\tid\nFROM t") == []

    def test_fingerprint_ignores_literals_and_case(self):
        a = fingerprint(parse_one("SELECT id FROM customers WHERE city = 'Pune'"), Dialect.POSTGRES)
        b = fingerprint(
            parse_one("select ID from CUSTOMERS where CITY = 'Delhi'"), Dialect.POSTGRES
        )
        c = fingerprint(
            parse_one("SELECT id FROM customers WHERE city = 'Pune' AND id = 1"), Dialect.POSTGRES
        )
        assert a == b and a != c


class TestLegacyLayer:
    def test_fixed_false_positives(self):
        assert legacy_validate("SELECT REPLACE(name,'a','b') FROM customers")["valid"]
        assert legacy_validate("SELECT grant_total FROM customers WHERE city = 'update'")["valid"]
        assert legacy_validate("SELECT 1 FROM customers WHERE notes LIKE '%insert%'")["valid"]

    def test_still_catches_nested_dml_and_multi(self):
        assert not legacy_validate("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x")["valid"]
        assert not legacy_validate("SELECT 1; DROP TABLE t")["valid"]
        assert not legacy_validate("REPLACE INTO t VALUES (1)")["valid"]
        assert not legacy_validate("SELECT * INTO newt FROM t")["valid"]

    def test_allowlist(self):
        assert legacy_validate("SELECT * FROM customers", ["customers"])["valid"]
        assert not legacy_validate("SELECT * FROM secret", ["customers"])["valid"]


class TestEngineDecisions:
    def test_allow_has_identity_and_limit(self, pg_ctx: PolicyContext):
        d = evaluate("SELECT name FROM customers WHERE city = 'Pune'", pg_ctx)
        assert d.decision is Decision.ALLOW and d.valid
        assert d.fingerprint and d.normalized_sql and d.transformed_sql.endswith("LIMIT 1000")
        assert [str(t) for t in d.tables] == ["demo.customers"]
        assert {c.column for c in d.columns} == {"name", "city"}
        assert d.policy_version.startswith("sql_policy@")

    def test_comments_are_stripped_from_execution_sql(self, pg_ctx: PolicyContext):
        d = evaluate("SELECT name FROM customers /* drop table x */", pg_ctx)
        assert d.valid and "drop" not in d.execution_sql.lower()

    def test_deny_has_no_execution_sql(self, pg_ctx: PolicyContext):
        d = evaluate("DELETE FROM customers", pg_ctx)
        assert d.decision is Decision.DENY and d.transformed_sql is None
        assert "delete" in d.reason.lower()

    def test_sensitive_column_actions(self):
        for action, expected in (
            ("approval", Decision.NEEDS_APPROVAL),
            ("deny", Decision.DENY),
            ("allow", Decision.ALLOW),
        ):
            d = evaluate("SELECT ssn FROM patients", make_ctx(sensitive_action=action))
            assert d.decision is expected, action
            assert [str(c) for c in d.sensitive_columns_touched] == ["demo.patients.ssn"]

    def test_missing_scope_fails_closed_when_required(self):
        d = evaluate(
            "SELECT 1", PolicyContext(dialect=Dialect.POSTGRES, scope=None, require_scope=True)
        )
        assert d.decision is Decision.DENY and "enroll" in d.reason.lower()

    def test_missing_scope_allowed_for_statement_only_checks(self):
        ctx = PolicyContext(dialect=Dialect.POSTGRES, scope=None, require_scope=False)
        assert evaluate("SELECT 1", ctx).valid
        assert not evaluate("SELECT * FROM pg_shadow", ctx).valid
        assert not evaluate("DROP TABLE x", ctx).valid

    def test_policy_levels_change_limits(self):
        recursive = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 5) SELECT count(*) FROM r"
        assert not evaluate(recursive, make_ctx(level=PolicyLevel.STANDARD)).valid
        assert evaluate(recursive, make_ctx(level=PolicyLevel.ADVANCED)).valid

    def test_parameterization_can_be_enabled(self):
        d = evaluate("SELECT id FROM customers WHERE city = 'Pune'", make_ctx(parameterize=True))
        assert d.valid and d.parameterized and d.parameters == {"p0": "Pune"}
        assert ":p0" in d.execution_sql

    def test_dialect_mapping(self):
        assert Dialect.from_db_type("postgresql+psycopg") is Dialect.POSTGRES
        assert Dialect.from_db_type("SQL Server") is Dialect.MSSQL
        assert Dialect.from_db_type("mariadb") is Dialect.MYSQL
        assert Dialect.from_db_type(None) is Dialect.GENERIC


class TestV1Wrapper:
    def test_validate_sql_shape(self):
        result = sql_validator.validate_sql("SELECT * FROM customers", db_flag="demo")
        assert result["valid"] is True and result["decision"] == "allow"
        assert result["tables"] == ["demo.customers"] and result["fingerprint"]

    def test_validate_sql_unknown_flag_fails_closed(self):
        result = sql_validator.validate_sql("SELECT 1", db_flag="no_such_db")
        assert result["valid"] is False and "enroll" in result["reason"].lower()

    def test_validate_sql_traversal_flag_rejected(self):
        result = sql_validator.validate_sql("SELECT 1", db_flag="../demo")
        assert result["valid"] is False
