"""Reading structure out of SQL, and the three things that are easy to get wrong.

All three produce the same visible symptom - a phantom "hallucinated column" on a query that is
perfectly correct - so each has its own test:

* a select-list alias reused in ``GROUP BY`` / ``ORDER BY``;
* a column qualified by a CTE alias;
* a column qualified by a derived-table alias.
"""

from __future__ import annotations

from app.evaluation.sqlfacts import order_sensitive, sql_facts


def test_tables_and_columns_are_attributed_through_aliases() -> None:
    facts = sql_facts(
        "SELECT c.customer_name, o.order_date FROM customers c "
        "JOIN orders o ON o.customer_id = c.customer_id"
    )
    assert facts.parsed is True
    assert facts.tables == ("customers", "orders")
    assert facts.columns == (
        "customers.customer_id",
        "customers.customer_name",
        "orders.customer_id",
        "orders.order_date",
    )


def test_join_edges_are_sorted_so_both_directions_agree() -> None:
    left = sql_facts("SELECT 1 AS n FROM a JOIN b ON a.id = b.a_id")
    right = sql_facts("SELECT 1 AS n FROM b JOIN a ON b.a_id = a.id")
    assert left.join_edges == right.join_edges == ("a.id -> b.a_id",)


def test_a_select_alias_reused_in_group_by_is_not_a_column() -> None:
    facts = sql_facts(
        "SELECT channel, COUNT(*) AS order_count FROM orders "
        "GROUP BY channel ORDER BY order_count DESC"
    )
    assert facts.columns == ("orders.channel",)


def test_a_column_qualified_by_a_cte_alias_is_not_a_schema_reference() -> None:
    """Its source columns are validated where the CTE is defined; counting it twice invents one."""
    facts = sql_facts(
        "WITH totals AS (SELECT oi.order_id AS order_id, SUM(oi.quantity) AS units "
        "FROM order_items oi GROUP BY oi.order_id) "
        "SELECT AVG(t.units) AS average_units FROM totals t"
    )
    assert facts.tables == ("order_items",)
    assert facts.ctes == ("totals",)
    assert all(column.startswith("order_items.") for column in facts.columns)


def test_a_column_qualified_by_a_derived_table_alias_is_not_a_schema_reference() -> None:
    facts = sql_facts("SELECT s.n FROM (SELECT COUNT(*) AS n FROM customers) s")
    assert facts.tables == ("customers",)
    assert facts.columns == ()


def test_unqualified_columns_in_a_single_table_query_get_their_table() -> None:
    facts = sql_facts("SELECT city FROM customers WHERE is_active = 1")
    assert facts.columns == ("customers.city", "customers.is_active")
    assert facts.unqualified_columns == ()


def test_unqualified_columns_in_a_join_are_reported_as_unattributed() -> None:
    facts = sql_facts("SELECT name FROM customers c JOIN orders o ON o.customer_id = c.customer_id")
    assert "name" in facts.unqualified_columns


def test_shape_flags_reflect_the_statement() -> None:
    facts = sql_facts("SELECT a, COUNT(*) AS n FROM t GROUP BY a ORDER BY n DESC LIMIT 5")
    assert facts.has_group_by is True
    assert facts.has_order_by is True
    assert facts.has_aggregate is True
    assert facts.has_limit is True


def test_order_sensitivity_comes_from_the_reference_query() -> None:
    assert order_sensitive("SELECT a FROM t ORDER BY a") is True
    assert order_sensitive("SELECT a FROM t") is False


def test_unparseable_sql_degrades_instead_of_raising() -> None:
    """A fact we could not extract must degrade a metric visibly, never abort a run."""
    facts = sql_facts("this is not sql at all ((((")
    assert facts.parsed is False
    assert facts.parse_error
    assert facts.tables == ()


def test_empty_sql_is_reported_as_unparsed() -> None:
    facts = sql_facts("")
    assert facts.parsed is False
    assert facts.parse_error == "empty statement"


def test_an_unknown_dialect_name_falls_back_instead_of_raising() -> None:
    facts = sql_facts("SELECT 1 AS n FROM t", dialect="cockroach")
    assert facts.parsed is True
