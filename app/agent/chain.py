"""LangChain-based SQL agent construction utilities."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, ModelResponse, wrap_model_call
from langchain.agents.structured_output import ToolStrategy
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_deepseek import ChatDeepSeek
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langsmith import traceable
from pydantic import BaseModel, Field, ValidationError

from app.agent.prompt import RESULT_SUMMARY_PROMPT, SQL_AGENT_PROMPT

load_dotenv()
from langgraph.checkpoint.base import LATEST_VERSION
from langgraph.checkpoint.base.id import uuid6

from app.agent.tools import (
    agent_context,
    default_collection_name,
    fetch_table_section_tool,
    fetch_table_summary_tool,
    get_collected_tables,
    get_context_db_flag,
    get_context_session_id,
    get_context_user_id,
    search_tables_tool,
    validate_sql_tool,
)
from app.utils.logger import sanitize_for_log, setup_logging
from db.conversation_memory import (
    format_conversation_summary,
    get_query_history,
)
from db.langchain_memory import get_checkpointer

logger = setup_logging(__name__)
CHECKPOINTER = None  # lazily initialized when needed to avoid import-time errors

PROVIDER_PRIORITY: list[tuple[str, str | None]] = [
    ("openai", "OPENAI_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("deepseek", "DEEPSEEK_API_KEY"),
    ("groq", "GROQ_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("gemini", "GOOGLE_API_KEY"),
]


def get_available_providers() -> list[str]:
    """Return providers ordered by priority that have credentials available."""
    available = []
    for provider, key_env in PROVIDER_PRIORITY:
        if key_env is None or os.environ.get(key_env):
            available.append(provider)
    # Always add Gemini as fallback even without API key (uses free tier)
    if "gemini" not in available:
        available.append("gemini")
        logger.debug("Added Gemini as fallback provider (free tier)")
    if not available:
        logger.debug("No providers available, defaulting to Gemini")
        return ["gemini"]
    logger.debug("Available providers via env: %s", available)
    return available


def get_preferred_provider() -> str:
    """Return the highest-priority provider that is runnable with existing credentials."""
    return get_available_providers()[0]


class QueryContext(BaseModel):
    """Track previous queries for context-aware responses"""

    query_text: str
    sql_generated: str
    execution_time: float
    tables_used: list[str]
    timestamp: datetime
    follow_up_questions: list[str] = Field(default_factory=list)


class LLMResponse(BaseModel):
    sql_query: str = Field(description="The generated SQL query string")
    follow_up_questions: list[str] = Field(
        default_factory=list, description="List of follow-up questions related to the SQL query"
    )
    query_context: str | None = Field(
        default=None, description="How this query relates to previous queries in the conversation"
    )


def _extract_user_query(request: ModelRequest) -> str | None:
    """Find the latest user message inside the agent request."""
    payload = getattr(request, "input", None)
    messages = None
    if isinstance(payload, dict):
        messages = payload.get("messages")
    else:
        messages = getattr(payload, "messages", None)
    if not messages:
        return None
    for msg in reversed(messages):
        content = None
        role = None
        if isinstance(msg, dict):
            role = msg.get("role")
            content = msg.get("content")
        else:
            role = getattr(msg, "role", None)
            content = getattr(msg, "content", None)
        if role == "user" and content:
            return str(content).strip()
    return None


def _extract_response_text(response: ModelResponse) -> str | None:
    """Normalize the agent response into a short string."""
    result = getattr(response, "result", None)
    if isinstance(result, list) and result:
        final = result[-1]
        text = getattr(final, "content", None)
        if text:
            return str(text).strip()
        if isinstance(final, dict):
            for key in ("content", "output", "answer", "text"):
                value = final.get(key)
                if value:
                    return str(value).strip()
    if isinstance(result, dict):
        for key in ("content", "output", "answer", "text"):
            value = result.get(key)
            if value:
                return str(value).strip()
    if isinstance(result, str):
        return result.strip()
    return None


def _build_checkpoint_payload(
    query_text: str | None,
    response_text: str | None,
    db_flag: str,
) -> dict[str, Any]:
    """Build the checkpoint dict stored in Postgres."""
    version = time.time_ns()
    return {
        "v": LATEST_VERSION,
        "id": str(uuid6()),
        "ts": datetime.now(UTC).isoformat(),
        "channel_values": {
            "query_text": query_text or "",
            "response_text": response_text or "",
            "db_flag": db_flag,
        },
        "channel_versions": {"agent": version},
        "versions_seen": {},
        "updated_channels": ["agent"],
    }


def _persist_checkpoint(request: ModelRequest, response: ModelResponse) -> None:
    user_id = get_context_user_id()
    session_id = get_context_session_id()
    db_flag = get_context_db_flag()
    if not (user_id and session_id and db_flag):
        return
    query_text = _extract_user_query(request)
    response_text = _extract_response_text(response)
    if not query_text and not response_text:
        return
    checkpoint = _build_checkpoint_payload(query_text, response_text, db_flag)
    config = {
        "configurable": {
            "thread_id": session_id,
            "checkpoint_ns": db_flag,
        },
    }
    metadata = {"source": "input", "step": 0}
    try:
        global CHECKPOINTER
        if CHECKPOINTER is None:
            try:
                CHECKPOINTER = get_checkpointer()
            except Exception as exc_init:
                from app.utils.logger import sanitize_for_log

                logger.debug(
                    "LangGraph checkpointer not available: %s",
                    sanitize_for_log(str(exc_init), max_len=300),
                )
                return
        CHECKPOINTER.put(config, checkpoint, metadata, checkpoint["channel_versions"])
    except Exception as exc:  # pragma: no cover - best-effort checkpointing
        from app.utils.logger import sanitize_for_log

        logger.debug(
            "Failed to persist LangGraph checkpoint: %s", sanitize_for_log(str(exc), max_len=500)
        )


@wrap_model_call
def _postgres_checkpoint_middleware(
    request: ModelRequest,
    handler: Callable[[ModelRequest], ModelResponse],
) -> ModelResponse:
    response = handler(request)
    _persist_checkpoint(request, response)
    return response


@wrap_model_call
def debug_model_call(
    request: ModelRequest,
    handler: Callable[[ModelRequest], ModelResponse],
) -> ModelResponse:
    """Log inputs and outputs around every model invocation."""
    logger.debug(
        "Agent middleware - before model call input=%s",
        sanitize_for_log(getattr(request, "input", None)),
    )
    response = handler(request)
    token_usage = None
    result = getattr(response, "result", None)
    if isinstance(result, list) and result:
        final_message = result[-1]
        metadata = getattr(final_message, "response_metadata", {}) or {}
        if isinstance(metadata, dict):
            token_usage = metadata.get("token_usage")
    logger.debug("Agent middleware - after model call tokens=%s", token_usage)
    return response


def _build_system_prompt(
    db_flag: str,
    user_id: str | None = None,
    session_id: str | None = None,
    conversation_summary: str = "",
    previous_context: str = "",
) -> str:
    return SQL_AGENT_PROMPT.format(
        db_flag=db_flag,
        current_time=datetime.utcnow().isoformat(),
        user_id=user_id or "Unknown",
        session_id=session_id or "8ccdc767-7e2c-45b2-9a25-a4bf0d90355d",
        conversation_summary=conversation_summary,
        previous_context=previous_context,
    )


@traceable
def create_sql_agent(llm: BaseChatModel, system_prompt: str) -> Any:
    """Instantiate the LangChain agent runnable for SQL generation."""

    tools = [
        search_tables_tool,
        fetch_table_summary_tool,
        fetch_table_section_tool,
        validate_sql_tool,
    ]
    logger.debug(
        "Creating SQL agent with tools=%s system_prompt_hash=%s",
        [tool.name for tool in tools],
        hash(system_prompt),
    )
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        response_format=ToolStrategy(LLMResponse),
        middleware=[debug_model_call, _postgres_checkpoint_middleware],
    )
    logger.info("Created LangChain SQL agent using model %s", getattr(llm, "model_name", repr(llm)))
    return agent


def _build_context_from_history(
    user_id: str | None,
    session_id: str | None,
    db_flag: str,
) -> tuple[str, str]:
    """Build conversation summary and context from query history.

    Returns: (conversation_summary, previous_context)
    """
    if not user_id or not session_id:
        return "", ""

    try:
        query_history = get_query_history(user_id, session_id, db_flag, limit=3)
        if not query_history:
            return "", ""
        logger.debug(
            "Loaded %d history entries for %s/%s (db=%s)",
            len(query_history),
            user_id,
            session_id,
            db_flag,
        )

        conversation_summary = format_conversation_summary(query_history)
        logger.debug(
            "Conversation summary length=%d",
            len(conversation_summary),
        )
        accessed_tables = {
            table for record in query_history for table in record.get("tables_used", [])
        }
        insights = [
            record["contextual_insights"]
            for record in reversed(query_history)
            if record.get("contextual_insights")
        ]
        previous_context_lines = []
        if accessed_tables:
            previous_context_lines.append(
                "Tables referenced previously: " + ", ".join(sorted(accessed_tables))
            )
        if insights:
            previous_context_lines.append("Insights: " + " | ".join(insights))

        previous_context = "\n".join(previous_context_lines)
        logger.debug(
            "Previous context entries=%d",
            len(previous_context_lines),
        )
        return conversation_summary, previous_context
    except Exception as exc:
        from app.utils.logger import sanitize_for_log

        logger.warning(
            "Failed to build context from history: %s", sanitize_for_log(str(exc), max_len=400)
        )
        return "", ""


def get_llm(provider: str | None = None) -> BaseChatModel:
    """Return the preferred LLM client with provider fallback. If provider is None, auto-select by API key presence."""
    try:
        provider_map = {
            "openai": lambda: ChatOpenAI(
                model="gpt-4o",  # or your preferred OpenAI model
                api_key=os.getenv("OPENAI_API_KEY"),
                temperature=0.1,
            ),
            "openrouter": lambda: ChatOpenAI(
                model="kwaipilot/kat-coder-pro:free",  # or your preferred OpenRouter model
                api_key=os.getenv("OPENROUTER_API_KEY"),
                base_url="https://openrouter.ai/api/v1",
                temperature=0.1,
            ),
            "deepseek": lambda: ChatDeepSeek(
                model="deepseek-chat",
                api_key=os.getenv("DEEPSEEK_API_KEY"),
                api_base="https://api.deepseek.com/v1",
                temperature=0.1,
            ),
            "groq": lambda: ChatGroq(
                model="qwen/qwen3-32b",
                api_key=os.getenv("GROQ_API_KEY"),
                temperature=0.1,
            ),
            "anthropic": lambda: ChatAnthropic(
                model="claude-3-opus-20240229",  # or your preferred Anthropic model
                api_key=os.getenv("ANTHROPIC_API_KEY"),
                temperature=0.1,
            ),
            "gemini": lambda: ChatGoogleGenerativeAI(
                model="gemini-2.5-pro",
                temperature=0.1,
            ),
        }

        alias_map = {
            "google": "gemini",
            "llama": "groq",
            "llama4": "groq",
        }

        if provider:
            provider_normalized = provider.lower()
            provider_normalized = alias_map.get(provider_normalized, provider_normalized)
            if provider_normalized not in provider_map:
                raise ValueError(f"Unsupported provider '{provider}'")
            key_env = dict(PROVIDER_PRIORITY).get(provider_normalized)
            # For Gemini, allow initialization even without API key (free tier)
            if provider_normalized == "gemini" or (key_env is None) or os.environ.get(key_env):
                logger.debug(f"Initializing {provider_normalized} model (explicit)")
                return provider_map[provider_normalized]()
            else:
                logger.warning(
                    "Requested provider '%s' missing API key and not a free provider; falling back to auto-selection",
                    provider_normalized,
                )
        # Auto-select: pick the first provider with credentials
        for prov in get_available_providers():
            if prov in provider_map:
                logger.debug(f"Auto-selecting {prov} model (API key found)")
                return provider_map[prov]()
        logger.debug("Falling back to Gemini (no API key provider found)")
        return provider_map["gemini"]()
    except Exception as exc:
        import traceback

        from app.utils.logger import sanitize_for_log

        logger.error(
            "LLM initialization failed for provider=%s: %s",
            provider,
            sanitize_for_log(traceback.format_exc(), max_len=800),
        )
        raise RuntimeError(f"Failed to initialize LLM for provider '{provider}'") from exc


def get_cached_agent_with_context(
    provider: str,
    db_flag: str,
    user_id: str | None = None,
    session_id: str | None = None,
) -> Any:
    """Return an agent runnable with conversation context awareness.

    Note: This function is NOT cached because conversation context changes per session.
    """
    llm = get_llm(provider)
    conversation_summary, previous_context = _build_context_from_history(
        user_id, session_id, db_flag
    )
    system_prompt = _build_system_prompt(
        db_flag,
        user_id=user_id,
        session_id=session_id,
        conversation_summary=conversation_summary,
        previous_context=previous_context,
    )
    return create_sql_agent(llm, system_prompt)


def get_cached_agent(provider: str, db_flag: str) -> Any:
    """Return an agent runnable for the provider and database context.

    Note: Use get_cached_agent_with_context for conversation-aware agents.
    This variant is for stateless/backward-compatible usage.
    """

    llm = get_llm(provider)
    system_prompt = _build_system_prompt(db_flag)
    return create_sql_agent(llm, system_prompt)


SUMMARY_JSON_LIMIT = 4000


def _truncate_json(raw_json: str, limit: int = SUMMARY_JSON_LIMIT) -> str:
    return raw_json[:limit] if raw_json and len(raw_json) > limit else (raw_json or "[]")


def _resolve_structured_payload(agent_result: Any) -> Any | None:
    if isinstance(agent_result, dict):
        for key in ("structured_response", "structuredResponse"):
            if key in agent_result:
                return agent_result[key]
    for attr in ("structured_response", "structuredResponse"):
        payload = getattr(agent_result, attr, None)
        if payload is not None:
            return payload
    return None


def parse_structured_response(agent_result: Any) -> LLMResponse | None:
    """Extract and validate the structured LLM response if available."""
    payload = _resolve_structured_payload(agent_result)
    if payload is None:
        return None
    if isinstance(payload, LLMResponse):
        return payload
    try:
        return LLMResponse.model_validate(payload)
    except ValidationError as exc:
        from app.utils.logger import sanitize_for_log

        logger.warning(
            "Failed to parse structured LLM response: %s", sanitize_for_log(str(exc), max_len=500)
        )
        return None


def summarize_query_results(provider: str, describe_text: str, raw_json: str) -> str | None:
    """Ask the LLM to generate a natural-language summary for the returned dataset."""
    if not describe_text and not raw_json:
        return None
    prompt = RESULT_SUMMARY_PROMPT.format(
        describe_text=describe_text or "No describe output available",
        raw_json=_truncate_json(raw_json),
    )

    # Try the specified provider first, then fallback to others
    providers_to_try = [provider] + [p for p in get_available_providers() if p != provider]

    for prov in providers_to_try:
        try:
            llm = get_llm(prov)
            # Different LLM client implementations accept different input types.
            # Attempt a few common invocation patterns to maximize compatibility.
            invocation_forms = [
                [HumanMessage(content=prompt)],  # list of messages
                [{"role": "user", "content": prompt}],  # list of dict messages
                prompt,  # raw prompt string
                {"messages": [HumanMessage(content=prompt)]},  # dict with messages (some clients)
                {
                    "messages": [{"role": "user", "content": prompt}]
                },  # dict with messages as list-of-dicts
                {"input": prompt},  # alternate key some clients use
                [{"content": prompt}],  # list of dicts with content-only
            ]
            response = None
            for form in invocation_forms:
                try:
                    response = llm.invoke(form)
                    logger.debug("LLM invoked using form %s for provider=%s", type(form), prov)
                    break
                except Exception as invoke_exc:  # try next form
                    logger.debug(
                        "LLM invoke form %s failed for provider=%s: %s",
                        type(form),
                        prov,
                        invoke_exc,
                    )
                    response = None
                    continue
            # If no direct invocation worked, try alternate methods like generate() or model callables
            if response is None:
                # Try `generate` API if available
                try:
                    if hasattr(llm, "generate"):
                        logger.debug("Trying llm.generate for provider=%s", prov)
                        # Most `generate` methods accept lists of messages or prompts
                        gen_arg = [HumanMessage(content=prompt)]
                        gen_result = llm.generate(gen_arg)
                        # extract text if possible
                        if hasattr(gen_result, "generations"):
                            # Already normalized by many SDKs
                            candidate = getattr(gen_result.generations[0][0], "text", None)
                            if candidate:
                                response = candidate
                        else:
                            # fallback to string representation
                            response = str(gen_result)
                except Exception as gen_exc:
                    logger.debug(
                        "llm.generate not supported or failed for provider=%s: %s", prov, gen_exc
                    )

            if response is None and callable(llm):
                try:
                    logger.debug("Trying callable LLM invocation for provider=%s", prov)
                    response = llm(prompt)
                except Exception as call_exc:
                    logger.debug("Callable invocation failed for provider=%s: %s", prov, call_exc)

            if response is None:
                raise RuntimeError(f"All invoke forms failed for provider={prov}")

            content = getattr(response, "content", None)
            if isinstance(content, str):
                return _truncate_sentences(content.strip(), max_sentences=3)
            if isinstance(response, str):
                return _truncate_sentences(response.strip(), max_sentences=3)
            return (
                _truncate_sentences(str(content).strip(), max_sentences=3)
                if content is not None
                else None
            )
        except Exception as exc:  # pragma: no cover - best-effort summary
            from app.utils.logger import sanitize_for_log

            logger.warning(
                "Result summary generation failed for provider=%s: %s",
                prov,
                sanitize_for_log(str(exc), max_len=500),
            )
            if prov != providers_to_try[-1]:  # Not the last provider
                logger.debug("Trying next provider for summary generation")
                continue

    return None


def _truncate_sentences(text: str, max_sentences: int = 3) -> str:
    """Keep at most `max_sentences` sentences from the text.

    A simple sentence splitter that splits on ., ?, or ! followed by whitespace. Keeps abbreviations unsplit.
    """
    if not text:
        return text
    # Normalize whitespace
    s = " ".join(text.strip().split())

    # Remove internal LLM role tokens like <think>...</think> (and remaining tags)
    s = re.sub(r"<think\b[^>]*>.*?</think>", "", s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"</?think\b[^>]*>", "", s, flags=re.IGNORECASE | re.DOTALL)
    # Split into sentences using punctuation followed by whitespace
    parts = re.split(r"(?<=[\.\?\!])\s+", s)
    if len(parts) <= max_sentences:
        return s
    truncated = " ".join(parts[:max_sentences]).strip()
    # Ensure it ends with a punctuation mark
    if not re.search(r"[\.\?\!]$", truncated):
        truncated = truncated.rstrip(".") + "."
    return truncated


__all__ = [
    "agent_context",
    "create_sql_agent",
    "default_collection_name",
    "get_available_providers",
    "get_cached_agent",
    "get_cached_agent_with_context",
    "get_collected_tables",
    "get_llm",
    "get_preferred_provider",
    "parse_structured_response",
    "summarize_query_results",
]
