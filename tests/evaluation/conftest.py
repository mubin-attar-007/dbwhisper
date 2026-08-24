"""Shared fixtures: build the evaluation databases once for the whole session.

Building three SQLite fixtures and their enrollment artefacts costs about a second. Doing it per
test would dominate the suite, and doing it per test would also hide the property that matters -
that the build is idempotent and that repeated preparation does not disturb the schema-index mtime
the policy engine's scope cache keys on.
"""

from __future__ import annotations

import pytest

from app.evaluation.datasets import SPECS, get_spec
from app.evaluation.datasets.build import prepare
from app.evaluation.runner import load_dataset_for


@pytest.fixture(scope="session", autouse=True)
def prepared_datasets() -> dict[str, object]:
    """Generate every fixture and enrollment artefact once."""
    built = {}
    for spec in SPECS:
        fixture, schema_index = prepare(spec)
        built[spec.name] = (fixture, schema_index)
    return built


@pytest.fixture(scope="session")
def datasets() -> dict[str, object]:
    """Loaded case files, keyed by dataset name."""
    return {spec.name: load_dataset_for(spec) for spec in SPECS}


@pytest.fixture(scope="session")
def all_cases(datasets) -> list:
    return [case for dataset in datasets.values() for case in dataset.cases]


@pytest.fixture(params=[spec.name for spec in SPECS])
def spec(request):
    """Parametrised over every dataset, so a failure names the dataset that broke."""
    return get_spec(request.param)
