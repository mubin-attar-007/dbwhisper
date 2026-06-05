"""Tests for the SQL-output sanitizers in app.main."""

from __future__ import annotations

from app.main import _extract_agent_output, _sanitize_sql


class TestSanitizeSql:
    def test_strips_code_fence(self):
        assert _sanitize_sql("```sql\nSELECT 1\n```") == "SELECT 1"

    def test_strips_plain_fence(self):
        assert _sanitize_sql("```\nSELECT 1\n```") == "SELECT 1"

    def test_strips_sql_prefix(self):
        assert _sanitize_sql("sql: SELECT 1") == "SELECT 1"

    def test_plain_select_unchanged(self):
        assert _sanitize_sql("SELECT * FROM t") == "SELECT * FROM t"

    def test_empty(self):
        assert _sanitize_sql("") == ""


class TestExtractAgentOutput:
    def test_from_messages(self):
        class _Msg:
            content = "hello world"

        assert _extract_agent_output({"messages": [_Msg()]}) == "hello world"

    def test_from_output_key(self):
        assert _extract_agent_output({"output": "answer"}) == "answer"

    def test_from_plain_string(self):
        assert _extract_agent_output("just text") == "just text"

    def test_from_list_of_segments(self):
        out = _extract_agent_output({"messages": [{"content": [{"text": "a"}, {"text": "b"}]}]})
        assert "a" in out and "b" in out
