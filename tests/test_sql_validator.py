"""Security-critical tests for read-only SQL validation."""

from __future__ import annotations

from app.core import sql_validator


def _valid(sql: str, db_flag: str | None = None) -> bool:
    return bool(sql_validator.validate_sql(sql, db_flag=db_flag)["valid"])


class TestValidReadOnly:
    def test_simple_select(self):
        assert _valid("SELECT * FROM users")

    def test_select_with_where(self):
        assert _valid("SELECT id, name FROM users WHERE active = 1")

    def test_cte_with(self):
        assert _valid("WITH t AS (SELECT 1 AS x) SELECT x FROM t")

    def test_trailing_semicolon(self):
        assert _valid("SELECT 1;")

    def test_join(self):
        assert _valid("SELECT a.id FROM a JOIN b ON a.id = b.a_id")


class TestForbiddenStatements:
    def test_insert(self):
        assert not _valid("INSERT INTO users (id) VALUES (1)")

    def test_update(self):
        assert not _valid("UPDATE users SET name = 'x'")

    def test_delete(self):
        assert not _valid("DELETE FROM users")

    def test_drop(self):
        assert not _valid("DROP TABLE users")

    def test_alter(self):
        assert not _valid("ALTER TABLE users ADD COLUMN x INT")

    def test_truncate(self):
        assert not _valid("TRUNCATE TABLE users")

    def test_create(self):
        assert not _valid("CREATE TABLE x (id INT)")

    def test_grant(self):
        assert not _valid("GRANT SELECT ON users TO bob")

    def test_exec(self):
        assert not _valid("EXEC sp_who")


class TestInjectionAndEdgeCases:
    def test_stacked_statements(self):
        assert not _valid("SELECT 1; DROP TABLE users")

    def test_select_into(self):
        assert not _valid("SELECT * INTO new_table FROM users")

    def test_empty(self):
        assert not _valid("   ")

    def test_non_select_start(self):
        # EXPLAIN is read-only but the validator is conservative: must start SELECT/WITH.
        assert not _valid("EXPLAIN SELECT * FROM users")

    def test_too_long(self):
        assert not _valid("SELECT " + ("a," * 3000) + "1")

    def test_information_schema_blocked(self):
        assert not _valid("SELECT * FROM information_schema.tables")

    def test_pg_catalog_blocked(self):
        assert not _valid("SELECT * FROM pg_catalog.pg_tables")

    def test_sys_schema_blocked(self):
        assert not _valid("SELECT * FROM sys.objects")

    def test_reason_is_reported(self):
        result = sql_validator.validate_sql("DELETE FROM x")
        assert result["valid"] is False
        assert isinstance(result["reason"], str) and result["reason"]
