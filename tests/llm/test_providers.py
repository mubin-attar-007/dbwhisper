"""Provider adapters: the local HTTP provider, the fake provider, and error normalisation.

The Ollama provider is exercised against an ``httpx`` mock transport, so the wire format, the
structured-output request, token accounting and every failure mapping are covered without a running
model server.
"""

from __future__ import annotations

from typing import ClassVar

import httpx
import pytest

from app.llm.providers.fake import FakeProvider, schema_shaped_default
from app.llm.providers.ollama import OllamaProvider
from app.llm.providers.remote import RemoteProvider, classify_error
from app.llm.registry import FAKE_PROFILE, profile
from app.llm.types import (
    FailureKind,
    ModelProfile,
    ModelRequest,
    ProviderError,
    ProviderKind,
    TokenUsage,
)

LOCAL = profile("local-small")


def _ollama(handler, wire: str = "native") -> OllamaProvider:
    provider = OllamaProvider(base_url="http://local-model:11434", wire=wire)
    provider._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://local-model:11434"
    )
    return provider


def _request(**kwargs) -> ModelRequest:
    params = {"system": "You are a SQL analyst.", "user": "count customers", "prompt_name": "t"}
    params.update(kwargs)
    return ModelRequest(**params)


class TestOllamaNativeWire:
    def test_successful_completion(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["body"] = httpx.Request("POST", "http://x", content=request.content).content
            import json

            captured["json"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "model": "qwen2.5-coder:1.5b",
                    "message": {"role": "assistant", "content": "SELECT COUNT(*) FROM customers"},
                    "done": True,
                    "prompt_eval_count": 31,
                    "eval_count": 9,
                },
            )

        response = _ollama(handler).complete(_request(), LOCAL)
        assert captured["path"] == "/api/chat"
        assert captured["json"]["model"] == "qwen2.5-coder:1.5b"
        assert captured["json"]["stream"] is False
        assert captured["json"]["messages"][0]["role"] == "system"
        assert response.text == "SELECT COUNT(*) FROM customers"
        assert response.usage == TokenUsage(input_tokens=31, output_tokens=9)
        assert response.model == "qwen2.5-coder:1.5b"
        assert response.latency_ms >= 0

    def test_no_api_key_is_ever_sent(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"message": {"content": "ok"}, "done": True})

        _ollama(handler).complete(_request(), LOCAL)
        assert seen["auth"] is None

    def test_json_schema_is_passed_when_the_profile_supports_it(self):
        seen: dict = {}
        schema = {"type": "object", "properties": {"sql": {"type": "string"}}}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen["format"] = json.loads(request.content).get("format")
            return httpx.Response(200, json={"message": {"content": "{}"}, "done": True})

        _ollama(handler).complete(_request(json_schema=schema), LOCAL)
        assert seen["format"] == schema

    def test_json_mode_when_the_profile_lacks_native_schema_support(self):
        seen: dict = {}
        plain = ModelProfile(
            name="p",
            provider="ollama",
            model="m",
            kind=ProviderKind.LOCAL,
            capabilities=LOCAL.capabilities,
            supports_native_json_schema=False,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen["format"] = json.loads(request.content).get("format")
            return httpx.Response(200, json={"message": {"content": "{}"}, "done": True})

        _ollama(handler).complete(_request(json_schema={"type": "object"}), plain)
        assert seen["format"] == "json"

    def test_health_lists_models(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"models": [{"name": "qwen2.5-coder:1.5b"}, {"name": "llama3.2:1b"}]}
            )

        health = _ollama(handler).health()
        assert health.available and "2 model(s)" in health.detail
        assert "llama3.2:1b" in health.models

    def test_has_model_ignores_the_tag(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:1.5b"}]})

        provider = _ollama(handler)
        assert provider.has_model("qwen2.5-coder:1.5b")
        assert provider.has_model("qwen2.5-coder")
        assert not provider.has_model("mistral")


class TestOllamaOpenAIWire:
    def test_successful_completion(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            return httpx.Response(
                200,
                json={
                    "model": "local",
                    "choices": [{"message": {"content": "SELECT 1"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                },
            )

        response = _ollama(handler, wire="openai").complete(_request(), LOCAL)
        assert seen["path"] == "/v1/chat/completions"
        assert response.text == "SELECT 1"
        assert response.usage.total == 15
        assert response.finish_reason == "stop"

    def test_structured_output_uses_response_format(self):
        seen: dict = {}
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen["rf"] = json.loads(request.content).get("response_format")
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

        _ollama(handler, wire="openai").complete(_request(json_schema=schema), LOCAL)
        assert seen["rf"]["type"] == "json_schema"
        assert seen["rf"]["json_schema"]["schema"] == schema

    def test_empty_choices_is_a_server_error(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        with pytest.raises(ProviderError) as exc_info:
            _ollama(handler, wire="openai").complete(_request(), LOCAL)
        assert exc_info.value.kind is FailureKind.SERVER_ERROR


class TestOllamaFailures:
    @pytest.mark.parametrize(
        ("status", "kind"),
        [
            (404, FailureKind.BAD_REQUEST),
            (429, FailureKind.RATE_LIMIT),
            (401, FailureKind.AUTH),
            (500, FailureKind.SERVER_ERROR),
            (400, FailureKind.BAD_REQUEST),
        ],
    )
    def test_status_codes_map_to_failure_kinds(self, status: int, kind: FailureKind):
        provider = _ollama(lambda _r: httpx.Response(status, text="boom"))
        provider.max_retries = 0
        with pytest.raises(ProviderError) as exc_info:
            provider.complete(_request(), LOCAL)
        assert exc_info.value.kind is kind

    def test_missing_model_says_how_to_fix_it(self):
        provider = _ollama(lambda _r: httpx.Response(404, text="model not found"))
        provider.max_retries = 0
        with pytest.raises(ProviderError, match="ollama pull"):
            provider.complete(_request(), LOCAL)

    def test_connection_failure_mentions_the_server(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        provider = _ollama(handler)
        provider.max_retries = 0
        with pytest.raises(ProviderError) as exc_info:
            provider.complete(_request(), LOCAL)
        assert exc_info.value.kind is FailureKind.CONNECTION
        assert "Is Ollama running?" in str(exc_info.value)

    def test_timeout_is_classified(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        provider = _ollama(handler)
        provider.max_retries = 0
        with pytest.raises(ProviderError) as exc_info:
            provider.complete(_request(), LOCAL)
        assert exc_info.value.kind is FailureKind.TIMEOUT

    def test_retryable_failures_are_retried_once(self):
        calls = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503, text="warming up")
            return httpx.Response(200, json={"message": {"content": "ok"}, "done": True})

        assert _ollama(handler).complete(_request(), LOCAL).text == "ok"
        assert calls["n"] == 2

    def test_health_never_raises(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope")

        health = _ollama(handler).health()
        assert health.available is False and "ConnectError" in health.detail


class TestFakeProvider:
    def test_fixture_by_prompt_name(self):
        provider = FakeProvider()
        provider.add_fixture("plan", "fixed answer")
        assert provider.complete(_request(prompt_name="plan"), FAKE_PROFILE).text == "fixed answer"

    def test_fixture_keyed_by_exact_request_wins(self):
        provider = FakeProvider()
        specific = _request(prompt_name="plan", user="exactly this")
        provider.add_fixture("plan", "generic")
        provider.add_fixture("plan", "specific", request=specific)
        assert provider.complete(specific, FAKE_PROFILE).text == "specific"
        assert provider.complete(_request(prompt_name="plan"), FAKE_PROFILE).text == "generic"

    def test_rules_match_on_the_request(self):
        provider = FakeProvider()
        provider.add_rule(lambda r: "revenue" in r.user, "SELECT SUM(amount) FROM orders")
        assert "SUM" in provider.complete(_request(user="total revenue"), FAKE_PROFILE).text

    def test_schema_default_when_nothing_matches(self):
        provider = FakeProvider()
        schema = {
            "type": "object",
            "properties": {"sql": {"type": "string"}, "n": {"type": "integer"}},
            "required": ["sql", "n"],
        }
        import json

        payload = json.loads(provider.complete(_request(json_schema=schema), FAKE_PROFILE).text)
        assert payload == {"sql": "", "n": 0}

    def test_is_deterministic(self):
        provider = FakeProvider()
        provider.add_fixture("t", "same")
        first = provider.complete(_request(), FAKE_PROFILE)
        second = provider.complete(_request(), FAKE_PROFILE)
        assert first.text == second.text and first.usage.total == second.usage.total

    def test_scripted_failures(self):
        provider = FakeProvider()
        provider.fail_next(FailureKind.RATE_LIMIT, times=2)
        for _ in range(2):
            with pytest.raises(ProviderError):
                provider.complete(_request(), FAKE_PROFILE)
        provider.add_fixture("t", "recovered")
        assert provider.complete(_request(), FAKE_PROFILE).text == "recovered"

    def test_records_calls(self):
        provider = FakeProvider()
        provider.complete(_request(user="one"), FAKE_PROFILE)
        provider.complete(_request(user="two"), FAKE_PROFILE)
        assert [c.user for c in provider.calls] == ["one", "two"]

    def test_schema_shaped_default_handles_nesting(self):
        schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"}},
                "kind": {"enum": ["a", "b"]},
                "flag": {"type": "boolean"},
            },
            "required": ["items", "kind", "flag"],
        }
        assert schema_shaped_default(schema) == {"items": [], "kind": "a", "flag": False}


class TestRemoteErrorClassification:
    @pytest.mark.parametrize(
        ("message", "kind"),
        [
            ("429 Too Many Requests", FailureKind.RATE_LIMIT),
            ("ResourceExhausted: quota exceeded", FailureKind.RATE_LIMIT),
            ("Invalid API key provided", FailureKind.AUTH),
            ("Request timed out", FailureKind.TIMEOUT),
            ("Connection error while reaching host", FailureKind.CONNECTION),
            ("The model is overloaded, please retry", FailureKind.SERVER_ERROR),
            ("Response blocked by safety filters", FailureKind.CONTENT_FILTER),
            ("something entirely unexpected", FailureKind.UNKNOWN),
        ],
    )
    def test_signatures(self, message: str, kind: FailureKind):
        assert classify_error(RuntimeError(message)) is kind

    def test_vendor_exception_is_wrapped_not_leaked(self):
        class Client:
            def invoke(self, _messages):
                raise RuntimeError("429 rate limit exceeded for gpt-4o")

        provider = RemoteProvider(name="openai", builder=lambda *_a: Client())
        with pytest.raises(ProviderError) as exc_info:
            provider.complete(_request(), profile("openai-gpt4o"))
        assert exc_info.value.kind is FailureKind.RATE_LIMIT
        assert exc_info.value.provider == "openai"

    def test_content_blocks_are_flattened(self):
        class Result:
            content: ClassVar[list] = [
                {"type": "text", "text": "SELECT 1"},
                {"type": "text", "text": "-- done"},
            ]
            usage_metadata: ClassVar[dict] = {"input_tokens": 5, "output_tokens": 2}
            response_metadata: ClassVar[dict] = {"model_name": "gpt-4o-2024"}

        class Client:
            def invoke(self, _messages):
                return Result()

        provider = RemoteProvider(name="openai", builder=lambda *_a: Client())
        response = provider.complete(_request(), profile("openai-gpt4o"))
        assert response.text == "SELECT 1\n-- done"
        assert response.model == "gpt-4o-2024"
        assert response.usage.total == 7

    def test_health_reports_missing_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        health = RemoteProvider(name="openai").health()
        assert not health.available and "OPENAI_API_KEY" in health.detail
