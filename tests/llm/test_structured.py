"""Structured output: extraction, validation and the bounded repair loop."""

from __future__ import annotations

import pytest

from app.llm.providers.fake import FakeProvider
from app.llm.registry import FAKE_PROFILE
from app.llm.structured import (
    StructuredOutputError,
    complete_structured,
    extract_json,
    validate_against,
)
from app.llm.types import FailureKind, ModelRequest, ProviderError

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string"},
        "tables": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "intent": {"type": "string", "enum": ["lookup", "aggregation", "trend"]},
    },
    "required": ["sql", "tables", "intent"],
}


class TestExtractJson:
    def test_plain_object(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_surrounded_by_prose(self):
        text = 'Sure! Here is the JSON you asked for:\n{"a": 1, "b": [2, 3]}\nHope that helps.'
        assert extract_json(text) == {"a": 1, "b": [2, 3]}

    def test_array_at_top_level(self):
        assert extract_json("Result: [1, 2, 3]") == [1, 2, 3]

    def test_empty_and_garbage_raise(self):
        for text in ("", "   ", "no json here at all"):
            with pytest.raises(StructuredOutputError):
                extract_json(text)


class TestValidate:
    def test_valid_payload(self):
        payload = {"sql": "SELECT 1", "tables": ["customers"], "intent": "lookup"}
        assert validate_against(PLAN_SCHEMA, payload) == []

    def test_missing_required_field(self):
        issues = validate_against(PLAN_SCHEMA, {"sql": "SELECT 1", "tables": []})
        assert any("missing required field 'intent'" in str(i) for i in issues)

    def test_wrong_type(self):
        issues = validate_against(PLAN_SCHEMA, {"sql": 5, "tables": [], "intent": "lookup"})
        assert any("expected string, got integer" in str(i) for i in issues)

    def test_enum_violation(self):
        issues = validate_against(
            PLAN_SCHEMA, {"sql": "x", "tables": [], "intent": "interpretive-dance"}
        )
        assert any("must be one of" in str(i) for i in issues)

    def test_numeric_bounds(self):
        issues = validate_against(
            PLAN_SCHEMA, {"sql": "x", "tables": [], "intent": "lookup", "confidence": 4}
        )
        assert any("must be <= 1" in str(i) for i in issues)

    def test_nested_item_types(self):
        issues = validate_against(
            PLAN_SCHEMA, {"sql": "x", "tables": ["ok", 7], "intent": "lookup"}
        )
        assert any("tables[1]" in str(i) for i in issues)

    def test_booleans_are_not_integers(self):
        assert validate_against({"type": "integer"}, True) != []
        assert validate_against({"type": "boolean"}, True) == []

    def test_min_max_items(self):
        schema = {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3}
        assert validate_against(schema, ["a"]) != []
        assert validate_against(schema, ["a", "b"]) == []
        assert validate_against(schema, ["a", "b", "c", "d"]) != []


class TestRepairLoop:
    def _request(self) -> ModelRequest:
        return ModelRequest(
            system=None,
            user="Write a plan",
            json_schema=PLAN_SCHEMA,
            prompt_name="plan",
        )

    def test_first_attempt_succeeds(self):
        provider = FakeProvider()
        provider.add_fixture("plan", '{"sql": "SELECT 1", "tables": ["t"], "intent": "lookup"}')
        response = complete_structured(provider, self._request(), FAKE_PROFILE)
        assert response.parsed["sql"] == "SELECT 1"
        assert response.repair_attempts == 0
        assert len(provider.calls) == 1

    def test_repairs_an_invalid_payload(self):
        provider = FakeProvider()
        answers = iter(
            [
                "here you go: {'sql': 'SELECT 1'}",  # not JSON, missing fields
                '{"sql": "SELECT 1", "tables": [], "intent": "nope"}',  # bad enum
                '{"sql": "SELECT 1", "tables": ["t"], "intent": "lookup"}',
            ]
        )
        provider.add_rule(lambda _r: True, lambda _r: next(answers))
        response = complete_structured(provider, self._request(), FAKE_PROFILE)
        assert response.parsed["intent"] == "lookup"
        assert response.repair_attempts == 2
        assert len(provider.calls) == 3

    def test_repair_prompt_names_the_problem(self):
        provider = FakeProvider()
        provider.add_rule(lambda _r: True, '{"sql": "SELECT 1", "tables": []}')
        with pytest.raises(ProviderError):
            complete_structured(provider, self._request(), FAKE_PROFILE)
        repair = provider.calls[1].user
        assert "missing required field 'intent'" in repair
        assert "corrected JSON" in repair

    def test_gives_up_after_the_budget(self):
        provider = FakeProvider()
        provider.add_rule(lambda _r: True, "never valid")
        with pytest.raises(ProviderError) as exc_info:
            complete_structured(provider, self._request(), FAKE_PROFILE, max_repairs=2)
        assert exc_info.value.kind is FailureKind.SCHEMA_VIOLATION
        assert len(provider.calls) == 3  # 1 attempt + 2 repairs

    def test_repairs_use_temperature_zero(self):
        provider = FakeProvider()
        answers = iter(["bad", '{"sql": "x", "tables": [], "intent": "lookup"}'])
        provider.add_rule(lambda _r: True, lambda _r: next(answers))
        complete_structured(provider, self._request(), FAKE_PROFILE)
        assert provider.calls[1].temperature == 0.0

    def test_requires_a_schema(self):
        with pytest.raises(ValueError, match="json_schema"):
            complete_structured(FakeProvider(), ModelRequest(system=None, user="x"), FAKE_PROFILE)
