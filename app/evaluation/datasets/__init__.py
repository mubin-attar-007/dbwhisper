"""The evaluation corpus: three synthetic databases and the committed questions asked of them.

A dataset is a pair - a :class:`~app.evaluation.datasets.spec.DatasetSpec` that generates the
database, and a YAML case file beside it that asks questions of it. Both are committed; the
generated ``.sqlite`` is not, because it can be rebuilt byte-for-byte from the spec.

``retail`` and ``saas_ops`` are ordinary business domains. ``synthetic_snf`` is healthcare-shaped and
is labelled synthetic in the spec, in the generator's docstring, in the generated schema index, and
in every report that mentions it - it contains no real identifiers of any kind and no conclusion may
be drawn from it. See ``app/evaluation/datasets/synthetic_snf.py`` for the full statement.

``adversarial/sql_policy_cases.yaml`` is a different kind of corpus and is owned by
``tests/sqlpolicy/``: it tests the policy *decision function* with no database and no model. This
package does not read it.
"""

from __future__ import annotations

from pathlib import Path

from app.evaluation.datasets import retail, saas_ops, synthetic_snf
from app.evaluation.datasets.spec import DatasetSpec

CASES_DIR = Path(__file__).parent / "cases"

#: Every dataset in the corpus, in the order a full run visits them.
SPECS: tuple[DatasetSpec, ...] = (retail.SPEC, saas_ops.SPEC, synthetic_snf.SPEC)

SPECS_BY_NAME: dict[str, DatasetSpec] = {spec.name: spec for spec in SPECS}
SPECS_BY_SOURCE_ID: dict[str, DatasetSpec] = {spec.source_id: spec for spec in SPECS}


def get_spec(name: str) -> DatasetSpec:
    """Look a dataset up by name or by ``source_id``."""
    if name in SPECS_BY_NAME:
        return SPECS_BY_NAME[name]
    if name in SPECS_BY_SOURCE_ID:
        return SPECS_BY_SOURCE_ID[name]
    known = ", ".join(sorted(SPECS_BY_NAME))
    raise KeyError(f"Unknown evaluation dataset '{name}'. Known datasets: {known}")


def case_file(name: str) -> Path:
    """Path to the committed YAML case file for a dataset."""
    return CASES_DIR / f"{get_spec(name).name}.yaml"


def dataset_names() -> tuple[str, ...]:
    return tuple(SPECS_BY_NAME)


__all__ = [
    "CASES_DIR",
    "SPECS",
    "SPECS_BY_NAME",
    "SPECS_BY_SOURCE_ID",
    "case_file",
    "dataset_names",
    "get_spec",
]
