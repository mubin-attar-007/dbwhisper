"""Ollama (and any OpenAI-compatible local server) over plain HTTP.

This is the provider that makes "runs with no paid API" true, so it is deliberately dependency-free
beyond ``httpx``: no vendor SDK, no LangChain, nothing that could pull a cloud client into the
default install. Two wire formats are supported because local runtimes disagree:

* ``native`` - Ollama's ``/api/chat``, which accepts a JSON schema in ``format`` on recent versions;
* ``openai`` - ``/v1/chat/completions``, which llama.cpp, vLLM, LM Studio and Ollama all speak.

Structured output is requested when the server supports it and *verified* afterwards regardless:
:mod:`app.llm.structured` validates the payload and runs a bounded repair loop, so a model that
ignores the schema degrades into a retry rather than a corrupt plan.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.llm.types import (
    FailureKind,
    ModelProfile,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderHealth,
    TokenUsage,
)

WireFormat = Literal["native", "openai"]

DEFAULT_BASE_URL = "http://127.0.0.1:11434"


@dataclass
class OllamaProvider:
    """Talks to a local inference server. No API key is required or sent."""

    base_url: str = DEFAULT_BASE_URL
    wire: WireFormat = "native"
    name: str = "ollama"
    api_key: str | None = None  # some OpenAI-compatible servers want a placeholder
    timeout_seconds: float = 120.0
    max_retries: int = 1
    _client: httpx.Client | None = field(default=None, repr=False)

    # -- transport -----------------------------------------------------------------------------
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.Client(
                base_url=self.base_url.rstrip("/"),
                timeout=httpx.Timeout(self.timeout_seconds),
                headers=headers,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # -- provider contract ---------------------------------------------------------------------
    def complete(self, request: ModelRequest, profile: ModelProfile) -> ModelResponse:
        payload = self._payload(request, profile)
        path = "/api/chat" if self.wire == "native" else "/v1/chat/completions"
        started = time.perf_counter()
        data = self._post(path, payload, request.timeout_seconds or self.timeout_seconds)
        latency_ms = (time.perf_counter() - started) * 1000
        text, model, usage, finish = (
            self._read_native(data) if self.wire == "native" else self._read_openai(data)
        )
        return ModelResponse(
            text=text,
            profile=profile.name,
            provider=self.name,
            model=model or profile.model,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=finish,
        )

    def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            path = "/api/tags" if self.wire == "native" else "/v1/models"
            response = self.client.get(path, timeout=5.0)
            response.raise_for_status()
            body = response.json()
            if self.wire == "native":
                models = [m.get("name", "") for m in body.get("models", [])]
            else:
                models = [m.get("id", "") for m in body.get("data", [])]
            return ProviderHealth(
                provider=self.name,
                available=True,
                detail=f"{len(models)} model(s) available",
                latency_ms=(time.perf_counter() - started) * 1000,
                models=[m for m in models if m],
            )
        except Exception as exc:
            return ProviderHealth(
                provider=self.name,
                available=False,
                detail=f"{type(exc).__name__}: {str(exc)[:120]}",
                latency_ms=(time.perf_counter() - started) * 1000,
            )

    def has_model(self, model: str) -> bool:
        """Whether the server has the model pulled (prefix match ignores the ``:latest`` tag)."""
        available = self.health().models
        return any(m == model or m.split(":")[0] == model.split(":")[0] for m in available)

    # -- payloads ------------------------------------------------------------------------------
    def _payload(self, request: ModelRequest, profile: ModelProfile) -> dict[str, Any]:
        temperature = (
            request.temperature if request.temperature is not None else profile.temperature
        )
        max_tokens = request.max_output_tokens or profile.max_output_tokens
        if self.wire == "native":
            options: dict[str, Any] = {"temperature": temperature, "num_predict": max_tokens}
            if request.stop:
                options["stop"] = request.stop
            payload: dict[str, Any] = {
                "model": profile.model,
                "messages": request.messages(),
                "stream": False,
                "options": options,
            }
            if request.json_schema and profile.supports_native_json_schema:
                payload["format"] = request.json_schema
            elif request.json_schema:
                payload["format"] = "json"
            return payload

        payload = {
            "model": profile.model,
            "messages": request.messages(),
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if request.stop:
            payload["stop"] = request.stop
        if request.json_schema and profile.supports_native_json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": request.json_schema},
            }
        elif request.json_schema:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _post(self, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        last: ProviderError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.post(path, json=payload, timeout=timeout)
            except httpx.TimeoutException:
                last = ProviderError(
                    FailureKind.TIMEOUT,
                    f"Local model timed out after {timeout}s",
                    provider=self.name,
                )
            except httpx.HTTPError as exc:
                last = ProviderError(
                    FailureKind.CONNECTION,
                    f"Could not reach the local model server at {self.base_url} ({type(exc).__name__}). "
                    "Is Ollama running?",
                    provider=self.name,
                )
            else:
                if response.status_code >= 400:
                    last = self._error_for(response)
                else:
                    try:
                        return response.json()
                    except json.JSONDecodeError as exc:
                        last = ProviderError(
                            FailureKind.SERVER_ERROR,
                            f"Local model returned invalid JSON: {exc}",
                            provider=self.name,
                        )
            if not last.kind.is_retryable or attempt == self.max_retries:
                raise last
        raise last  # pragma: no cover - loop always raises

    def _error_for(self, response: httpx.Response) -> ProviderError:
        body = response.text[:200]
        status = response.status_code
        if status == 404:
            return ProviderError(
                FailureKind.BAD_REQUEST,
                f"Model not found on the local server. Pull it first (ollama pull ...). {body}",
                provider=self.name,
            )
        if status == 429:
            return ProviderError(
                FailureKind.RATE_LIMIT, f"Local server busy: {body}", provider=self.name
            )
        if status in (401, 403):
            return ProviderError(
                FailureKind.AUTH, f"Local server rejected the request: {body}", provider=self.name
            )
        if status >= 500:
            return ProviderError(
                FailureKind.SERVER_ERROR, f"Local server error {status}: {body}", provider=self.name
            )
        return ProviderError(
            FailureKind.BAD_REQUEST, f"Local server said {status}: {body}", provider=self.name
        )

    # -- responses -----------------------------------------------------------------------------
    @staticmethod
    def _read_native(data: dict[str, Any]) -> tuple[str, str, TokenUsage, str | None]:
        message = data.get("message") or {}
        usage = TokenUsage(
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
        )
        finish = "stop" if data.get("done") else data.get("done_reason")
        return str(message.get("content") or ""), str(data.get("model") or ""), usage, finish

    @staticmethod
    def _read_openai(data: dict[str, Any]) -> tuple[str, str, TokenUsage, str | None]:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(FailureKind.SERVER_ERROR, "Local server returned no choices")
        choice = choices[0]
        content = (choice.get("message") or {}).get("content") or ""
        raw_usage = data.get("usage") or {}
        usage = TokenUsage(
            input_tokens=int(raw_usage.get("prompt_tokens") or 0),
            output_tokens=int(raw_usage.get("completion_tokens") or 0),
        )
        return str(content), str(data.get("model") or ""), usage, choice.get("finish_reason")


__all__ = ["DEFAULT_BASE_URL", "OllamaProvider", "WireFormat"]
