"""The retrieval command's reporting flags, and the provenance a number needs to be citable.

`--out` and `--label` were advertised in the help text and silently did nothing, and the embedder was
hardcoded so `retrieval` could never measure the semantic path a real deployment uses. A flag that
does nothing is worse than an absent flag: it produces a confident-looking run with no artefact, and
nobody notices until they go looking for the report.
"""

from __future__ import annotations

import json

import pytest

from app.evaluation import cli


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILE", "fake")
    monkeypatch.setenv("EMBEDDING_PROFILE", "fake")


def _run(tmp_path, *extra: str) -> int:
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "retrieval",
            "--dataset",
            "retail",
            "--limit",
            "3",
            "--out",
            str(tmp_path),
            "--label",
            "unit",
            *extra,
        ]
    )
    return args.handler(args)


class TestTheEmbedderIsSelectable:
    def test_fake_is_the_default_so_ci_stays_offline(self):
        parser = cli.build_parser()
        assert parser.parse_args(["retrieval"]).embeddings == "fake"

    def test_local_is_offered(self):
        parser = cli.build_parser()
        assert parser.parse_args(["retrieval", "--embeddings", "local"]).embeddings == "local"

    def test_an_unknown_embedder_is_refused(self):
        parser = cli.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["retrieval", "--embeddings", "openai"])

    def test_the_factory_returns_a_deterministic_provider_for_fake(self):
        first = cli._retrieval_embedder("fake")
        second = cli._retrieval_embedder("fake")
        question = "how many orders per city"
        assert first.embed_query(question) == second.embed_query(question)
        assert "fake" in first.version.key


class TestTheReportIsWritten:
    def test_out_produces_both_artefacts(self, tmp_path, offline):
        assert _run(tmp_path) == 0
        assert (tmp_path / "unit.json").is_file()
        assert (tmp_path / "unit.md").is_file()

    def test_without_out_nothing_is_written(self, tmp_path, offline):
        parser = cli.build_parser()
        args = parser.parse_args(["retrieval", "--dataset", "retail", "--limit", "3"])
        assert args.handler(args) == 0
        assert list(tmp_path.iterdir()) == []

    def test_the_json_carries_what_makes_the_number_citable(self, tmp_path, offline):
        _run(tmp_path)
        payload = json.loads((tmp_path / "unit.json").read_text(encoding="utf-8"))

        # Without these a recall figure is a rumour: you cannot tell what produced it.
        assert payload["k"] == 8
        assert payload["embeddings"] == "fake"
        assert payload["embedding_version"].startswith("fake:")
        assert payload["cases_scored"] > 0
        assert payload["table_recall"]["denominator"] > 0
        assert payload["reproduce"].startswith("uv run python -m app.evaluation.cli retrieval")
        assert payload["environment"]

    def test_it_states_plainly_that_it_does_not_measure_the_model(self, tmp_path, offline):
        _run(tmp_path)
        payload = json.loads((tmp_path / "unit.json").read_text(encoding="utf-8"))
        assert payload["measures_model"] is False

        markdown = (tmp_path / "unit.md").read_text(encoding="utf-8")
        assert "Measures model quality | no" in markdown

    def test_a_fake_run_says_it_is_not_measuring_semantic_retrieval(self, tmp_path, offline):
        _run(tmp_path)
        payload = json.loads((tmp_path / "unit.json").read_text(encoding="utf-8"))
        assert "lexical" in payload["caveat"]
        assert payload["caveat"] in (tmp_path / "unit.md").read_text(encoding="utf-8")

    def test_the_reproduce_command_names_the_dataset_and_embedder(self, tmp_path, offline):
        _run(tmp_path)
        reproduce = json.loads((tmp_path / "unit.json").read_text(encoding="utf-8"))["reproduce"]
        assert "--dataset retail" in reproduce
        assert "--embeddings fake" in reproduce

    def test_per_dataset_rows_are_broken_out(self, tmp_path, offline):
        _run(tmp_path)
        payload = json.loads((tmp_path / "unit.json").read_text(encoding="utf-8"))
        assert [d["dataset"] for d in payload["per_dataset"]] == ["retail"]
        assert payload["per_dataset"][0]["cases"] > 0

    def test_the_label_names_the_files(self, tmp_path, offline):
        _run(tmp_path)
        assert {p.stem for p in tmp_path.iterdir()} == {"unit"}


class TestTheCommittedReportsStayHonest:
    """The reports under evaluation/results are cited; they must keep their provenance."""

    @pytest.mark.parametrize(
        "name",
        ["retrieval-retail-fake-k8", "retrieval-retail-local-k8", "retrieval-all-local-k8"],
    )
    def test_each_committed_report_carries_its_provenance(self, name):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "evaluation" / "results"
        payload = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))

        assert payload["measures_model"] is False
        assert payload["embedding_version"]
        assert payload["k"] > 0
        assert payload["cases_scored"] > 0
        assert payload["reproduce"]
        assert (root / f"{name}.md").is_file()
