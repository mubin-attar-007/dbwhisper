"""A synthetic B2B SaaS operations database: orgs, seats, MRR, churn, expansion and support.

**All data here is fabricated.** Organisation names are invented, user identity is stored only as an
opaque ``user_code`` (there are no names and no email addresses anywhere in this schema), and no
figure was taken from a real company.

The domain is chosen because SaaS metrics are *definitionally* ambiguous, which is what makes it
worth evaluating on:

* "churn" can mean logo churn (orgs that cancelled) or revenue churn (MRR lost) - ``churn_events``
  records both, so a question that says only "churn" genuinely has two right answers and belongs in
  the clarification set;
* "active users" can mean users with a recent ``last_seen_on`` or users who produced an event in a
  window, and the two do not agree;
* MRR lives in three places - the current ``subscriptions.mrr``, the ``expansions.mrr_added`` history
  and ``churn_events.mrr_lost`` - so a naive sum double-counts.

Structural facts a query has to respect: a subscription with ``ended_on IS NULL`` is still live;
``events.event_count`` is a daily roll-up, not one row per action, so counting rows is not counting
events; ``support_tickets.closed_on`` is null while a ticket is open.

Sizes: 5 plans, 120 orgs, 700 users, ~150 subscriptions, ~3,000 event roll-ups, 400 tickets.
"""

from __future__ import annotations

from random import Random

from app.evaluation.datasets.spec import (
    Column,
    DatasetSpec,
    Population,
    Relationship,
    Table,
    code,
    date_series,
    money,
    weighted_choice,
)

SEED = 20260825
START_DATE = "2024-01-01"
DAYS = 731

PLANS: tuple[tuple[int, str, float, int, str], ...] = (
    (1, "Free", 0.0, 3, "free"),
    (2, "Starter", 49.0, 10, "paid"),
    (3, "Team", 199.0, 50, "paid"),
    (4, "Business", 599.0, 200, "paid"),
    (5, "Enterprise", 1800.0, 2000, "enterprise"),
)

INDUSTRIES = ("logistics", "fintech", "healthtech", "retail", "media", "education", "manufacturing")
COUNTRIES = ("Atlantia", "Borealis", "Caldera", "Dorne", "Ember")
ROLES = ("admin", "member", "viewer", "billing")
EVENT_NAMES = ("query_run", "dashboard_view", "export", "invite_sent", "api_call")
CHURN_REASONS = ("price", "missing feature", "budget cut", "acquired", "poor support")
TICKET_PRIORITIES: tuple[tuple[str, float], ...] = (
    ("low", 0.4),
    ("normal", 0.35),
    ("high", 0.18),
    ("urgent", 0.07),
)
TICKET_CATEGORIES = ("billing", "bug", "how-to", "outage", "feature request")
CHANNELS = ("organic", "paid search", "referral", "outbound", "partner")

_ORG_PREFIXES = (
    "Northwind",
    "Bluepeak",
    "Ironvale",
    "Silverlark",
    "Redkite",
    "Greenpath",
    "Amberline",
    "Stonebridge",
    "Clearwater",
    "Highfield",
    "Brightmoor",
    "Copperleaf",
)
_ORG_SUFFIXES = ("Labs", "Group", "Systems", "Works", "Collective", "Industries")


def _tables() -> tuple[Table, ...]:
    return (
        Table(
            name="orgs",
            description=(
                "Customer organisations. One row per account; every other table hangs off org_id."
            ),
            columns=(
                Column("org_id", "INTEGER"),
                Column("org_name", "TEXT"),
                Column("industry", "TEXT"),
                Column("country", "TEXT"),
                Column("created_on", "TEXT", description="ISO date the org signed up"),
            ),
            primary_key=("org_id",),
            keywords=("organisation", "account", "customer", "tenant", "company"),
        ),
        Table(
            name="users",
            description=(
                "Seats inside an organisation. Identity is an opaque user_code - this schema holds "
                "no names or email addresses. last_seen_on is the last day the user opened the app."
            ),
            columns=(
                Column("user_id", "INTEGER"),
                Column("org_id", "INTEGER"),
                Column("user_code", "TEXT", description="opaque synthetic identifier"),
                Column("role", "TEXT", description="admin, member, viewer or billing"),
                Column("created_on", "TEXT"),
                Column("last_seen_on", "TEXT", nullable=True, description="null if never returned"),
            ),
            primary_key=("user_id",),
            keywords=("user", "seat", "member", "role", "activity"),
        ),
        Table(
            name="plans",
            description=(
                "The price book. monthly_price is list price per month; seat_limit is the maximum "
                "number of users the plan allows."
            ),
            columns=(
                Column("plan_id", "INTEGER"),
                Column("plan_name", "TEXT"),
                Column("monthly_price", "REAL"),
                Column("seat_limit", "INTEGER"),
                Column("tier", "TEXT", description="free, paid or enterprise"),
            ),
            primary_key=("plan_id",),
            keywords=("plan", "tier", "pricing", "package", "seat limit"),
        ),
        Table(
            name="subscriptions",
            description=(
                "What an org is paying for. A subscription is live when ended_on is null; mrr is "
                "the negotiated monthly recurring revenue, which can differ from the plan price."
            ),
            columns=(
                Column("subscription_id", "INTEGER"),
                Column("org_id", "INTEGER"),
                Column("plan_id", "INTEGER"),
                Column("started_on", "TEXT"),
                Column("ended_on", "TEXT", nullable=True, description="null while still active"),
                Column("seats", "INTEGER"),
                Column(
                    "mrr", "REAL", description="monthly recurring revenue for this subscription"
                ),
                Column("status", "TEXT", description="active, cancelled or trial"),
            ),
            primary_key=("subscription_id",),
            keywords=("subscription", "mrr", "revenue", "seats", "contract", "active"),
        ),
        Table(
            name="events",
            description=(
                "Daily product-usage roll-up. One row per org, user, event name and day, with "
                "event_count actions that day - counting rows is not counting events."
            ),
            columns=(
                Column("event_id", "INTEGER"),
                Column("org_id", "INTEGER"),
                Column("user_id", "INTEGER"),
                Column("event_name", "TEXT"),
                Column("event_date", "TEXT"),
                Column("event_count", "INTEGER"),
            ),
            primary_key=("event_id",),
            keywords=("event", "usage", "activity", "engagement", "telemetry"),
        ),
        Table(
            name="churn_events",
            description=(
                "One row per organisation that cancelled, with the date, the stated reason and the "
                "monthly recurring revenue lost. Logo churn is a row count; revenue churn sums "
                "mrr_lost."
            ),
            columns=(
                Column("churn_id", "INTEGER"),
                Column("org_id", "INTEGER"),
                Column("churn_date", "TEXT"),
                Column("reason", "TEXT"),
                Column("mrr_lost", "REAL"),
            ),
            primary_key=("churn_id",),
            keywords=("churn", "cancellation", "lost", "attrition", "downgrade"),
        ),
        Table(
            name="expansions",
            description=(
                "Upsells: seats and monthly revenue added to an existing org after it signed up."
            ),
            columns=(
                Column("expansion_id", "INTEGER"),
                Column("org_id", "INTEGER"),
                Column("expansion_date", "TEXT"),
                Column("seats_added", "INTEGER"),
                Column("mrr_added", "REAL"),
            ),
            primary_key=("expansion_id",),
            keywords=("expansion", "upsell", "growth", "upgrade", "net revenue retention"),
        ),
        Table(
            name="support_tickets",
            description=(
                "Support requests. closed_on is null while the ticket is open; satisfaction is a "
                "1-5 score recorded only when the customer answered the survey."
            ),
            columns=(
                Column("ticket_id", "INTEGER"),
                Column("org_id", "INTEGER"),
                Column("opened_on", "TEXT"),
                Column("closed_on", "TEXT", nullable=True),
                Column("priority", "TEXT"),
                Column("category", "TEXT"),
                Column("satisfaction", "INTEGER", nullable=True, description="1-5, often null"),
            ),
            primary_key=("ticket_id",),
            keywords=("support", "ticket", "csat", "priority", "incident", "helpdesk"),
        ),
        Table(
            name="cohorts",
            description=(
                "The acquisition cohort an org belongs to: the month it was acquired and the "
                "channel that brought it in. One row per org."
            ),
            columns=(
                Column("cohort_id", "INTEGER"),
                Column("org_id", "INTEGER"),
                Column("cohort_month", "TEXT", description="YYYY-MM"),
                Column("acquisition_channel", "TEXT"),
            ),
            primary_key=("cohort_id",),
            keywords=("cohort", "acquisition", "channel", "vintage", "retention"),
        ),
    )


_RELATIONSHIPS = (
    Relationship("users", ("org_id",), "orgs", ("org_id",)),
    Relationship("subscriptions", ("org_id",), "orgs", ("org_id",)),
    Relationship("subscriptions", ("plan_id",), "plans", ("plan_id",)),
    Relationship("events", ("org_id",), "orgs", ("org_id",)),
    Relationship("events", ("user_id",), "users", ("user_id",)),
    Relationship("churn_events", ("org_id",), "orgs", ("org_id",)),
    Relationship("expansions", ("org_id",), "orgs", ("org_id",)),
    Relationship("support_tickets", ("org_id",), "orgs", ("org_id",)),
    Relationship("cohorts", ("org_id",), "orgs", ("org_id",)),
)


def populate(rng: Random) -> Population:
    """Every row of the SaaS-ops fixture, from one seeded generator and nothing else."""
    dates = date_series(START_DATE, DAYS)

    orgs: list[tuple] = []
    for org_id in range(1, 121):
        prefix = _ORG_PREFIXES[rng.randrange(len(_ORG_PREFIXES))]
        suffix = _ORG_SUFFIXES[rng.randrange(len(_ORG_SUFFIXES))]
        created = dates[rng.randrange(0, 500)]
        orgs.append(
            (
                org_id,
                f"{prefix} {suffix}",
                INDUSTRIES[rng.randrange(len(INDUSTRIES))],
                COUNTRIES[rng.randrange(len(COUNTRIES))],
                created,
            )
        )

    users: list[tuple] = []
    user_id = 0
    users_by_org: dict[int, list[int]] = {}
    for org_id, *_rest in orgs:
        created_on = orgs[org_id - 1][4]
        created_index = dates.index(created_on)
        for _ in range(rng.randrange(2, 12)):
            user_id += 1
            joined = dates[min(created_index + rng.randrange(0, 120), DAYS - 1)]
            seen = None
            if rng.random() > 0.15:
                joined_index = dates.index(joined)
                seen = dates[min(joined_index + rng.randrange(0, 400), DAYS - 1)]
            users.append(
                (
                    user_id,
                    org_id,
                    code("USR", user_id),
                    ROLES[rng.randrange(len(ROLES))],
                    joined,
                    seen,
                )
            )
            users_by_org.setdefault(org_id, []).append(user_id)

    plans = [tuple(row) for row in PLANS]

    subscriptions: list[tuple] = []
    churn_events: list[tuple] = []
    subscription_id = 0
    churn_id = 0
    for org_id, *_rest in orgs:
        created_index = dates.index(orgs[org_id - 1][4])
        plan_id = 1 + rng.randrange(len(PLANS))
        seats = rng.randrange(2, min(60, PLANS[plan_id - 1][3]) + 1)
        list_price = PLANS[plan_id - 1][2]
        mrr = money(list_price * (0.8 + rng.random() * 0.5)) if list_price else 0.0
        cancelled = rng.random() < 0.28
        started_index = min(created_index + rng.randrange(0, 30), DAYS - 1)
        ended = None
        status = "active"
        if plan_id == 1:
            status = "trial"
        if cancelled:
            end_index = min(started_index + rng.randrange(60, 500), DAYS - 1)
            ended = dates[end_index]
            status = "cancelled"
        subscription_id += 1
        subscriptions.append(
            (
                subscription_id,
                org_id,
                plan_id,
                dates[started_index],
                ended,
                seats,
                mrr,
                status,
            )
        )
        if cancelled:
            churn_id += 1
            churn_events.append(
                (
                    churn_id,
                    org_id,
                    ended,
                    CHURN_REASONS[rng.randrange(len(CHURN_REASONS))],
                    mrr,
                )
            )
        # A minority of orgs bought a second subscription (a separate team, a second product line).
        if rng.random() < 0.22:
            subscription_id += 1
            second_plan = 1 + rng.randrange(len(PLANS))
            second_price = PLANS[second_plan - 1][2]
            subscriptions.append(
                (
                    subscription_id,
                    org_id,
                    second_plan,
                    dates[min(started_index + rng.randrange(30, 300), DAYS - 1)],
                    None,
                    rng.randrange(1, 20),
                    money(second_price * (0.8 + rng.random() * 0.4)) if second_price else 0.0,
                    "active",
                )
            )

    events: list[tuple] = []
    event_id = 0
    for _ in range(3000):
        org_id = 1 + rng.randrange(len(orgs))
        candidates = users_by_org.get(org_id) or []
        if not candidates:
            continue
        event_id += 1
        events.append(
            (
                event_id,
                org_id,
                candidates[rng.randrange(len(candidates))],
                EVENT_NAMES[rng.randrange(len(EVENT_NAMES))],
                dates[rng.randrange(0, DAYS)],
                rng.randrange(1, 40),
            )
        )

    expansions: list[tuple] = []
    expansion_id = 0
    for org_id, *_rest in orgs:
        if rng.random() >= 0.4:
            continue
        for _ in range(rng.randrange(1, 3)):
            expansion_id += 1
            expansions.append(
                (
                    expansion_id,
                    org_id,
                    dates[rng.randrange(60, DAYS)],
                    rng.randrange(1, 25),
                    money(40 + rng.random() * 600),
                )
            )

    tickets: list[tuple] = []
    for ticket_id in range(1, 401):
        org_id = 1 + rng.randrange(len(orgs))
        opened_index = rng.randrange(0, DAYS)
        closed = None
        satisfaction = None
        if rng.random() < 0.82:
            closed = dates[min(opened_index + rng.randrange(0, 21), DAYS - 1)]
            if rng.random() < 0.55:
                satisfaction = rng.randrange(1, 6)
        tickets.append(
            (
                ticket_id,
                org_id,
                dates[opened_index],
                closed,
                weighted_choice(rng, TICKET_PRIORITIES),
                TICKET_CATEGORIES[rng.randrange(len(TICKET_CATEGORIES))],
                satisfaction,
            )
        )

    cohorts: list[tuple] = []
    for index, org in enumerate(orgs, start=1):
        cohorts.append(
            (
                index,
                org[0],
                org[4][:7],
                CHANNELS[rng.randrange(len(CHANNELS))],
            )
        )

    return {
        "orgs": orgs,
        "users": users,
        "plans": plans,
        "subscriptions": subscriptions,
        "events": events,
        "churn_events": churn_events,
        "expansions": expansions,
        "support_tickets": tickets,
        "cohorts": cohorts,
    }


SPEC = DatasetSpec(
    name="saas_ops",
    source_id="eval_saas_ops",
    version="1.0.0",
    description=(
        "Synthetic B2B SaaS operations: organisations, users, plans, subscriptions, usage events, "
        "churn, expansion, support tickets and acquisition cohorts."
    ),
    domain="saas_operations",
    tables=_tables(),
    relationships=_RELATIONSHIPS,
    populate=populate,
    seed=SEED,
    verified_queries=(
        (
            "how much monthly recurring revenue is active right now",
            "SELECT SUM(mrr) AS active_mrr FROM subscriptions WHERE ended_on IS NULL",
        ),
        (
            "which organisations churned and how much revenue was lost",
            "SELECT o.org_name, c.mrr_lost FROM churn_events c "
            "JOIN orgs o ON o.org_id = c.org_id ORDER BY c.mrr_lost DESC",
        ),
    ),
    synthetic=True,
    provenance_note=(
        f"Fabricated data generated by app/evaluation/datasets/saas_ops.py with seed {SEED}. "
        "No organisation, user or revenue figure corresponds to a real company."
    ),
    covered_concepts=(
        "orgs",
        "users",
        "subscriptions",
        "plans",
        "events",
        "churn",
        "expansion",
        "support",
        "cohorts",
    ),
)

__all__ = ["SEED", "SPEC", "populate"]
