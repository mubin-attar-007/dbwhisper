"""Structured output: ask for JSON, verify it, and repair a bounded number of times.

Native schema-constrained decoding is used when the runtime supports it, but never trusted: small
local models routinely emit prose around the object, a trailing comma, or a field of the wrong type.
The contract here is that a caller either receives a payload that validates against the schema, or
an exception - never a half-parsed dictionary that fails three layers later.

Repair is bounded (two attempts by default) and each attempt tells the model exactly what was wrong,
which is far more effective than asking it to "try again".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.llm.types import FailureKind, ModelRequest, ModelResponse, ProviderError

MAX_REPAIR_ATTEMPTS = 2

_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)


class StructuredOutputError(ValueError):
    """The model could not produce a payload matching the schema within the repair budget."""


@dataclass(slots=True)
class ValidationIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


def extract_json(text: str) -> Any:
    """Pull a JSON value out of a model response, tolerating fences and surrounding prose."""
    if not text or not text.strip():
        raise StructuredOutputError("The model returned an empty response.")
    candidate = text.strip()

    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Fall back to the outermost balanced object or array in the text.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            snippet = candidate[start : end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                continue
    raise StructuredOutputError("The model response did not contain valid JSON.")


def validate_against(schema: dict[str, Any], value: Any, path: str = "") -> list[ValidationIssue]:
    """Validate against the subset of JSON Schema the model layer uses.

    Deliberately small: type, required, enum, properties, items, minimum/maximum, minItems/maxItems.
    A full validator would be a dependency for very little gain, and an unsupported keyword silently
    passing is safer here than an import failure at start-up.
    """
    issues: list[ValidationIssue] = []
    expected = schema.get("type")

    if expected and not _type_matches(expected, value):
        issues.append(ValidationIssue(path, f"expected {expected}, got {_type_name(value)}"))
        return issues

    if "enum" in schema and value not in schema["enum"]:
        issues.append(ValidationIssue(path, f"must be one of {schema['enum']}, got {value!r}"))

    if expected == "object" or isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if not isinstance(value, dict) or name not in value:
                issues.append(ValidationIssue(path, f"missing required field '{name}'"))
        if isinstance(value, dict):
            for name, sub_schema in properties.items():
                if name in value:
                    issues.extend(
                        validate_against(sub_schema, value[name], f"{path}.{name}".lstrip("."))
                    )

    if (expected == "array" or isinstance(value, list)) and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issues.extend(validate_against(item_schema, item, f"{path}[{index}]"))
        if "minItems" in schema and len(value) < schema["minItems"]:
            issues.append(ValidationIssue(path, f"needs at least {schema['minItems']} item(s)"))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            issues.append(ValidationIssue(path, f"allows at most {schema['maxItems']} item(s)"))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            issues.append(ValidationIssue(path, f"must be >= {schema['minimum']}"))
        if "maximum" in schema and value > schema["maximum"]:
            issues.append(ValidationIssue(path, f"must be <= {schema['maximum']}"))

    return issues


def _type_matches(expected: str | list[str], value: Any) -> bool:
    if isinstance(expected, list):
        return any(_type_matches(item, value) for item in expected)
    return {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }.get(expected, lambda _v: True)(value)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def repair_prompt(original: str, response_text: str, issues: list[ValidationIssue]) -> str:
    """Tell the model precisely what was wrong. Vague retries mostly reproduce the same error."""
    listed = "\n".join(f"- {issue}" for issue in issues[:8])
    return (
        f"{original}\n\n"
        "Your previous response could not be used. It must be a single JSON object matching the "
        "schema, with no prose and no code fences.\n\n"
        f"Previous response:\n{response_text[:800]}\n\n"
        f"Problems found:\n{listed}\n\n"
        "Return only the corrected JSON."
    )


def complete_structured(
    provider: Any,
    request: ModelRequest,
    profile: Any,
    *,
    max_repairs: int = MAX_REPAIR_ATTEMPTS,
) -> ModelResponse:
    """Call the provider until the payload validates, or give up with a clear error."""
    if not request.json_schema:
        raise ValueError("complete_structured requires request.json_schema")

    attempt_request = request
    last_error = ""
    for attempt in range(max_repairs + 1):
        response = provider.complete(attempt_request, profile)
        try:
            payload = extract_json(response.text)
        except StructuredOutputError as exc:
            issues = [ValidationIssue("", str(exc))]
        else:
            issues = validate_against(request.json_schema, payload)
            if not issues:
                response.parsed = payload
                response.repair_attempts = attempt
                return response

        last_error = "; ".join(str(issue) for issue in issues[:5])
        if attempt == max_repairs:
            break
        attempt_request = ModelRequest(
            system=request.system,
            user=repair_prompt(request.user, response.text, issues),
            capabilities=request.capabilities,
            json_schema=request.json_schema,
            temperature=0.0,  # a repair should be deterministic, not creative
            max_output_tokens=request.max_output_tokens,
            stop=request.stop,
            egress_policy=request.egress_policy,
            prompt_name=f"{request.prompt_name}.repair{attempt + 1}",
            prompt_version=request.prompt_version,
            timeout_seconds=request.timeout_seconds,
        )

    raise ProviderError(
        FailureKind.SCHEMA_VIOLATION,
        f"Model did not return a valid response after {max_repairs} repair attempt(s): {last_error}",
        provider=getattr(provider, "name", "unknown"),
    )


__all__ = [
    "MAX_REPAIR_ATTEMPTS",
    "StructuredOutputError",
    "ValidationIssue",
    "complete_structured",
    "extract_json",
    "repair_prompt",
    "validate_against",
]
