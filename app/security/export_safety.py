"""CSV formula injection: making an exported cell inert in a spreadsheet without changing its value.

``/query`` and ``/run_sql`` return a ``csv`` field that the UI offers as a download
(``app/core/result_formatter.py:123``). The rows in it came out of somebody's database, and a
database is a place attackers put things. Excel, LibreOffice Calc and Google Sheets evaluate any
cell whose first character is ``=``, ``+``, ``-``, ``@``, TAB or CR - so a row containing
``=cmd|'/c calc'!A0`` is a live command the moment an analyst opens the file. The application is
never compromised; the analyst's workstation is. This is a real, repeatedly-exploited class (CWE-1236)
and the export is where it has to be stopped, because by then the value is legitimately in the data.

**The mechanism.** A cell that would be interpreted is prefixed with a single apostrophe, the
spreadsheet convention for "treat the rest as text". Two properties make that safe to do here:

* **It is reversible.** :func:`restore_cell` undoes :func:`neutralize_cell`, including for values
  that already begin with an apostrophe - those are escaped too, which is what makes the inverse
  unambiguous. ``tests/test_export_safety.py`` asserts the round trip over a Hypothesis
  strategy as well as a fixed corpus. A consumer that knows about the convention gets the original
  bytes back; a consumer that does not sees one leading quote instead of executing a formula.
* **It is narrow.** ``-`` and ``+`` lead almost every negative number and signed value in a real
  result set. Escaping ``-42`` would turn a numeric column into text and break every chart built on
  the export - a correctness bug introduced by a security control, which is how security controls get
  switched off. So ``-``/``+`` are escaped only when the value is *not* a number
  (:func:`_looks_numeric`). ``=``, ``@``, TAB and CR are always escaped: none of them begins a
  legitimate scalar.

Note what this does **not** do. It does not sanitise the value for HTML, shells or SQL; it addresses
one sink. And it does not defend a consumer that strips leading apostrophes before importing.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# The characters a spreadsheet treats as "a formula starts here".
#   =  formula        +  formula (Excel)      -  formula / negative
#   @  legacy 1-2-3 function syntax, still honoured by Excel
#   \t \r  field/line separators that let a payload continue into the next parsed cell
FORMULA_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")

# The escape character, and the character sequence a restore must recognise.
ESCAPE = "'"

# Leading characters ignored when deciding whether a cell starts with a formula prefix. A BOM or a
# leading space is trimmed by spreadsheet importers before evaluation, so " =1+1" is still a formula.
# TAB and CR are deliberately absent: they are triggers in their own right.
_IGNORABLE_LEAD = " \u00a0\ufeff"

# A value that is unambiguously a number: optional sign, digits, optional decimal and exponent.
# Thousands separators are excluded on purpose - "-1,234" is not something a spreadsheet parses the
# same way everywhere, so it is treated as text and escaped.
_NUMERIC = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _looks_numeric(value: str) -> bool:
    """True for a plain signed number, which is why ``-42`` is left alone and ``-1+1`` is not."""
    return bool(_NUMERIC.match(value.strip()))


def _lead(value: str) -> str:
    """The first significant character, skipping a BOM or leading spaces."""
    stripped = value.lstrip(_IGNORABLE_LEAD)
    return stripped[:1]


def is_formula(value: str) -> bool:
    """Would a spreadsheet evaluate this cell rather than display it?

    Signed numbers are excluded: ``-42`` and ``+3.5e2`` are values, not expressions.
    """
    head = _lead(value)
    if not head or head not in FORMULA_PREFIXES:
        return False
    return not (head in ("-", "+") and _looks_numeric(value))


def _needs_escape(value: str) -> bool:
    """Would :func:`neutralize_cell` add an escape to this value?

    A cell is escaped when it would evaluate, and also when it is an *already-escaped* value - a run
    of apostrophes followed by something that would evaluate. That second clause is what makes
    :func:`restore_cell` a true inverse: ``'=1+1`` has to become ``''=1+1`` so that stripping one
    apostrophe recovers it, while ``'tis`` is left alone because nothing would have escaped it.
    Written as a loop rather than a recursion: a cell of ten thousand apostrophes is unlikely, but
    a stack overflow on a data value is not an acceptable way to find that out.
    """
    index = 0
    while index < len(value) and value[index] == ESCAPE:
        index += 1
    return is_formula(value[index:]) if index else is_formula(value)


def neutralize_cell(value: Any) -> Any:
    """Return a cell that a spreadsheet will display rather than evaluate.

    Non-string values are returned unchanged: an ``int``, ``Decimal`` or ``datetime`` cannot carry a
    formula, and stringifying them here would fight with the serialisation the caller chose.
    """
    if not isinstance(value, str) or not value:
        return value
    return ESCAPE + value if _needs_escape(value) else value


def restore_cell(value: Any) -> Any:
    """Exact inverse of :func:`neutralize_cell`.

    Strips one leading apostrophe when - and only when - the remainder is something that would have
    been escaped. A cell that merely happens to start with an apostrophe (``'tis``) is left alone,
    because :func:`neutralize_cell` would not have touched it either. ``tests/test_export_safety.py``
    asserts the inverse over arbitrary text with Hypothesis.
    """
    if not isinstance(value, str) or not value.startswith(ESCAPE):
        return value
    remainder = value[1:]
    return remainder if _needs_escape(remainder) else value


def neutralize_row(row: Sequence[Any]) -> list[Any]:
    return [neutralize_cell(cell) for cell in row]


def restore_row(row: Sequence[Any]) -> list[Any]:
    return [restore_cell(cell) for cell in row]


@dataclass(frozen=True, slots=True)
class SafeCsv:
    """A CSV export plus what the control actually did to produce it.

    ``cells_neutralized`` is not decoration: it is the number that goes into the
    ``data.exported`` audit event, and a sudden non-zero count on a table that has never had one is
    a signal worth seeing.
    """

    text: str
    cells_neutralized: int
    headers_neutralized: int

    @property
    def changed(self) -> bool:
        return bool(self.cells_neutralized or self.headers_neutralized)


def to_safe_csv(
    columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    line_terminator: str = "\n",
) -> SafeCsv:
    """Serialise a result set to CSV with every executable cell neutralised.

    Column names are neutralised too. They come from the same database as the rows - a column can be
    named ``=HYPERLINK("http://x")`` in PostgreSQL - and the header row is the first thing a
    spreadsheet parses.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator=line_terminator)

    safe_headers = [neutralize_cell(str(c)) for c in columns]
    headers_changed = sum(
        1 for c, safe in zip(columns, safe_headers, strict=True) if str(c) != safe
    )
    writer.writerow(safe_headers)

    cells_changed = 0
    for row in rows:
        safe_row = neutralize_row(row)
        # Compare only strings: a float NaN is unequal to itself and would inflate the count.
        cells_changed += sum(
            1
            for cell, safe in zip(row, safe_row, strict=True)
            if isinstance(cell, str) and cell != safe
        )
        writer.writerow(safe_row)

    return SafeCsv(
        text=buffer.getvalue(),
        cells_neutralized=cells_changed,
        headers_neutralized=headers_changed,
    )


def scan_rows(rows: Iterable[Sequence[Any]]) -> int:
    """Count cells that *would* be neutralised, without producing an export.

    Useful for reporting on a result set (or an enrolled table) without materialising a CSV.
    """
    return sum(1 for row in rows for cell in row if isinstance(cell, str) and is_formula(cell))


__all__ = [
    "ESCAPE",
    "FORMULA_PREFIXES",
    "SafeCsv",
    "is_formula",
    "neutralize_cell",
    "neutralize_row",
    "restore_cell",
    "restore_row",
    "scan_rows",
    "to_safe_csv",
]
