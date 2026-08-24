#!/usr/bin/env bash
# Only the cases whose contract is to decline. Run this on every change to the policy engine.
#
# Fails when any unsafe request executed, and also when any unsafe request was not declined -
# a case that errored for an unrelated reason is not a passing refusal.
#
# Usage:  scripts/eval-safety.sh [extra args]
set -euo pipefail

cd "$(dirname "$0")/.."
export MODEL_PROFILE="${MODEL_PROFILE:-fake}"
export EMBEDDING_PROFILE="${EMBEDDING_PROFILE:-fake}"

exec uv run python scripts/eval_run.py safety --label "${EVAL_LABEL:-eval-safety}" "$@"
