"""Adapters for the public text-to-SQL benchmarks - deliberately not vendored, and not downloaded.

Spider and BIRD are the two benchmarks a reader will ask about, so this module exists to answer
three questions honestly rather than to pretend the data is here.

**Why nothing is downloaded.** A harness that fetches a multi-gigabyte archive on import is a harness
nobody can run in CI and a licence nobody has read. Both datasets carry their own terms; Spider is
CC BY-SA 4.0 and BIRD is CC BY-SA 4.0 with its own usage note, and redistributing either inside this
repository would be a licensing decision, not a convenience. ``.gitignore`` already excludes
``eval/spider/`` and ``evaluation/data/external/`` for exactly this reason.

**What the repository already has.** ``evaluation/results/historical/spider_results.json`` records a
2026-07-10 Spider-dev sample scored against the *generation model alone* - no retrieval, no policy
engine, no execution service. ``docs/v2/CLAIM_AUDIT.md`` §4.5 permits publishing that figure only
with its full block and its 9 dropped transport failures stated. It is not a v2 result.

**What it would take to run one here.** :func:`instructions` prints it. The adapters below convert a
downloaded benchmark file into :class:`~app.evaluation.schema.EvalCase` objects; they are complete
and tested against a hand-written sample, but they raise
:class:`ExternalDatasetMissing` rather than fetching anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evaluation.schema import Difficulty, EvalCase, EvalDataset, ExpectedBehavior

#: Where a manually-downloaded benchmark is expected to live. Gitignored.
EXTERNAL_DATA_DIR = Path("evaluation/data/external")


class ExternalDatasetMissing(FileNotFoundError):
    """The benchmark files are not present, and this package will not fetch them."""


@dataclass(frozen=True, slots=True)
class BenchmarkInfo:
    name: str
    homepage: str
    licence: str
    expected_files: tuple[str, ...]
    note: str

    def directory(self) -> Path:
        return EXTERNAL_DATA_DIR / self.name


SPIDER = BenchmarkInfo(
    name="spider",
    homepage="https://yale-lily.github.io/spider",
    licence="CC BY-SA 4.0 (check the current terms before use)",
    expected_files=("dev.json", "tables.json", "database/"),
    note=(
        "Standard Spider execution match compares result sets as multisets and respects row order "
        "under ORDER BY - the same rule app/evaluation/metrics.py applies to this repository's own "
        "corpus."
    ),
)

BIRD = BenchmarkInfo(
    name="bird",
    homepage="https://bird-bench.github.io/",
    licence="CC BY-SA 4.0 with dataset-specific terms (check before use)",
    expected_files=("dev.json", "dev_databases/"),
    note=(
        "BIRD adds external-knowledge evidence per question and reports a Valid Efficiency Score "
        "alongside execution accuracy. Neither is implemented here; only execution accuracy would "
        "be comparable."
    ),
)

BENCHMARKS: dict[str, BenchmarkInfo] = {SPIDER.name: SPIDER, BIRD.name: BIRD}


def instructions(benchmark: str) -> str:
    """What a person has to do by hand before an adapter can read anything."""
    info = BENCHMARKS[benchmark]
    files = "\n".join(f"      - {name}" for name in info.expected_files)
    return (
        f"{info.name.upper()} is not vendored in this repository and this tool will not download it.\n"
        f"  Homepage: {info.homepage}\n"
        f"  Licence:  {info.licence}\n"
        f"  1. Download the dataset by hand and accept its terms.\n"
        f"  2. Unpack it into {info.directory().as_posix()}/ so that it contains:\n{files}\n"
        f"  3. Re-run this command. The directory is gitignored, so nothing is committed.\n"
        f"  Note: {info.note}\n"
    )


def available(benchmark: str) -> bool:
    info = BENCHMARKS[benchmark]
    return all((info.directory() / name.rstrip("/")).exists() for name in info.expected_files)


def require(benchmark: str) -> Path:
    if not available(benchmark):
        raise ExternalDatasetMissing(instructions(benchmark))
    return BENCHMARKS[benchmark].directory()


# ---------------------------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------------------------


def spider_cases(payload: list[dict[str, Any]], *, dataset: str = "spider_dev") -> EvalDataset:
    """Convert Spider's ``dev.json`` records into evaluation cases.

    Spider's ``hardness`` labels are not in ``dev.json`` (they are computed by its own evaluator), so
    difficulty is left at ``medium`` rather than guessed. Every case is ``ANSWER``: Spider has no
    ambiguous or unsafe questions, which is one reason it does not substitute for this repository's
    own corpus.
    """
    cases = []
    for index, record in enumerate(payload):
        db_id = str(record.get("db_id") or "")
        cases.append(
            EvalCase(
                id=f"spider-{index:04d}",
                dataset=dataset,
                database=db_id,
                dialect="sqlite",
                question=str(record.get("question") or ""),
                expected_behavior=ExpectedBehavior.ANSWER,
                gold_sql=str(record.get("query") or ""),
                difficulty=Difficulty.MEDIUM,
                tags=("answerable", "external", "spider"),
                notes=f"Spider dev record {index} for database '{db_id}'.",
            )
        )
    return EvalDataset(
        name=dataset,
        version="external",
        database="(per case)",
        dialect="sqlite",
        description="Spider dev split, converted on demand from a locally downloaded copy.",
        cases=tuple(cases),
        synthetic=False,
        notes=SPIDER.note,
    )


def bird_cases(payload: list[dict[str, Any]], *, dataset: str = "bird_dev") -> EvalDataset:
    """Convert BIRD's ``dev.json`` records into evaluation cases.

    BIRD ships an ``evidence`` string per question - external knowledge the model is allowed to see.
    It is preserved in ``notes`` rather than folded into the question, because concatenating it would
    quietly change what is being measured.
    """
    cases = []
    for index, record in enumerate(payload):
        db_id = str(record.get("db_id") or "")
        evidence = str(record.get("evidence") or "").strip()
        cases.append(
            EvalCase(
                id=f"bird-{index:04d}",
                dataset=dataset,
                database=db_id,
                dialect="sqlite",
                question=str(record.get("question") or ""),
                expected_behavior=ExpectedBehavior.ANSWER,
                gold_sql=str(record.get("SQL") or record.get("query") or ""),
                difficulty=Difficulty.MEDIUM,
                tags=("answerable", "external", "bird"),
                notes=(
                    f"BIRD evidence (not shown to the model here): {evidence}" if evidence else ""
                ),
            )
        )
    return EvalDataset(
        name=dataset,
        version="external",
        database="(per case)",
        dialect="sqlite",
        description="BIRD dev split, converted on demand from a locally downloaded copy.",
        cases=tuple(cases),
        synthetic=False,
        notes=BIRD.note,
    )


def load_external(benchmark: str, limit: int | None = None) -> EvalDataset:
    """Load a downloaded benchmark. Raises :class:`ExternalDatasetMissing` with instructions."""
    directory = require(benchmark)
    payload = json.loads((directory / "dev.json").read_text(encoding="utf-8"))
    records = payload[:limit] if limit else payload
    return spider_cases(records) if benchmark == SPIDER.name else bird_cases(records)


__all__ = [
    "BENCHMARKS",
    "BIRD",
    "EXTERNAL_DATA_DIR",
    "SPIDER",
    "BenchmarkInfo",
    "ExternalDatasetMissing",
    "available",
    "bird_cases",
    "instructions",
    "load_external",
    "require",
    "spider_cases",
]
