"""A deterministic provider for CI, contract tests and the frontend end-to-end suite.

It is not a mock in the usual sense - it is a real provider that happens to read its answers from
fixtures. That distinction matters: the same code path runs in CI as in production, so a test can
exercise streaming, structured output, repair loops and provider failures without a key, a network
or a GPU, and two runs of the suite produce byte-identical results.

Answers are resolved in this order:

1. an exact fixture for ``(prompt_name, sha256(system + user))``;
2. a fixture registered for ``prompt_name`` alone;
3. a rule registered with :meth:`FakeProvider.add_rule` whose predicate matches the request;
4. a schema-shaped default, so an unseen prompt still returns something valid rather than crashing.

Failures are scripted with :meth:`fail_next`, which is how the router's fallback and circuit-breaker
behaviour gets tested without waiting for a real provider to break.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.llm.types import (
    FailureKind,
    ModelProfile,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderHealth,
    TokenUsage,
)


def fixture_key(request: ModelRequest) -> str:
    payload = f"{request.system or ''}\n---\n{request.user}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class _Rule:
    predicate: Callable[[ModelRequest], bool]
    response: str | Callable[[ModelRequest], str]
    label: str = ""


@dataclass
class FakeProvider:
    """Fixture-backed provider. Construct one per test; register fixtures, then use it."""

    name: str = "fake"
    fixtures: dict[str, str] = field(default_factory=dict)
    latency_ms: float = 0.0
    _rules: list[_Rule] = field(default_factory=list)
    _queued_failures: list[ProviderError] = field(default_factory=list)
    calls: list[ModelRequest] = field(default_factory=list)

    # -- registration --------------------------------------------------------------------------
    def add_fixture(
        self, prompt_name: str, response: str, request: ModelRequest | None = None
    ) -> None:
        key = f"{prompt_name}:{fixture_key(request)}" if request else prompt_name
        self.fixtures[key] = response

    def add_rule(
        self,
        predicate: Callable[[ModelRequest], bool],
        response: str | Callable[[ModelRequest], str],
        label: str = "",
    ) -> None:
        self._rules.append(_Rule(predicate, response, label))

    def fail_next(
        self, kind: FailureKind, message: str = "scripted failure", times: int = 1
    ) -> None:
        for _ in range(times):
            self._queued_failures.append(ProviderError(kind, message, provider=self.name))

    def load_fixture_dir(self, directory: Path) -> int:
        """Load ``*.txt`` / ``*.json`` fixtures named ``<prompt_name>[.<key>].ext``."""
        loaded = 0
        for path in sorted(Path(directory).glob("*")):
            if path.suffix not in {".txt", ".json"} or not path.is_file():
                continue
            self.fixtures[path.stem] = path.read_text(encoding="utf-8").strip()
            loaded += 1
        return loaded

    # -- provider contract ---------------------------------------------------------------------
    def complete(self, request: ModelRequest, profile: ModelProfile) -> ModelResponse:
        self.calls.append(request)
        if self._queued_failures:
            raise self._queued_failures.pop(0)
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000.0)

        text = self._resolve(request)
        return ModelResponse(
            text=text,
            profile=profile.name,
            provider=self.name,
            model=profile.model,
            usage=TokenUsage(
                input_tokens=_rough_tokens(request.user) + _rough_tokens(request.system or ""),
                output_tokens=_rough_tokens(text),
            ),
            latency_ms=self.latency_ms,
            finish_reason="stop",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name, available=True, detail="deterministic fixture provider"
        )

    # -- resolution ----------------------------------------------------------------------------
    def _resolve(self, request: ModelRequest) -> str:
        exact = f"{request.prompt_name}:{fixture_key(request)}"
        if exact in self.fixtures:
            return self.fixtures[exact]
        if request.prompt_name in self.fixtures:
            return self.fixtures[request.prompt_name]
        # Most recently registered wins, so a test can override a fixture's default answer.
        for rule in reversed(self._rules):
            if rule.predicate(request):
                return rule.response(request) if callable(rule.response) else rule.response
        if request.json_schema:
            return json.dumps(schema_shaped_default(request.json_schema))
        return f"[fake:{request.prompt_name}] no fixture registered"


def schema_shaped_default(schema: dict[str, Any]) -> Any:
    """A minimal value that satisfies a JSON schema's required fields."""
    kind = schema.get("type")
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", list(properties))
        return {name: schema_shaped_default(properties.get(name, {})) for name in required}
    if kind == "array":
        return []
    if kind == "integer":
        return 0
    if kind == "number":
        return 0.0
    if kind == "boolean":
        return False
    if schema.get("enum"):
        return schema["enum"][0]
    return ""


def _rough_tokens(text: str) -> int:
    """A crude word-based count. Labelled 'rough' because it is not a tokenizer measurement."""
    return len(text.split()) if text else 0


__all__ = ["FakeProvider", "fixture_key", "schema_shaped_default"]
