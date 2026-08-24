"""The provider contract. Every adapter implements exactly this."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.llm.types import ModelProfile, ModelRequest, ModelResponse, ProviderHealth


@runtime_checkable
class ModelProvider(Protocol):
    """A source of completions.

    Implementations must:

    * raise :class:`~app.llm.types.ProviderError` with a normalised
      :class:`~app.llm.types.FailureKind` - never a raw SDK exception, which the router cannot
      classify;
    * report the model that actually answered, not the one that was requested (aliases and
      server-side routing make these differ);
    * fill in token usage when the backend reports it, and leave it at zero when it does not,
      rather than estimating and presenting a guess as a measurement.
    """

    name: str

    def complete(self, request: ModelRequest, profile: ModelProfile) -> ModelResponse:
        """Run one completion. Blocking; the caller owns concurrency."""
        ...

    def health(self) -> ProviderHealth:
        """Cheap liveness check. Must not raise."""
        ...


__all__ = ["ModelProvider"]
