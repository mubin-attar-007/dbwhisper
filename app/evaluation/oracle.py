"""A scripted provider that replays the dataset's reference SQL. It measures the harness, not a model.

Say the uncomfortable part first: **an execution-accuracy number produced with this provider is not a
model result.** The SQL it returns was written by the person who authored the case. Reporting it as
"DBWhisper answers 94% of questions correctly" would be exactly the class of claim
``docs/v2/CLAIM_AUDIT.md`` exists to prevent, and :class:`~app.evaluation.provenance.Provenance`
carries a ``measures_model=False`` flag so a report cannot quietly forget.

What it *does* measure, genuinely and offline:

* that retrieval surfaces the tables the reference query needs, from the real index;
* that the real policy engine allows those queries against the real enrolled scope;
* that the execution service runs them read-only and returns the rows the oracle expects;
* that the result comparison, the taxonomy and the report are correct end to end;
* **that no unsafe request executes** - the safety cases replay genuinely unsafe statements through
  the whole graph, and the policy engine has to stop every one of them. That is a real result.

One part is not an oracle at all. :func:`looks_ambiguous` is a small deterministic *classifier* that
decides whether to ask a clarifying question, and it never looks at the case's expected behaviour -
it reads the question text and nothing else. So clarification precision and recall computed under
this provider are a real (if primitive) measurement of a real classifier, and they are not 100%.
That is deliberate: a metric that is perfect by construction is not a metric.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.evaluation.schema import EvalCase
from app.llm.providers.fake import FakeProvider
from app.llm.types import ModelRequest

ORACLE_PROFILE_LABEL = "fake/oracle"

# ---------------------------------------------------------------------------------------------
# Deterministic ambiguity classifier
# ---------------------------------------------------------------------------------------------

#: Superlatives with no stated measure. "Best" is only answerable once somebody says best *at what*.
_UNQUALIFIED_SUPERLATIVES = (
    "best",
    "worst",
    "biggest",
    "sickest",
    "top customers",
    "good",
    "bad",
    "performing",
    "doing",
)
#: Bare metric nouns. "Revenue" alone does not say over what period or which definition.
_BARE_METRICS = (
    "sales",
    "revenue",
    "the numbers",
    "occupancy",
    "money",
    "performance",
)
#: Relative time expressions with no anchor date. The corpus spans two years, so these are undefined.
_FLOATING_PERIODS = ("last quarter", "last month", "last year", "recently", "this quarter")
#: A rate has a numerator and a denominator; naming only one of them is an incomplete question.
_RATE_WORDS = ("rate", "share of", "percentage of")
_DENOMINATOR_HINTS = ("out of", "per ", "over ", "divided by", "as a share of")
#: Words whose population depends on a definition the schema does not fix.
_DEFINITION_DEPENDENT = ("active users", "big customers", "new accounts", "should we")

#: Signals that a question is concrete enough to answer: an explicit period, a named aggregation, or
#: an explicit grouping. Note what this does *not* resolve - see the guard in :func:`looks_ambiguous`.
_CONCRETE = re.compile(
    r"\b(20\d\d|q[1-4]\b|january|february|march|april|may|june|july|august|september|october"
    r"|november|december|count|how many|how much|average|total|sum|list"
    r"|which \w+ (?:have|has|were|are)|each \w+|by \w+|in each|per \w+)\b"
)


@dataclass(frozen=True, slots=True)
class AmbiguityVerdict:
    ambiguous: bool
    trigger: str = ""
    question: str = ""

    def clarifying_question(self) -> str:
        """The question to put back to the user. Names the trigger, so it can be scored on topic."""
        if not self.ambiguous:
            return ""
        return (
            f"Before I run anything: what should '{self.trigger}' mean here - which metric, "
            "and over which period? I can give you a precise answer once that is fixed."
        )


def looks_ambiguous(question: str) -> AmbiguityVerdict:
    """Decide whether to ask, from the question text alone.

    Deliberately simple and deliberately not tuned against the labels: it fires on an unqualified
    superlative, a bare metric noun, a floating relative period, a rate with no denominator, or a
    term whose population depends on a definition - unless the question also carries something
    concrete (a year, a quarter, a named aggregation, an explicit grouping).
    """
    text = " ".join((question or "").lower().split())
    if not text:
        return AmbiguityVerdict(False)

    trigger = ""
    #: A definition-dependent term is not rescued by concreteness elsewhere in the sentence, so it is
    #: tracked separately: "how many active users" is precise about the aggregation and still silent
    #: about which population counts as active.
    definitional = False

    for phrase in _DEFINITION_DEPENDENT:
        if phrase in text:
            trigger, definitional = phrase, True
            break
    if not trigger:
        for phrase in _FLOATING_PERIODS:
            if phrase in text:
                trigger = phrase
                break
    if not trigger:
        for phrase in _UNQUALIFIED_SUPERLATIVES:
            if re.search(rf"\b{re.escape(phrase)}\b", text):
                trigger = phrase
                break
    if not trigger:
        for phrase in _RATE_WORDS:
            # Word boundaries matter: "generate" is not a question about a rate.
            if re.search(rf"\b{re.escape(phrase)}", text) and not any(
                hint in text for hint in _DENOMINATOR_HINTS
            ):
                trigger = phrase
                break
    if not trigger:
        for phrase in _BARE_METRICS:
            if re.search(rf"\b{re.escape(phrase)}\b", text):
                trigger = phrase
                break
    if not trigger:
        return AmbiguityVerdict(False, question=text)

    # A concrete period, aggregation or grouping resolves a vague measure - but not a vague
    # population, which is why the definitional case skips this escape hatch.
    if not definitional and _CONCRETE.search(text):
        return AmbiguityVerdict(False, trigger=trigger, question=text)
    return AmbiguityVerdict(True, trigger=trigger, question=text)


# ---------------------------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------------------------


@dataclass
class OracleProvider:
    """Wraps a :class:`~app.llm.providers.fake.FakeProvider` and answers from the current case.

    The runner sets :attr:`current_case` immediately before invoking the graph. That is honest about
    what this is: a script, driven by the harness, going through the production provider interface so
    the rest of the pipeline cannot tell the difference.
    """

    provider: FakeProvider = field(default_factory=FakeProvider)
    current_case: EvalCase | None = None
    #: When false the provider never asks a clarifying question, whatever the classifier says.
    clarification_enabled: bool = True
    asked: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.provider.add_rule(lambda r: r.prompt_name == "understand", self._understand)
        self.provider.add_rule(
            lambda r: r.prompt_name in {"generate_sql", "repair_sql"}, self._generate
        )
        self.provider.add_rule(lambda r: r.prompt_name == "summarize", self._summarize)

    # The provider protocol - delegated, so the router treats this exactly like any other provider.
    def complete(self, request, profile):  # type: ignore[no-untyped-def]
        return self.provider.complete(request, profile)

    def health(self):  # type: ignore[no-untyped-def]
        return self.provider.health()

    @property
    def calls(self):  # type: ignore[no-untyped-def]
        return self.provider.calls

    # -- scripted answers ------------------------------------------------------------------------
    def _understand(self, request: ModelRequest) -> str:
        case = self.current_case
        question = case.question if case else request.user
        verdict = (
            looks_ambiguous(question) if self.clarification_enabled else AmbiguityVerdict(False)
        )
        payload: dict[str, object] = {
            "intent_type": _intent_type(question),
            "requires_clarification": verdict.ambiguous,
        }
        if verdict.ambiguous:
            payload["clarification_question"] = verdict.clarifying_question()
            if case is not None:
                self.asked[case.id] = verdict.trigger
        return json.dumps(payload)

    def _generate(self, request: ModelRequest) -> str:
        case = self.current_case
        sql = (case.gold_sql or "") if case else ""
        if not sql:
            # No reference query: the case is a refusal or an out-of-scope question. Returning an
            # empty statement is what a model that declined would produce, and the graph blocks it.
            return json.dumps({"sql": "", "rationale": "no reference query for this case"})
        return json.dumps(
            {
                "sql": sql,
                "rationale": "reference query replayed by the evaluation oracle",
                "follow_ups": [],
            }
        )

    def _summarize(self, request: ModelRequest) -> str:
        case = self.current_case
        question = case.question if case else ""
        return json.dumps(
            {
                "answer": f"Result for: {question}"[:200],
                "observations": [],
                "caveats": [
                    "Generated by the evaluation oracle; this text is not a model summary."
                ],
                "follow_ups": [],
            }
        )


def _intent_type(question: str) -> str:
    """A deterministic intent label, matching the enum the understand schema allows."""
    text = (question or "").lower()
    if any(w in text for w in ("trend", "over time", "per month", "each month", "monthly")):
        return "trend"
    if any(w in text for w in ("top ", "most", "least", "best", "worst", "highest", "lowest")):
        return "ranking"
    if any(w in text for w in ("compare", "versus", " vs ", "difference between")):
        return "comparison"
    if any(w in text for w in ("how many", "how much", "total", "average", "count", "sum")):
        return "aggregation"
    if any(w in text for w in ("why", "explain", "cause", "driver")):
        return "diagnostic"
    return "lookup"


__all__ = [
    "ORACLE_PROFILE_LABEL",
    "AmbiguityVerdict",
    "OracleProvider",
    "looks_ambiguous",
]
