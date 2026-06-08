"""Tests for Phase-1a hardening: config properties, SQL allowlist, env log level."""

from __future__ import annotations

import logging

from app.core import sql_validator
from app.core.config import Settings


# ─── Settings properties ───────────────────────────────────────────────────────
class TestSettingsProps:
    def test_auth_tokens_list(self):
        assert Settings(api_auth_tokens="a, b ,c").auth_tokens_list == ["a", "b", "c"]
        assert Settings(api_auth_tokens="").auth_tokens_list == []
        assert Settings(api_auth_tokens=None).auth_tokens_list == []

    def test_effective_auth_required(self):
        assert Settings(auth_required=True).effective_auth_required is True
        assert Settings(app_env="production").effective_auth_required is True
        assert Settings(app_env="development").effective_auth_required is False

    def test_effective_log_json(self):
        assert Settings(app_env="production").effective_log_json is True
        assert Settings(app_env="development").effective_log_json is False
        assert Settings(app_env="development", log_json=True).effective_log_json is True
        assert Settings(app_env="production", log_json=False).effective_log_json is False


# ─── SQL table allowlist (uses the committed database_schemas/demo index) ───────
def _valid(sql: str, db_flag: str | None = None) -> bool:
    return bool(sql_validator.validate_sql(sql, db_flag=db_flag)["valid"])


class TestSqlAllowlist:
    def test_known_table_allowed(self):
        assert _valid("SELECT * FROM customers", db_flag="demo")

    def test_schema_qualified_allowed(self):
        assert _valid("SELECT * FROM demo.orders", db_flag="demo")

    def test_join_of_known_tables_allowed(self):
        assert _valid(
            "SELECT * FROM customers c JOIN orders o ON c.id = o.customer_id", db_flag="demo"
        )

    def test_cte_name_allowed(self):
        assert _valid(
            "WITH top AS (SELECT customer_id FROM orders) SELECT * FROM top", db_flag="demo"
        )

    def test_unknown_table_rejected(self):
        result = sql_validator.validate_sql("SELECT * FROM secret_table", db_flag="demo")
        assert result["valid"] is False
        assert "secret_table" in result["reason"]

    def test_unknown_dbflag_fails_open(self):
        # No schema index on disk for this flag → allowlist is skipped (fail-open).
        assert _valid("SELECT * FROM anything_at_all", db_flag="no_such_db")


# ─── Env-configurable log level ────────────────────────────────────────────────
def test_log_level_from_env(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    from app.utils.logger import setup_logging

    lg = setup_logging("test_level_logger_unique")
    assert lg.level == logging.WARNING
