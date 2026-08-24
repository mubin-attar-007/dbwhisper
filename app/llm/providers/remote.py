"""Optional hosted providers, behind the same contract as the local one.

These wrap the LangChain chat adapters that were already dependencies, for one reason: they are
*optional*. Nothing in DBWhisper requires them, they are only enabled when their API key is present,
and the egress policy can exclude them entirely. Their job here is to translate - vendor exception
in, normalised :class:`~app.llm.types.FailureKind` out - so the router can make sensible decisions
instead of pattern-matching on error strings.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
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

logger = logging.getLogger(__name__)

#: Substrings that identify a failure class across vendors. Checked in order.
_ERROR_SIGNATURES: tuple[tuple[str, FailureKind], ...] = (
    ("rate limit", FailureKind.RATE_LIMIT),
    ("rate_limit", FailureKind.RATE_LIMIT),
    ("429", FailureKind.RATE_LIMIT),
    ("quota", FailureKind.RATE_LIMIT),
    ("resource_exhausted", FailureKind.RATE_LIMIT),
    ("resourceexhausted", FailureKind.RATE_LIMIT),
    ("too many requests", FailureKind.RATE_LIMIT),
    ("overloaded", FailureKind.SERVER_ERROR),
    ("api key", FailureKind.AUTH),
    ("unauthorized", FailureKind.AUTH),
    ("authentication", FailureKind.AUTH),
    ("permission", FailureKind.AUTH),
    ("401", FailureKind.AUTH),
    ("403", FailureKind.AUTH),
    ("timeout", FailureKind.TIMEOUT),
    ("timed out", FailureKind.TIMEOUT),
    ("deadline", FailureKind.TIMEOUT),
    ("connection", FailureKind.CONNECTION),
    ("could not connect", FailureKind.CONNECTION),
    ("safety", FailureKind.CONTENT_FILTER),
    ("content filter", FailureKind.CONTENT_FILTER),
    ("blocked", FailureKind.CONTENT_FILTER),
    ("500", FailureKind.SERVER_ERROR),
    ("502", FailureKind.SERVER_ERROR),
    ("503", FailureKind.SERVER_ERROR),
    ("internal server", FailureKind.SERVER_ERROR),
    ("invalid", FailureKind.BAD_REQUEST),
    ("not found", FailureKind.BAD_REQUEST),
)


def classify_error(exc: BaseException) -> FailureKind:
    """Map a vendor exception onto a failure class the router understands."""
    text = f"{type(exc).__name__} {exc}".lower()
    for signature, kind in _ERROR_SIGNATURES:
        if signature in text:
            return kind
    return FailureKind.UNKNOWN


def _build_chat_model(profile: ModelProfile, temperature: float, max_tokens: int) -> Any:
    """Construct the vendor client lazily, so an unused SDK is never imported."""
    provider = profile.provider
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=profile.model, temperature=temperature, max_output_tokens=max_tokens
        )
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=profile.model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=os.getenv("GROQ_API_KEY"),
            max_retries=0,
        )
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=profile.model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=os.getenv("OPENAI_API_KEY"),
            max_retries=0,
        )
    if provider == "openrouter":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=profile.model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
            max_retries=0,
        )
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=profile.model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )
    if provider == "deepseek":
        from langchain_deepseek import ChatDeepSeek

        return ChatDeepSeek(
            model=profile.model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=os.getenv("DEEPSEEK_API_KEY"),
        )
    raise ProviderError(
        FailureKind.BAD_REQUEST, f"No adapter for remote provider '{provider}'", provider=provider
    )


@dataclass
class RemoteProvider:
    """A hosted chat model. Constructed per profile; clients are cached per configuration."""

    name: str
    builder: Any = _build_chat_model
    _clients: dict[tuple[str, float, int], Any] = field(default_factory=dict, repr=False)

    def _client(self, profile: ModelProfile, temperature: float, max_tokens: int) -> Any:
        key = (profile.name, temperature, max_tokens)
        if key not in self._clients:
            try:
                self._clients[key] = self.builder(profile, temperature, max_tokens)
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(
                    classify_error(exc),
                    f"Could not initialise {profile.provider}: {exc}",
                    provider=profile.provider,
                ) from exc
        return self._clients[key]

    def complete(self, request: ModelRequest, profile: ModelProfile) -> ModelResponse:
        temperature = (
            request.temperature if request.temperature is not None else profile.temperature
        )
        max_tokens = request.max_output_tokens or profile.max_output_tokens
        client = self._client(profile, temperature, max_tokens)

        started = time.perf_counter()
        try:
            result = client.invoke(request.messages())
        except Exception as exc:
            raise ProviderError(
                classify_error(exc),
                f"{profile.provider} call failed: {str(exc)[:200]}",
                provider=profile.provider,
            ) from exc
        latency_ms = (time.perf_counter() - started) * 1000

        return ModelResponse(
            text=_content_text(getattr(result, "content", result)),
            profile=profile.name,
            provider=profile.provider,
            model=_reported_model(result) or profile.model,
            usage=_usage(result),
            latency_ms=latency_ms,
            finish_reason=(getattr(result, "response_metadata", {}) or {}).get("finish_reason"),
        )

    def health(self) -> ProviderHealth:
        """Key presence only. A real probe would cost a request, and this runs on every route."""
        from app.llm.registry import PROVIDER_KEY_ENV

        key = PROVIDER_KEY_ENV.get(self.name)
        present = bool(key and os.getenv(key))
        return ProviderHealth(
            provider=self.name,
            available=present,
            detail="API key present" if present else f"{key or 'API key'} is not set",
        )


def _content_text(content: Any) -> str:
    """Vendors return a string, or a list of content blocks. Normalise both."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(parts)
    return str(content)


def _reported_model(result: Any) -> str | None:
    metadata = getattr(result, "response_metadata", None) or {}
    return metadata.get("model_name") or metadata.get("model")


def _usage(result: Any) -> TokenUsage:
    raw = getattr(result, "usage_metadata", None) or {}
    if raw:
        return TokenUsage(
            input_tokens=int(raw.get("input_tokens") or 0),
            output_tokens=int(raw.get("output_tokens") or 0),
        )
    metadata = (getattr(result, "response_metadata", None) or {}).get("token_usage") or {}
    return TokenUsage(
        input_tokens=int(metadata.get("prompt_tokens") or 0),
        output_tokens=int(metadata.get("completion_tokens") or 0),
    )


__all__ = ["RemoteProvider", "classify_error"]
