"""What the graph needs from the outside world, injected rather than imported.

Nodes never reach for a global. Everything they use - the model router, the retrieval index, the
embedder, and the resolver that turns a ``source_id`` into a connection - arrives in a
:class:`GraphDeps` bound when the graph is compiled.

That is what keeps credentials out of the checkpointed state: the state carries an identifier, and
only the deps can turn it into something that can open a connection.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.embeddings.base import EmbeddingProvider
from app.llm.router import ModelRouter
from app.platform.modes import EgressPolicy, NetworkPolicyLevel
from app.retrieval.base import RetrievalIndex
from app.sqlpolicy.types import Dialect, PolicyLevel


@dataclass(frozen=True, slots=True)
class DataSourceTarget:
    """Everything needed to execute against one enrolled database."""

    source_id: str
    connection_string: str
    dialect: Dialect
    max_rows: int = 1000
    timeout_seconds: int = 30
    snapshot_id: str | None = None
    description: str = ""


#: Resolves an identifier to a target. Raises ``KeyError`` when the source is unknown, which the
#: graph turns into a blocked run rather than a crash.
SourceResolver = Callable[[str], DataSourceTarget]


@dataclass(slots=True)
class GraphDeps:
    router: ModelRouter
    index: RetrievalIndex
    embedder: EmbeddingProvider
    resolve_source: SourceResolver
    egress_policy: EgressPolicy = EgressPolicy.LOCAL_ONLY
    policy_level: PolicyLevel = PolicyLevel.STANDARD
    network_level: NetworkPolicyLevel = NetworkPolicyLevel.PRIVATE_ALLOWED
    network_allowlist: list[str] = field(default_factory=list)
    bundled_hosts: list[str] = field(default_factory=list)
    #: How many times generated SQL may be repaired before the run gives up.
    max_repairs: int = 2
    #: Token budget for the schema context handed to the model.
    context_budget_tokens: int = 3000
    #: How many candidate documents retrieval selects before neighbour expansion.
    retrieval_k: int = 8
    #: Skip the model-based understanding step (used by evaluation runs that supply an intent).
    skip_understanding: bool = False
    #: Extra metadata recorded on every run (application version, deployment name...).
    run_metadata: dict[str, Any] = field(default_factory=dict)


__all__ = ["DataSourceTarget", "GraphDeps", "SourceResolver"]
