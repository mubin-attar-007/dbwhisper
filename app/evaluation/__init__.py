"""Evaluation infrastructure: the corpus, the metrics, the runner and the reports.

The package exists because of a specific failure recorded in ``docs/v2/CLAIM_AUDIT.md`` §4.4: the v1
evaluation produced an "82% execution accuracy" figure that could not be published, for four
independent reasons - the harness was untracked, it depended on a service running on one laptop, it
measured a prompt that had since been deleted, and its scoring predicate compared result sets as a
``frozenset`` and so collapsed duplicate rows.

Every design decision here is a response to one of those:

* the harness is **inside the repository and committed**, and its fixtures are generated from seeded
  code rather than copied from a machine (:mod:`app.evaluation.datasets`);
* it runs **fully offline** with no credentials and no listening port (:mod:`app.evaluation.runner`);
* every run records the **prompt and component versions** that produced it
  (:mod:`app.evaluation.provenance`);
* correctness is a **multiset comparison that respects ``ORDER BY``**, and the looser set-based
  verdict is computed alongside it purely to show how much it would have inflated the number
  (:mod:`app.evaluation.metrics`).

Start at :func:`app.evaluation.runner.run`, or run ``python -m app.evaluation.cli smoke``.
"""

from __future__ import annotations

__all__ = [
    "adapters",
    "cli",
    "datasets",
    "metrics",
    "oracle",
    "outcomes",
    "provenance",
    "report",
    "runner",
    "schema",
    "sqlfacts",
    "taxonomy",
]
