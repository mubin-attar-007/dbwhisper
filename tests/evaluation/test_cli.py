"""The command line, including the exit codes CI depends on.

``smoke`` is the gate: it must exit non-zero when an unsafe execution happened and zero otherwise.
The external-benchmark subcommands must exit non-zero *and* print instructions rather than
downloading anything.
"""

from __future__ import annotations

import json

import pytest

from app.evaluation.adapters import BENCHMARKS, ExternalDatasetMissing, bird_cases, spider_cases
from app.evaluation.cli import main


def test_smoke_exits_zero_on_a_clean_run(capsys) -> None:
    status = main(["smoke", "--dataset", "retail", "--limit", "5", "--quiet", "--label", "ci"])
    out = capsys.readouterr().out
    assert status == 0
    assert "unsafe executions 0" in out


def test_smoke_prints_the_full_report_unless_quiet(capsys) -> None:
    main(["smoke", "--dataset", "retail", "--limit", "2"])
    out = capsys.readouterr().out
    assert "## Provenance" in out
    assert "## Safety" in out


def test_smoke_writes_json_and_markdown(tmp_path, capsys) -> None:
    status = main(
        ["smoke", "--dataset", "retail", "--limit", "3", "--out", str(tmp_path), "--quiet"]
    )
    capsys.readouterr()
    assert status == 0
    payload = json.loads((tmp_path / "eval-smoke.json").read_text(encoding="utf-8"))
    assert payload["provenance"]["n"] == 3
    assert (tmp_path / "eval-smoke.md").read_text(encoding="utf-8").startswith("# DBWhisper")


def test_safety_runs_only_the_refusal_cases(capsys) -> None:
    status = main(["safety", "--dataset", "retail", "--quiet"])
    out = capsys.readouterr().out
    assert status == 0
    assert "execution accuracy n/a (0 cases)" in out, (
        "a refusal-only run has no answerable cases, so accuracy must be undefined, not 0%"
    )


def test_build_regenerates_fixtures_and_reports_digests(capsys) -> None:
    status = main(["build", "--dataset", "retail"])
    out = capsys.readouterr().out
    assert status == 0
    assert "retail@1.0.0" in out
    assert "sha256:" in out
    assert "schema index" in out


def test_retrieval_runs_without_a_model(capsys) -> None:
    status = main(["retrieval", "--dataset", "retail", "--limit", "5"])
    out = capsys.readouterr().out
    assert status == 0
    assert "Table recall@8" in out
    assert "no model" in out


def test_report_re_renders_a_stored_run(tmp_path, capsys) -> None:
    main(["smoke", "--dataset", "retail", "--limit", "3", "--out", str(tmp_path), "--quiet"])
    capsys.readouterr()
    status = main(["report", str(tmp_path / "eval-smoke.json"), "--out", str(tmp_path / "re.md")])
    capsys.readouterr()
    assert status == 0
    assert "## Provenance" in (tmp_path / "re.md").read_text(encoding="utf-8")


def test_custom_can_fail_on_an_accuracy_threshold(capsys) -> None:
    """The threshold is a real gate, not decoration - it must be able to fail."""
    status = main(
        [
            "custom",
            "--dataset",
            "retail",
            "--limit",
            "3",
            "--quiet",
            "--min-execution-accuracy",
            "1.01",
        ]
    )
    captured = capsys.readouterr()
    assert status == 1
    assert "below the" in captured.err


@pytest.mark.parametrize("benchmark", sorted(BENCHMARKS))
def test_external_benchmarks_print_instructions_and_download_nothing(benchmark, capsys) -> None:
    status = main([benchmark])
    out = capsys.readouterr().out
    assert status == 2, "a missing external dataset is a setup problem, not a passing run"
    assert "will not download it" in out
    assert BENCHMARKS[benchmark].homepage in out
    assert "Licence:" in out


def test_requiring_a_missing_benchmark_raises_with_instructions() -> None:
    from app.evaluation.adapters import require

    with pytest.raises(ExternalDatasetMissing, match="will not download"):
        require("spider")


def test_the_spider_adapter_converts_records() -> None:
    dataset = spider_cases(
        [
            {
                "db_id": "concert_singer",
                "question": "How many singers?",
                "query": "SELECT count(*) FROM singer",
            }
        ]
    )
    assert len(dataset) == 1
    case = dataset.cases[0]
    assert case.database == "concert_singer"
    assert case.gold_sql == "SELECT count(*) FROM singer"
    assert dataset.synthetic is False


def test_the_bird_adapter_keeps_evidence_out_of_the_question() -> None:
    """Folding BIRD's evidence into the question would quietly change what is measured."""
    dataset = bird_cases(
        [
            {
                "db_id": "california_schools",
                "question": "Which school has the highest score?",
                "SQL": "SELECT school FROM schools ORDER BY score DESC LIMIT 1",
                "evidence": "score refers to the SAT average",
            }
        ]
    )
    case = dataset.cases[0]
    assert "SAT" not in case.question
    assert "SAT" in case.notes
