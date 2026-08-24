"""The model layer: capabilities in, a completion out; providers are an implementation detail."""

from app.llm.registry import ALL_PROFILES, enabled_profiles, profile, registry_version
from app.llm.router import CircuitBreaker, ModelRouter
from app.llm.service import get_router, health_report
from app.llm.structured import complete_structured, extract_json, validate_against
from app.llm.types import (
    FailureKind,
    ModelCapability,
    ModelProfile,
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderHealth,
    ProviderKind,
    RoutingDecision,
    TokenUsage,
)

__all__ = [
    "ALL_PROFILES",
    "CircuitBreaker",
    "FailureKind",
    "ModelCapability",
    "ModelProfile",
    "ModelRequest",
    "ModelResponse",
    "ModelRouter",
    "ProviderError",
    "ProviderHealth",
    "ProviderKind",
    "RoutingDecision",
    "TokenUsage",
    "complete_structured",
    "enabled_profiles",
    "extract_json",
    "get_router",
    "health_report",
    "profile",
    "registry_version",
    "validate_against",
]
