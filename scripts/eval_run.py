"""Entry point for the evaluation harness that works without ``-m`` and without an installed package.

``pyproject.toml`` sets ``package = false`` - DBWhisper is run from source - so ``python
scripts/eval_run.py`` from anywhere needs the repository root on ``sys.path`` before ``app`` can be
imported. That is all this file does; the argument parsing lives in :mod:`app.evaluation.cli`.

It also pins the offline providers when the caller has not chosen otherwise, so an evaluation cannot
quietly reach the network (or spend money) because a stale ``.env`` was sitting in the working
directory. Set ``MODEL_PROFILE`` explicitly to run against a real model.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Deliberate defaults, not overrides: an explicit environment always wins.
os.environ.setdefault("MODEL_PROFILE", "fake")
os.environ.setdefault("EMBEDDING_PROFILE", "fake")

from app.evaluation.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
