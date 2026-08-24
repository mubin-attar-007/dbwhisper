#!/usr/bin/env bash
# Run the corpus and write evaluation/results/<label>.json and .md, provenance block included.
#
# With no arguments this uses the gold-SQL oracle, which does NOT measure a model - the report
# says so in a banner. To produce a publishable number, name a real profile:
#     scripts/eval-report.sh --provider local-balanced
#
# Usage:  scripts/eval-report.sh [--provider NAME] [extra args]
set -euo pipefail

cd "$(dirname "$0")/.."
export MODEL_PROFILE="${MODEL_PROFILE:-fake}"
export EMBEDDING_PROFILE="${EMBEDDING_PROFILE:-fake}"
OUT_DIR="${EVAL_OUT_DIR:-evaluation/results}"

exec uv run python scripts/eval_run.py custom \
    --out "$OUT_DIR" --label "${EVAL_LABEL:-eval-custom}" "$@"
