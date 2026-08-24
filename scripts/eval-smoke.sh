#!/usr/bin/env bash
# The CI gate: run the whole evaluation corpus offline and fail on any unsafe execution.
#
# Deterministic, needs no credentials and no network. The exit code is the gate - a non-zero
# status means a request whose contract is to be declined returned rows, or a statement that
# executed does not pass an independent re-check by the policy engine.
#
# Usage:  scripts/eval-smoke.sh [extra args passed to `python -m app.evaluation.cli smoke`]
set -euo pipefail

cd "$(dirname "$0")/.."
export MODEL_PROFILE="${MODEL_PROFILE:-fake}"
export EMBEDDING_PROFILE="${EMBEDDING_PROFILE:-fake}"

exec uv run python scripts/eval_run.py smoke --label "${EVAL_LABEL:-eval-smoke}" "$@"
