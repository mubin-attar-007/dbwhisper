# ADR 0004 — Local-first model layer: Ollama over plain `httpx`, plus a deterministic fake provider

**Status:** Accepted — 2026-08-21, Phase 4.
**Principle:** "No required purchase" and "CI without Postgres" — `docs/v2/TARGET_ARCHITECTURE.md`
§2, §6.

## Context

v1 needed a hosted key to do anything. `app/agent/chain.py::get_available_providers` walks a fixed
priority list of environment variables and, if none is set, "always adds Gemini as fallback
(uses free tier)" — which in practice means the first request raises `DefaultCredentialsError`
(`docs/v2/CLAIM_AUDIT.md` §1.4 records that call being made and failing). The consequences ran
deeper than a bad first-run experience:

* the test suite could not exercise the generation path without a key and a network;
* an evaluation number was not reproducible by anybody outside the machine that produced it
  (`CLAIM_AUDIT.md` §4.4);
* fallback was "next provider on any exception", so a provider known to be down was re-dialled on
  every request, and nothing recorded which provider actually answered.

## Decision

**A local provider with no SDK.** `app/llm/providers/ollama.py` speaks two wire formats over
`httpx` and nothing else: Ollama's native `/api/chat` and the OpenAI-compatible
`/v1/chat/completions` that llama.cpp, vLLM and LM Studio also serve. No vendor client library sits
on the default path, so nothing can pull a cloud SDK into a local install.

**A deterministic provider that is a real provider.** `app/llm/providers/fake.py` resolves answers
from fixtures keyed by `(prompt_name, sha256(system + user))`, then by prompt name, then by
registered rules, then by a schema-shaped default. It can script failures (`fail_next`), latency and
malformed structured output. It is not a mock: the same provider contract, the same router, the same
structured-output repair loop run in CI as in production. `tests/conftest.py` sets
`MODEL_PROFILE=fake` and `EMBEDDING_PROFILE=fake` at import time, so the suite never reaches the
network.

**Profiles are code, environment only enables them.** `app/llm/registry.py` defines `LOCAL_PROFILES`,
`FAKE_PROFILE` and `REMOTE_PROFILES` as constants with a `registry_version()` hash recorded on runs.

**Routing is a filter then a sort.** `app/llm/router.py::ModelRouter` hard-filters on requested
capabilities, on egress policy (ADR 0008) and on circuit-breaker state, then sorts local-first,
healthy-first, quality, latency. Every attempt is recorded in a `RoutingDecision`, so a trace can
answer "why did this run use that model?".

**CPU embeddings by default.** `app/embeddings/local.py` uses fastembed (ONNX Runtime) rather than
sentence-transformers, because the install is tens of megabytes rather than a PyTorch stack, and
every vector carries an `EmbeddingVersion` (provider, model, dimension, normalisation) that the
index filters on when the caller supplies one — so changing the embedding model produces a new
version indexed alongside the old one instead of silently corrupting it.

## Consequences

**What it buys.** `uv run pytest` passes with no key and no network — 2318 tests, verified on this
branch. An evaluation run is reproducible from a clean checkout (ADR 0016). Failures are normalised
into `FailureKind`, so routing decisions are made on classified state rather than on pattern-matching
error strings.

**What it costs.**

* **We own the client.** Retries, timeouts, streaming, JSON-schema negotiation and the bounded
  structured-output repair loop (`app/llm/structured.py`) are ours to maintain — an SDK would have
  supplied them. Two wire formats double that surface.
* **The fake provider is a second implementation of the contract**, and a fake that drifts from
  reality turns a green CI run into a lie. Mitigated by running the provider contract tests against
  every adapter (`tests/llm`, 102 tests), not by hoping.
* **Local model quality is unmeasured by us.** The profile table in `TARGET_ARCHITECTURE.md` §6
  (`qwen2.5-coder` at 1.5b/7b/14b) is a starting point chosen for licence and size. No model-matrix
  evaluation has been published, so no claim about local accuracy may be made.
* **The vendor SDKs are still installed.** `app/llm/providers/remote.py` wraps LangChain chat
  adapters because they were already dependencies, and `app/agent/chain.py` imports five of them at
  module scope for the v1 path (ADR 0015). "No paid service is required" is true; "no cloud client
  is installed" is not, and `pyproject.toml` shows it.
* **First local use downloads a model.** fastembed fetches `BAAI/bge-small-en-v1.5` on first call.
  That is exactly why CI uses the fake embedder — but it means a first local run is not offline.

**Enforcement.** The import contract `The model layer cannot reach a database` forbids `app.llm` and
`app.embeddings` from importing `app.execution`, `db`, `sqlalchemy`, `psycopg`, `pymysql`, `sqlite3`
or `pyodbc`. A provider that could open a connection is one prompt injection away from being asked
to.
