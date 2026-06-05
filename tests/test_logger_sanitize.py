"""Tests that sanitize_for_log scrubs secrets and PII from log strings."""

from __future__ import annotations

from app.utils.logger import sanitize_for_log


def test_none_returns_literal():
    assert sanitize_for_log(None) == "None"


def test_masks_password_in_uri():
    out = sanitize_for_log("postgresql://user:secretpass@host:5432/db")
    assert "secretpass" not in out


def test_masks_password_kv():
    out = sanitize_for_log("PWD=supersecret;UID=admin")
    assert "supersecret" not in out
    assert "admin" not in out


def test_masks_api_key():
    out = sanitize_for_log("api_key=abcdef123456")
    assert "abcdef123456" not in out


def test_masks_sql_string_literal():
    out = sanitize_for_log("SELECT * FROM t WHERE name='Alice'")
    assert "Alice" not in out
    assert "REDACTED" in out


def test_truncates_long_values():
    out = sanitize_for_log("x" * 200, max_len=10)
    assert out.endswith("...")
    assert len(out) <= 13


def test_handles_dict():
    out = sanitize_for_log({"password": "hunter2", "k": 1})
    assert "hunter2" not in out
