#!/usr/bin/env bash
# Retrieval metrics only: no model, no policy engine, no execution. Seconds, not minutes.
#
# Usage:  scripts/eval-retrieval.sh [--k 8] [extra args]
set -euo pipefail

cd "$(dirname "$0")/.."
export MODEL_PROFILE="${MODEL_PROFILE:-fake}"
export EMBEDDING_PROFILE="${EMBEDDING_PROFILE:-fake}"

exec uv run python scripts/eval_run.py retrieval "$@"
