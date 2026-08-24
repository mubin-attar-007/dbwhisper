"""The block that must travel with every number this package produces.

``docs/v2/CLAIM_AUDIT.md`` §4 states the rule plainly: *a number may be published only with a
provenance block containing dataset + version, n, model, prompt version, date, execution environment,
exclusions, limitations.* §5 explains why - "a metric without a prompt version is not re-checkable
after the next prompt change" - and §4.4 is a four-part demonstration of what happens without one.

So the block is a dataclass, not a convention. Every report renders it, the JSON carries it, and
:meth:`Provenance.missing_fields` refuses to let a run be reported with holes in it.

:attr:`Provenance.measures_model` is the field that keeps this harness honest. A run driven by the
gold-SQL oracle (:mod:`app.evaluation.oracle`) exercises retrieval, policy, execution, result
comparison and the safety path for real - but the SQL was *replayed from the dataset*, so its
execution accuracy says nothing whatsoever about a model. When ``measures_model`` is false the report
says so at the top, in the summary, and next to the accuracy figure.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def environment_description() -> str:
    """Where this ran, in the form the audit's approved blocks use."""
    return (
        f"{platform.system()} {platform.release()}, "
        f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )


@dataclass(slots=True)
class Provenance:
    """Everything a sceptical reader needs to decide whether to believe a number."""

    #: ``retail@1.0.0`` - dataset name and version.
    dataset: str
    #: Content hash over the case corpus, so an edited case file is visible.
    dataset_hash: str
    #: How many cases the numbers are computed over, after exclusions.
    n: int
    #: How many cases the file holds, before exclusions.
    n_available: int
    #: The model profile that answered (``fake/oracle``, ``local-balanced``, ...).
    model_profile: str
    model_identifier: str
    #: ``{"generate_sql": "generate_sql@2.0", ...}`` - taken from the runs, not from a constant.
    prompt_versions: dict[str, str] = field(default_factory=dict)
    #: Versions of the deterministic components that shaped the result.
    component_versions: dict[str, str] = field(default_factory=dict)
    date: str = field(default_factory=_utc_now)
    environment: str = field(default_factory=environment_description)
    #: Cases deliberately not run, and why. An empty list is a claim: "every case ran".
    exclusions: list[str] = field(default_factory=list)
    #: What the numbers cannot support. Never empty in practice.
    limitations: list[str] = field(default_factory=list)
    #: The command that reproduces this run from a clean checkout.
    reproduce_command: str = ""
    #: False when the SQL was replayed rather than generated. Gates every model-quality claim.
    measures_model: bool = True
    #: Free-form label for the run (``eval-smoke``, ``nightly``, a git sha).
    run_label: str = ""
    fixture_digests: dict[str, str] = field(default_factory=dict)
    row_counts: dict[str, dict[str, int]] = field(default_factory=dict)

    REQUIRED = (
        "dataset",
        "dataset_hash",
        "n",
        "model_profile",
        "date",
        "environment",
        "reproduce_command",
    )

    def missing_fields(self) -> list[str]:
        """Which required parts of the block are empty. A report with any of these is not publishable."""
        missing = []
        for name in self.REQUIRED:
            value = getattr(self, name)
            if value in (None, "", 0) and not (name == "n" and value == 0):
                missing.append(name)
        if not self.limitations:
            missing.append("limitations")
        return missing

    @property
    def publishable(self) -> bool:
        """Complete *and* actually a measurement of the model. Not a claim that it is a good number."""
        return not self.missing_fields() and self.measures_model

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "dataset_hash": self.dataset_hash,
            "n": self.n,
            "n_available": self.n_available,
            "model_profile": self.model_profile,
            "model_identifier": self.model_identifier,
            "prompt_versions": dict(self.prompt_versions),
            "component_versions": dict(self.component_versions),
            "date": self.date,
            "environment": self.environment,
            "exclusions": list(self.exclusions),
            "limitations": list(self.limitations),
            "reproduce_command": self.reproduce_command,
            "measures_model": self.measures_model,
            "run_label": self.run_label,
            "fixture_digests": dict(self.fixture_digests),
            "row_counts": {k: dict(v) for k, v in self.row_counts.items()},
            "complete": not self.missing_fields(),
            "missing_fields": self.missing_fields(),
        }

    def as_markdown(self) -> str:
        """The blockquote form used throughout ``docs/v2/CLAIM_AUDIT.md`` §4."""
        lines = [
            f"> **Dataset:** `{self.dataset}` (corpus hash `{self.dataset_hash}`)",
            f"> **n:** {self.n} cases scored of {self.n_available} in the corpus",
            f"> **Model:** `{self.model_profile}` / `{self.model_identifier}`",
        ]
        if self.prompt_versions:
            rendered = ", ".join(f"`{k}`=`{v}`" for k, v in sorted(self.prompt_versions.items()))
            lines.append(f"> **Prompt versions:** {rendered}")
        else:
            lines.append("> **Prompt versions:** none recorded - no model prompt was used")
        if self.component_versions:
            rendered = ", ".join(f"`{k}`=`{v}`" for k, v in sorted(self.component_versions.items()))
            lines.append(f"> **Component versions:** {rendered}")
        lines.append(f"> **Date / environment:** {self.date}, {self.environment}")
        if self.fixture_digests:
            rendered = ", ".join(
                f"`{k}`=`{v[:23]}`" for k, v in sorted(self.fixture_digests.items())
            )
            lines.append(f"> **Fixture digests:** {rendered}")
        lines.append(
            "> **Exclusions:** "
            + ("; ".join(self.exclusions) if self.exclusions else "none - every case ran")
        )
        for index, limitation in enumerate(self.limitations):
            prefix = "> **Limitations:** " if index == 0 else "> "
            lines.append(f"{prefix}({chr(97 + index)}) {limitation}")
        lines.append(f"> **Reproduce:** `{self.reproduce_command}`")
        if not self.measures_model:
            lines.append(
                "> **NOT A MODEL MEASUREMENT.** The SQL in this run was replayed from the dataset's "
                "reference queries, not generated by a model. Correctness figures here describe the "
                "harness, the retrieval index, the policy engine and the execution path - they say "
                "nothing about how well any model writes SQL."
            )
        missing = self.missing_fields()
        if missing:
            lines.append(
                f"> **INCOMPLETE PROVENANCE - do not publish.** Missing: {', '.join(missing)}."
            )
        return "\n".join(lines)


__all__ = ["Provenance", "environment_description"]
