"""Properties of the generated databases and the committed case corpus.

The two that matter most:

* **The generators are deterministic.** Building twice must produce the same bytes. Without that,
  "the reference result for case X" is not a fixed thing and no comparison against it means anything.
* **The healthcare dataset contains nothing that could be mistaken for a real record.** That is
  checked mechanically here rather than left to a docstring: no column named like an identifier this
  fixture must not hold, no value shaped like an SSN, a date of birth, an ICD code or an NPI.
"""

from __future__ import annotations

import re

import pytest

from app.evaluation.datasets import SPECS, SPECS_BY_NAME, dataset_names, get_spec
from app.evaluation.datasets.build import (
    build_documents,
    fixture_digest,
    row_counts,
    run_readonly,
    write_schema_index,
)
from app.evaluation.datasets.synthetic_snf import SPEC as SNF_SPEC
from app.evaluation.runner import load_dataset_for
from app.evaluation.schema import ExpectedBehavior

#: The coverage floors the evaluation brief sets, checked across the whole corpus.
COVERAGE_MINIMUMS = {
    "answerable": 100,
    "multi_table": 30,
    "time_range": 20,
    "aggregation": 20,
    "conversational": 15,
}
MINIMUM_AMBIGUOUS_CASES = 15


# ---------------------------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------------------------


def test_fixture_bytes_are_identical_across_two_builds(spec) -> None:
    """Same seed, same bytes. This is what makes a reference result a fixed thing."""
    assert fixture_digest(spec) == fixture_digest(spec)


def test_generation_is_deterministic_before_it_reaches_sqlite(spec) -> None:
    """Determinism is a property of the generator, not of SQLite's page allocation."""
    assert spec.generate() == spec.generate()


def test_row_counts_are_non_trivial(spec) -> None:
    counts = row_counts(spec)
    assert set(counts) == set(spec.table_names)
    assert all(count > 0 for count in counts.values()), f"empty tables: {counts}"
    assert sum(counts.values()) > 1000, "a fixture this small cannot make a GROUP BY interesting"


# ---------------------------------------------------------------------------------------------
# The spec, the fixture and the schema index must agree
# ---------------------------------------------------------------------------------------------


def test_fixture_columns_match_the_spec(spec) -> None:
    for table in spec.tables:
        columns, _rows = run_readonly(spec, f"SELECT * FROM {table.name} LIMIT 1")
        assert tuple(columns) == table.column_names, f"{spec.name}.{table.name} drifted"


def test_schema_index_describes_the_same_tables(spec) -> None:
    import yaml

    path = write_schema_index(spec)
    body = path.read_text(encoding="utf-8")
    assert "GENERATED FILE" in body, "the enrollment artefact must say it is generated"
    data = yaml.safe_load(body)
    indexed = {entry["table"]: tuple(entry["column_names"]) for entry in data["tables"]}
    assert indexed == {t.name: t.column_names for t in spec.tables}


def test_writing_the_schema_index_twice_does_not_touch_the_file(spec) -> None:
    """``scope_from_schema_index`` caches on mtime; a pointless rewrite would thrash that cache."""
    first = write_schema_index(spec)
    before = first.stat().st_mtime_ns
    second = write_schema_index(spec)
    assert second.stat().st_mtime_ns == before


def test_relationships_reference_real_tables_and_columns(spec) -> None:
    columns = spec.columns_by_table()
    for relationship in spec.relationships:
        assert relationship.from_table in columns, relationship.label()
        assert relationship.to_table in columns, relationship.label()
        for column in relationship.from_columns:
            assert column in columns[relationship.from_table], relationship.label()
        for column in relationship.to_columns:
            assert column in columns[relationship.to_table], relationship.label()


def test_retrieval_documents_cover_every_table_and_column(spec) -> None:
    documents = build_documents(spec)
    tables = {d.table for d in documents if d.kind.value == "table"}
    assert tables == set(spec.table_names)
    columns = {f"{d.table}.{d.column}" for d in documents if d.kind.value == "column" and d.column}
    expected = {f"{t.name}.{c}" for t in spec.tables for c in t.column_names}
    assert columns == expected


def test_verified_queries_run(spec) -> None:
    """The seeded 'human-approved' examples must not be broken SQL - the model is shown them."""
    for _question, sql in spec.verified_queries:
        columns, _rows = run_readonly(spec, sql)
        assert columns


# ---------------------------------------------------------------------------------------------
# The synthetic healthcare dataset carries no real-looking identifiers
# ---------------------------------------------------------------------------------------------

_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_FULL_DATE_OF_BIRTH = re.compile(r"\b(19|20)\d\d-\d\d-\d\d\b")
_ICD10 = re.compile(r"\b[A-TV-Z]\d\d(?:\.\d{1,4})?\b")
_NPI_LIKE = re.compile(r"\b\d{10}\b")

_FORBIDDEN_COLUMN_WORDS = (
    "ssn",
    "social_security",
    "mrn",
    "medical_record",
    "npi",
    "date_of_birth",
    "dob",
    "email",
    "phone",
    "address",
    "insurance_number",
    "member_id",
)


def test_synthetic_healthcare_schema_holds_no_identifier_columns() -> None:
    for table in SNF_SPEC.tables:
        for column in table.column_names:
            lowered = column.lower()
            for forbidden in _FORBIDDEN_COLUMN_WORDS:
                assert forbidden not in lowered, (
                    f"synthetic_snf.{table.name}.{column} looks like a real identifier column"
                )


def test_synthetic_healthcare_values_are_not_real_looking() -> None:
    """Scan every text value in the fixture for anything shaped like a real-world identifier."""
    for table in SNF_SPEC.tables:
        columns, rows = run_readonly(SNF_SPEC, f"SELECT * FROM {table.name}")
        for row in rows:
            for column_name, value in zip(columns, row, strict=True):
                if not isinstance(value, str):
                    continue
                where = f"synthetic_snf.{table.name}.{column_name} = {value!r}"
                assert not _SSN.search(value), f"SSN-shaped value in {where}"
                assert not _NPI_LIKE.search(value), f"NPI-shaped value in {where}"
                if column_name not in {
                    "admitted_on",
                    "discharged_on",
                    "census_date",
                    "recorded_on",
                    "assessment_date",
                    "claim_date",
                    "session_date",
                    "incident_date",
                    "first_admitted_on",
                }:
                    assert not _FULL_DATE_OF_BIRTH.search(value), f"date-shaped value in {where}"
                if column_name == "diagnosis_group":
                    assert not _ICD10.match(value), f"code-shaped diagnosis in {where}"


def test_synthetic_healthcare_identifiers_are_prefixed() -> None:
    _columns, rows = run_readonly(SNF_SPEC, "SELECT resident_code FROM residents LIMIT 5")
    assert all(str(row[0]).startswith("SYNTH-R-") for row in rows)
    _columns, claims = run_readonly(SNF_SPEC, "SELECT claim_code FROM claims LIMIT 5")
    assert all(str(row[0]).startswith("SYNTH-CLM-") for row in claims)


def test_synthetic_healthcare_is_labelled_synthetic_everywhere() -> None:
    assert SNF_SPEC.synthetic is True
    assert "SYNTHETIC" in SNF_SPEC.provenance_note.upper()
    assert "SYNTHETIC" in SNF_SPEC.description.upper()
    assert SNF_SPEC.schema_index()["synthetic"] is True

    from app.evaluation.datasets import synthetic_snf

    assert "fabricated" in (synthetic_snf.__doc__ or "").lower()

    dataset = load_dataset_for(SNF_SPEC)
    assert dataset.synthetic is True


def test_every_dataset_declares_itself_synthetic() -> None:
    for spec in SPECS:
        assert spec.synthetic is True
        assert spec.provenance_note, f"{spec.name} has no provenance note"


# ---------------------------------------------------------------------------------------------
# Corpus coverage
# ---------------------------------------------------------------------------------------------


def test_registry_is_consistent() -> None:
    assert set(dataset_names()) == set(SPECS_BY_NAME)
    for name in dataset_names():
        assert get_spec(name) is SPECS_BY_NAME[name]
        assert get_spec(get_spec(name).source_id) is SPECS_BY_NAME[name]
    with pytest.raises(KeyError):
        get_spec("not-a-dataset")


def test_source_ids_cannot_collide_with_a_real_enrolled_database() -> None:
    for spec in SPECS:
        assert spec.source_id.startswith("eval_"), (
            "evaluation databases write a schema index into database_schemas/; the prefix is what "
            "keeps them from shadowing a real enrolled database"
        )


@pytest.mark.parametrize(("tag", "minimum"), sorted(COVERAGE_MINIMUMS.items()))
def test_corpus_meets_the_coverage_minimum(all_cases, tag: str, minimum: int) -> None:
    count = sum(1 for case in all_cases if case.has_tag(tag))
    assert count >= minimum, f"only {count} case(s) tagged '{tag}'; the brief requires {minimum}"


def test_corpus_has_enough_ambiguous_cases(all_cases) -> None:
    ambiguous = [c for c in all_cases if c.expected_behavior is ExpectedBehavior.CLARIFY]
    assert len(ambiguous) >= MINIMUM_AMBIGUOUS_CASES
    for case in ambiguous:
        assert case.expected_clarification, f"{case.id} has no expected clarification phrases"


def test_case_ids_are_unique_across_the_whole_corpus(all_cases) -> None:
    ids = [case.id for case in all_cases]
    assert len(ids) == len(set(ids))


def test_every_case_names_a_dataset_that_exists(all_cases) -> None:
    for case in all_cases:
        spec = get_spec(case.dataset)
        assert case.database == spec.source_id
        assert case.dialect == spec.dialect


def test_safety_cases_declare_a_category(all_cases) -> None:
    for case in all_cases:
        if case.expected_behavior is ExpectedBehavior.REFUSE:
            assert case.safety_category is not None
            assert case.expected_refusal, f"{case.id} does not say what a good refusal looks like"


def test_conversational_cases_carry_prior_turns(all_cases) -> None:
    conversational = [c for c in all_cases if c.has_tag("conversational")]
    assert conversational
    for case in conversational:
        assert case.is_conversational, f"{case.id} is tagged conversational but has no context"
        rendered = case.contextual_question()
        assert case.question in rendered
        assert "Earlier in this conversation" in rendered
