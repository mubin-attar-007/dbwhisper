"""Report rendering, and the two rules it is supposed to enforce.

``docs/v2/CLAIM_AUDIT.md`` §1.3 forbids a bare percentage and §4 requires a provenance block on every
number. Those are asserted here against the real rendered text, not against an intention.
"""

from __future__ import annotations

import json
import re

import pytest

from app.evaluation import report as report_module
from app.evaluation.provenance import Provenance
from app.evaluation.runner import RunnerConfig, run

#: The only shape a percentage is allowed to appear in: "18 of 22 (81.8%)".
_ATTRIBUTED_PERCENTAGE = re.compile(r"\d+ of \d+ \(\d+(?:\.\d+)?%\)")


@pytest.fixture(scope="module")
def rendered():
    result = run(RunnerConfig(datasets=("retail",), provider="oracle", run_label="test-report"))
    return result, report_module.to_markdown(result)


def test_no_percentage_appears_without_its_numerator_and_denominator(rendered) -> None:
    """The rule the v1 stat cards broke: "82%" with nothing a reader could re-derive it from."""
    _result, markdown = rendered
    for line in markdown.splitlines():
        if "%" not in line:
            continue
        attributed = {
            index
            for match in _ATTRIBUTED_PERCENTAGE.finditer(line)
            for index in range(match.start(), match.end())
        }
        for position, character in enumerate(line):
            assert character != "%" or position in attributed, (
                f"a percentage appears without its numerator and denominator: {line.strip()}"
            )


def test_the_provenance_block_is_the_first_content_section(rendered) -> None:
    _result, markdown = rendered
    headings = [line for line in markdown.splitlines() if line.startswith("## ")]
    assert headings[0] == "## Provenance"


def test_an_oracle_run_is_banner_flagged_before_any_number(rendered) -> None:
    _result, markdown = rendered
    banner_position = markdown.index("did not measure a model")
    accuracy_position = markdown.index("Execution accuracy")
    assert banner_position < accuracy_position


def test_synthetic_data_is_declared(rendered) -> None:
    _result, markdown = rendered
    assert "Synthetic data" in markdown
    assert "No row describes a real person" in markdown


def test_every_metric_section_is_present(rendered) -> None:
    _result, markdown = rendered
    for heading in (
        "## Datasets",
        "## Headline",
        "## Retrieval",
        "## Generation",
        "## Clarification",
        "## Safety",
        "## Failures",
        "## Operations",
    ):
        assert heading in markdown


def test_the_scoring_method_check_is_reported(rendered) -> None:
    """The set-versus-multiset gap is the evidence for the metric fix; it must be visible."""
    _result, markdown = rendered
    assert "duplicate-collapsing set comparison" in markdown
    assert "CLAIM_AUDIT.md" in markdown


def test_the_unsafe_execution_count_is_in_the_headline(rendered) -> None:
    _result, markdown = rendered
    headline = markdown.split("## Headline", 1)[1].split("##", 1)[0]
    assert "Unsafe executions:" in headline


def test_json_holds_every_case_so_a_run_can_be_rescored(rendered, tmp_path) -> None:
    result, _markdown = rendered
    path = report_module.write_json(result, tmp_path / "run.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert next(iter(payload)) == "provenance"
    assert len(payload["cases"]) == len(result.outcomes)
    assert payload["cases"][0]["case_id"] == result.outcomes[0].case_id
    assert payload["scorecard"]["safety"]["unsafe_executions"] == 0


def test_a_stored_run_re_renders_without_re_running_anything(rendered, tmp_path) -> None:
    """The capability the v1 harness lacked when its scoring predicate turned out to be wrong."""
    from app.evaluation.cli import _markdown_from_payload

    result, _markdown = rendered
    path = report_module.write_json(result, tmp_path / "run.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    re_rendered = _markdown_from_payload(payload)
    assert "## Provenance" in re_rendered
    assert result.scorecard.generation.execution_accuracy.text() in re_rendered
    assert "did not measure a model" in re_rendered


def test_the_summary_line_leads_with_the_gate(rendered) -> None:
    result, _markdown = rendered
    line = report_module.summary_line(result)
    assert "unsafe executions 0" in line
    assert "not a model measurement" in line


def test_markdown_writes_to_disk(rendered, tmp_path) -> None:
    result, markdown = rendered
    path = report_module.write_markdown(result, tmp_path / "run.md")
    assert path.read_text(encoding="utf-8") == markdown


# ---------------------------------------------------------------------------------------------
# Provenance completeness
# ---------------------------------------------------------------------------------------------


def test_a_provenance_block_with_holes_refuses_to_be_publishable() -> None:
    incomplete = Provenance(
        dataset="",
        dataset_hash="",
        n=0,
        n_available=0,
        model_profile="",
        model_identifier="",
    )
    missing = incomplete.missing_fields()
    assert "dataset" in missing
    assert "limitations" in missing
    assert incomplete.publishable is False
    assert "INCOMPLETE PROVENANCE" in incomplete.as_markdown()


def test_a_complete_block_that_did_not_measure_a_model_is_still_not_publishable() -> None:
    """Completeness is necessary, not sufficient - the oracle path must never slip through."""
    provenance = Provenance(
        dataset="retail@1.0.0",
        dataset_hash="sha256:abc",
        n=10,
        n_available=10,
        model_profile="fake/oracle",
        model_identifier="reference SQL replay",
        limitations=["the SQL was replayed"],
        reproduce_command="uv run python -m app.evaluation.cli smoke",
        measures_model=False,
    )
    assert provenance.missing_fields() == []
    assert provenance.publishable is False
    assert "NOT A MODEL MEASUREMENT" in provenance.as_markdown()


def test_a_complete_model_run_block_is_publishable() -> None:
    provenance = Provenance(
        dataset="retail@1.0.0",
        dataset_hash="sha256:abc",
        n=50,
        n_available=63,
        model_profile="local-balanced",
        model_identifier="qwen2.5-coder:7b",
        prompt_versions={"generate_sql": "generate_sql@2.0"},
        limitations=["synthetic fixtures only"],
        exclusions=["13 cases were not selected"],
        reproduce_command="uv run python -m app.evaluation.cli custom --provider local-balanced",
    )
    assert provenance.publishable is True
    markdown = provenance.as_markdown()
    assert "**n:** 50 cases scored of 63" in markdown
    assert "generate_sql@2.0" in markdown
    assert "13 cases were not selected" in markdown
