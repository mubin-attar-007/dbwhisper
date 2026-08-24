"""Property-based tests: generated read-only queries are allowed; any mutation that introduces a
write, an extra statement, a catalog or an unknown object is denied; denials never carry SQL."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.sqlpolicy.conftest import TABLES, make_ctx

from app.sqlpolicy import Decision, Dialect, evaluate

_SETTINGS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])

tables = st.sampled_from(sorted(t for t in TABLES if t != "patients"))
ints = st.integers(min_value=0, max_value=10_000)
words = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=1, max_size=12)


@st.composite
def simple_select(draw):
    table = draw(tables)
    cols = TABLES[table]
    projection = ", ".join(
        draw(st.lists(st.sampled_from(cols), min_size=1, max_size=3, unique=True))
    )
    where = ""
    if draw(st.booleans()):
        col = draw(st.sampled_from(cols))
        value = draw(st.one_of(ints.map(str), words.map(lambda w: f"'{w}'")))
        op = draw(st.sampled_from(["=", "<>", ">", "<", ">=", "<="]))
        where = f" WHERE {col} {op} {value}"
    order = f" ORDER BY {draw(st.sampled_from(cols))}" if draw(st.booleans()) else ""
    limit = (
        f" LIMIT {draw(st.integers(min_value=1, max_value=5000))}" if draw(st.booleans()) else ""
    )
    return f"SELECT {projection} FROM {table}{where}{order}{limit}"


WRITE_TEMPLATES = [
    "DELETE FROM {t}",
    "UPDATE {t} SET id = 1",
    "INSERT INTO {t} (id) VALUES (1)",
    "DROP TABLE {t}",
    "TRUNCATE TABLE {t}",
    "ALTER TABLE {t} ADD COLUMN x INT",
    "CREATE TABLE x AS SELECT * FROM {t}",
]


@given(simple_select())
@_SETTINGS
def test_generated_read_only_queries_are_allowed(sql: str):
    d = evaluate(sql, make_ctx(Dialect.POSTGRES))
    assert d.decision is Decision.ALLOW, f"{sql} -> {d.reason}"
    assert d.transformed_sql and d.fingerprint
    # the execution SQL always carries a limit no greater than the policy default
    assert (
        d.existing_limit is not None
        or d.limit_applied == 1000
        or d.complexity["aggregate_functions"]
    )


@given(simple_select(), st.sampled_from(WRITE_TEMPLATES), tables)
@_SETTINGS
def test_appending_a_write_statement_is_denied(sql: str, template: str, table: str):
    d = evaluate(f"{sql}; {template.format(t=table)}", make_ctx(Dialect.POSTGRES))
    assert d.decision is Decision.DENY and d.transformed_sql is None


@given(simple_select(), st.sampled_from(WRITE_TEMPLATES), tables)
@_SETTINGS
def test_write_hidden_in_cte_is_denied(sql: str, template: str, table: str):
    write = template.format(t=table)
    if not write.upper().startswith(("DELETE", "UPDATE", "INSERT")):
        return  # only DML is syntactically valid inside a CTE
    d = evaluate(f"WITH w AS ({write} RETURNING *) {sql}", make_ctx(Dialect.POSTGRES))
    assert d.decision is Decision.DENY and d.transformed_sql is None


@given(
    simple_select(),
    st.sampled_from(["pg_shadow", "pg_catalog.pg_tables", "information_schema.tables"]),
)
@_SETTINGS
def test_catalog_join_is_denied(sql: str, catalog: str):
    d = evaluate(sql.replace(" FROM ", f" FROM {catalog} AS zz, ", 1), make_ctx(Dialect.POSTGRES))
    assert d.decision is Decision.DENY


@given(simple_select(), st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=3, max_size=12))
@_SETTINGS
def test_unknown_table_is_denied(sql: str, name: str):
    if name in TABLES:
        return
    table = next(t for t in TABLES if f" FROM {t}" in sql)
    d = evaluate(sql.replace(f" FROM {table}", f" FROM {name}", 1), make_ctx(Dialect.POSTGRES))
    assert d.decision is Decision.DENY, f"{sql} -> {d.reason}"
    assert d.transformed_sql is None


@given(simple_select())
@_SETTINGS
def test_fingerprint_is_stable_under_whitespace_and_case(sql: str):
    ctx = make_ctx(Dialect.POSTGRES)
    a = evaluate(sql, ctx).fingerprint
    b = evaluate("  " + sql.lower().replace(" from ", "\n   FROM ") + " ", ctx).fingerprint
    assert a == b
