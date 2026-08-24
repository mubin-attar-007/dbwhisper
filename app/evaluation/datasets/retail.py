"""A synthetic retail database: orders, revenue, returns, inventory, discounts and time.

**All data here is fabricated.** Customer names are drawn from a fixed pool of invented names, no
value corresponds to a real person, company or transaction, and nothing was copied from a production
system. The shape is what matters: this is the schema an analyst actually has to reason about -
a fact table (``order_items``) reachable only through a header table (``orders``), a returns table
that hangs off the line item rather than the order, an inventory table with no time dimension at all,
and a calendar table that exists precisely so a question about "Q3" has a right answer.

Deliberate awkwardnesses, because a fixture without them flatters the pipeline:

* revenue is ``quantity * unit_price`` on the *line item*, not a column - a query that sums
  ``products.list_price`` is wrong, and looks plausible;
* ``order_items.unit_price`` differs from ``products.list_price`` where a discount applied, so the
  two ways of computing revenue genuinely disagree;
* ``orders.status`` includes ``cancelled``, which must be excluded from revenue but not from order
  counts - the single most common source of a confidently wrong answer;
* refunds live in ``product_returns.refund_amount`` and are not netted off anywhere;
* the table is called ``product_returns`` rather than ``returns`` because ``RETURNS`` is a reserved
  word in several dialects and the fixture should not be testing the parser.

Sizes are small enough to build in well under a second and large enough that a ``GROUP BY`` has
something to say: 6 regions, 60 products, 200 customers, ~900 orders, ~2,400 line items.
"""

from __future__ import annotations

from random import Random

from app.evaluation.datasets.spec import (
    Column,
    DatasetSpec,
    Population,
    Relationship,
    Table,
    date_series,
    money,
    weighted_choice,
)

SEED = 20260824
START_DATE = "2024-01-01"
DAYS = 731  # 2024-01-01 .. 2025-12-31

REGIONS: tuple[tuple[int, str, str], ...] = (
    (1, "North", "Atlantia"),
    (2, "South", "Atlantia"),
    (3, "East", "Borealis"),
    (4, "West", "Borealis"),
    (5, "Central", "Caldera"),
    (6, "Islands", "Caldera"),
)

CATEGORIES: tuple[str, ...] = ("Electronics", "Furniture", "Stationery", "Outdoor", "Kitchen")
SEGMENTS: tuple[str, ...] = ("consumer", "smb", "enterprise")
CHANNELS: tuple[str, ...] = ("web", "retail", "partner")
ORDER_STATUS: tuple[tuple[str, float], ...] = (
    ("completed", 0.68),
    ("shipped", 0.16),
    ("pending", 0.09),
    ("cancelled", 0.07),
)
RETURN_REASONS: tuple[str, ...] = ("damaged", "wrong item", "changed mind", "late delivery")
WAREHOUSES: tuple[str, ...] = ("WH-NORTH", "WH-SOUTH")

_FIRST_NAMES = (
    "Ava",
    "Liam",
    "Noah",
    "Mia",
    "Kai",
    "Zara",
    "Omar",
    "Ines",
    "Theo",
    "Nina",
    "Ravi",
    "Lena",
    "Ivan",
    "Sofia",
    "Milo",
    "Ada",
    "Jonas",
    "Priya",
    "Elin",
    "Hugo",
)
_LAST_NAMES = (
    "Arden",
    "Bramble",
    "Calder",
    "Dunmore",
    "Ellery",
    "Fairholm",
    "Granger",
    "Hollis",
    "Ivers",
    "Jarrow",
    "Keswick",
    "Lindqvist",
    "Marchetti",
    "Norbury",
    "Oakes",
    "Pemberton",
)
_PRODUCT_NOUNS = (
    "Desk Lamp",
    "Office Chair",
    "Notebook",
    "Wireless Mouse",
    "Standing Desk",
    "Kettle",
    "Backpack",
    "Water Bottle",
    "Monitor Stand",
    "Keyboard",
    "Tent",
    "Cutting Board",
)
_PRODUCT_QUALIFIERS = ("Basic", "Pro", "Compact", "Deluxe", "Field")


def _tables() -> tuple[Table, ...]:
    return (
        Table(
            name="regions",
            description="Sales regions. One row per region; regions group customers by geography.",
            columns=(
                Column("region_id", "INTEGER"),
                Column("region_name", "TEXT", description="North, South, East, West, ..."),
                Column("country", "TEXT"),
            ),
            primary_key=("region_id",),
            keywords=("region", "geography", "territory", "country"),
        ),
        Table(
            name="customers",
            description=(
                "People and businesses that place orders. One row per customer, with the region "
                "they belong to, the date they signed up and their segment."
            ),
            columns=(
                Column("customer_id", "INTEGER"),
                Column("customer_name", "TEXT"),
                Column("region_id", "INTEGER"),
                Column("signup_date", "TEXT", description="ISO date the customer registered"),
                Column("segment", "TEXT", description="consumer, smb or enterprise"),
                Column("is_active", "INTEGER", description="1 when the account is still open"),
            ),
            primary_key=("customer_id",),
            keywords=("customer", "account", "buyer", "segment", "signup"),
        ),
        Table(
            name="products",
            description=(
                "Catalogue of items for sale, with a category, the cost to us and the list price. "
                "List price is the sticker price; what a customer actually paid is on the order line."
            ),
            columns=(
                Column("product_id", "INTEGER"),
                Column("product_name", "TEXT"),
                Column("category", "TEXT"),
                Column("unit_cost", "REAL", description="what the item costs us"),
                Column("list_price", "REAL", description="undiscounted catalogue price"),
                Column("is_discontinued", "INTEGER"),
            ),
            primary_key=("product_id",),
            keywords=("product", "item", "sku", "catalogue", "category", "price"),
        ),
        Table(
            name="discounts",
            description=(
                "Time-boxed promotional discounts on a product. A discount applies only between "
                "starts_on and ends_on inclusive."
            ),
            columns=(
                Column("discount_id", "INTEGER"),
                Column("product_id", "INTEGER"),
                Column("discount_pct", "REAL", description="fraction off, 0.10 = 10%"),
                Column("starts_on", "TEXT"),
                Column("ends_on", "TEXT"),
                Column("campaign", "TEXT"),
            ),
            primary_key=("discount_id",),
            keywords=("discount", "promotion", "campaign", "markdown", "offer"),
        ),
        Table(
            name="orders",
            description=(
                "Order headers: who ordered, when, through which channel and the fulfilment "
                "status. Cancelled orders still have rows and must be excluded from revenue."
            ),
            columns=(
                Column("order_id", "INTEGER"),
                Column("customer_id", "INTEGER"),
                Column("order_date", "TEXT"),
                Column("status", "TEXT", description="completed, shipped, pending or cancelled"),
                Column("channel", "TEXT", description="web, retail or partner"),
            ),
            primary_key=("order_id",),
            keywords=("order", "purchase", "sale", "transaction", "status", "channel"),
        ),
        Table(
            name="order_items",
            description=(
                "One row per product on an order. Line revenue is quantity * unit_price; "
                "unit_price is what was charged, which is below list price when a discount applied."
            ),
            columns=(
                Column("order_item_id", "INTEGER"),
                Column("order_id", "INTEGER"),
                Column("product_id", "INTEGER"),
                Column("quantity", "INTEGER"),
                Column("unit_price", "REAL", description="price actually charged per unit"),
            ),
            primary_key=("order_item_id",),
            keywords=("line item", "revenue", "quantity", "unit price", "basket"),
        ),
        Table(
            name="product_returns",
            description=(
                "Returned line items with the refund paid. A return points at an order line, so a "
                "return is only reachable from an order through order_items."
            ),
            columns=(
                Column("return_id", "INTEGER"),
                Column("order_item_id", "INTEGER"),
                Column("return_date", "TEXT"),
                Column("reason", "TEXT"),
                Column("refund_amount", "REAL"),
            ),
            primary_key=("return_id",),
            keywords=("return", "refund", "rma", "chargeback", "reason"),
        ),
        Table(
            name="inventory",
            description=(
                "Current stock per product per warehouse. A snapshot with no history: it says what "
                "is on hand now, never what was on hand last month."
            ),
            columns=(
                Column("inventory_id", "INTEGER"),
                Column("product_id", "INTEGER"),
                Column("warehouse", "TEXT"),
                Column("on_hand", "INTEGER"),
                Column("reorder_point", "INTEGER"),
            ),
            primary_key=("inventory_id",),
            keywords=("inventory", "stock", "on hand", "warehouse", "reorder"),
        ),
        Table(
            name="calendar_days",
            description=(
                "One row per calendar date, with the month, quarter and fiscal year it belongs to. "
                "Join to it when a question asks about a named period rather than a date range."
            ),
            columns=(
                Column("calendar_date", "TEXT"),
                Column("day_of_week", "TEXT"),
                Column("month_name", "TEXT"),
                Column("month_number", "INTEGER"),
                Column("quarter", "TEXT", description="Q1..Q4"),
                Column("calendar_year", "INTEGER"),
                Column("is_weekend", "INTEGER"),
            ),
            primary_key=("calendar_date",),
            keywords=("calendar", "date", "month", "quarter", "year", "weekend", "period"),
        ),
    )


_RELATIONSHIPS = (
    Relationship("customers", ("region_id",), "regions", ("region_id",)),
    Relationship("orders", ("customer_id",), "customers", ("customer_id",)),
    Relationship("order_items", ("order_id",), "orders", ("order_id",)),
    Relationship("order_items", ("product_id",), "products", ("product_id",)),
    Relationship("product_returns", ("order_item_id",), "order_items", ("order_item_id",)),
    Relationship("discounts", ("product_id",), "products", ("product_id",)),
    Relationship("inventory", ("product_id",), "products", ("product_id",)),
    Relationship("orders", ("order_date",), "calendar_days", ("calendar_date",)),
)


def populate(rng: Random) -> Population:
    """Every row of the retail fixture, from one seeded generator and nothing else."""
    dates = date_series(START_DATE, DAYS)

    regions = [tuple(row) for row in REGIONS]

    customers: list[tuple] = []
    for index in range(1, 201):
        first = _FIRST_NAMES[rng.randrange(len(_FIRST_NAMES))]
        last = _LAST_NAMES[rng.randrange(len(_LAST_NAMES))]
        signup = dates[rng.randrange(0, 400)]
        customers.append(
            (
                index,
                f"{first} {last}",
                rng.randrange(1, len(REGIONS) + 1),
                signup,
                SEGMENTS[rng.randrange(len(SEGMENTS))],
                1 if rng.random() > 0.18 else 0,
            )
        )

    products: list[tuple] = []
    for index in range(1, 61):
        noun = _PRODUCT_NOUNS[index % len(_PRODUCT_NOUNS)]
        qualifier = _PRODUCT_QUALIFIERS[(index // len(_PRODUCT_NOUNS)) % len(_PRODUCT_QUALIFIERS)]
        cost = money(4 + rng.random() * 180)
        products.append(
            (
                index,
                f"{qualifier} {noun}",
                CATEGORIES[index % len(CATEGORIES)],
                cost,
                money(cost * (1.35 + rng.random() * 0.9)),
                1 if rng.random() > 0.9 else 0,
            )
        )

    discounts: list[tuple] = []
    for index in range(1, 41):
        product_id = rng.randrange(1, 61)
        start_offset = rng.randrange(0, DAYS - 40)
        length = rng.randrange(7, 35)
        discounts.append(
            (
                index,
                product_id,
                round(0.05 + rng.randrange(0, 6) * 0.05, 2),
                dates[start_offset],
                dates[start_offset + length],
                f"campaign-{1 + (index % 6)}",
            )
        )
    # Which products are ever discounted, so the line price can plausibly differ from list price.
    discounted_products = {row[1] for row in discounts}

    orders: list[tuple] = []
    order_items: list[tuple] = []
    line_id = 0
    for order_id in range(1, 901):
        customer_id = rng.randrange(1, 201)
        # Orders cluster in the later half of the window, as a growing business would.
        day = int(rng.triangular(0, DAYS - 1, DAYS * 0.72))
        status = weighted_choice(rng, ORDER_STATUS)
        orders.append(
            (
                order_id,
                customer_id,
                dates[min(day, DAYS - 1)],
                status,
                CHANNELS[rng.randrange(len(CHANNELS))],
            )
        )
        for _ in range(rng.randrange(1, 5)):
            line_id += 1
            product_id = rng.randrange(1, 61)
            list_price = products[product_id - 1][4]
            charged = (
                money(list_price * (1 - round(0.05 + rng.randrange(0, 4) * 0.05, 2)))
                if product_id in discounted_products and rng.random() < 0.45
                else list_price
            )
            order_items.append((line_id, order_id, product_id, rng.randrange(1, 6), charged))

    returns: list[tuple] = []
    return_id = 0
    for line in order_items:
        if rng.random() >= 0.075:
            continue
        order = orders[line[1] - 1]
        if order[3] == "cancelled":
            continue  # a cancelled order was never delivered, so it cannot be returned
        return_id += 1
        order_day = dates.index(order[2])
        return_day = min(order_day + rng.randrange(3, 40), DAYS - 1)
        returns.append(
            (
                return_id,
                line[0],
                dates[return_day],
                RETURN_REASONS[rng.randrange(len(RETURN_REASONS))],
                money(line[3] * line[4] * (0.5 + rng.random() * 0.5)),
            )
        )

    inventory: list[tuple] = []
    inventory_id = 0
    for product_id in range(1, 61):
        for warehouse in WAREHOUSES:
            inventory_id += 1
            inventory.append(
                (
                    inventory_id,
                    product_id,
                    warehouse,
                    rng.randrange(0, 400),
                    rng.randrange(10, 60),
                )
            )

    calendar: list[tuple] = []
    weekday_names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    month_names = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    for iso in dates:
        from datetime import date as _date

        year, month, day = (int(p) for p in iso.split("-"))
        weekday = _date(year, month, day).weekday()
        calendar.append(
            (
                iso,
                weekday_names[weekday],
                month_names[month - 1],
                month,
                f"Q{((month - 1) // 3) + 1}",
                year,
                1 if weekday >= 5 else 0,
            )
        )

    return {
        "regions": regions,
        "customers": customers,
        "products": products,
        "discounts": discounts,
        "orders": orders,
        "order_items": order_items,
        "product_returns": returns,
        "inventory": inventory,
        "calendar_days": calendar,
    }


SPEC = DatasetSpec(
    name="retail",
    source_id="eval_retail",
    version="1.0.0",
    description=(
        "Synthetic retail store: regions, customers, products, discounts, orders, order lines, "
        "returns, inventory and a calendar dimension."
    ),
    domain="retail",
    tables=_tables(),
    relationships=_RELATIONSHIPS,
    populate=populate,
    seed=SEED,
    verified_queries=(
        (
            "revenue by product category",
            "SELECT p.category, SUM(oi.quantity * oi.unit_price) AS revenue "
            "FROM order_items oi JOIN products p ON p.product_id = oi.product_id "
            "JOIN orders o ON o.order_id = oi.order_id WHERE o.status <> 'cancelled' "
            "GROUP BY p.category ORDER BY revenue DESC",
        ),
        (
            "how many orders were cancelled",
            "SELECT COUNT(*) AS cancelled_orders FROM orders WHERE status = 'cancelled'",
        ),
    ),
    synthetic=True,
    provenance_note=(
        "Fabricated data generated by app/evaluation/datasets/retail.py with seed "
        f"{SEED}. No row corresponds to a real customer, product or transaction."
    ),
    covered_concepts=(
        "revenue",
        "products",
        "orders",
        "returns",
        "customers",
        "regions",
        "inventory",
        "discounts",
        "time",
    ),
)

__all__ = ["SEED", "SPEC", "populate"]
