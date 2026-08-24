"""Investigation mode: bounded plans, real sub-query evidence, and citations that are checked."""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.graph.investigation import (
    MAX_SUBQUESTIONS,
    SubQueryEvidence,
    compile_investigation_graph,
    find_causal_claims,
    initial_investigation_state,
    investigation_payload,
)
from app.graph.query_graph import pending_interrupt
from app.graph.state import RunStatus

SOURCE = "demo"


def _plan(*questions: str, hypotheses: list[str] | None = None) -> str:
    return json.dumps(
        {
            "restated_question": "Why did the numbers move?",
            "hypotheses": hypotheses or ["Fewer customers in one city"],
            "sub_questions": [{"question": q, "expected_evidence": "counts"} for q in questions],
            "limitations": [],
        }
    )


def _synthesis(statement: str, citations: list[str]) -> str:
    return json.dumps(
        {
            "executive_finding": "The largest observed difference was in Mumbai.",
            "observations": [{"statement": statement, "citations": citations}],
            "assumptions": ["City is recorded on the customer, not the order."],
            "limitations": ["Only three customers exist in this demo dataset."],
            "next_steps": ["Break the same figures down by month."],
        }
    )


@pytest.fixture
def investigate(make_graph, model):
    """Run an investigation to completion (or to its plan interrupt)."""

    def runner(question: str, *, resume=None, thread: str = "inv-1", **state_kwargs):
        deps = make_graph()
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = compile_investigation_graph(deps, checkpointer=saver)
            config = {"configurable": {"thread_id": thread}}
            state = initial_investigation_state(
                question=question, source_id=SOURCE, run_id=thread, **state_kwargs
            )
            result = graph.invoke(state, config)
            if resume is not None and result.get("__interrupt__"):
                result = graph.invoke(Command(resume=resume), config)
            return result

    return runner


@pytest.fixture(autouse=True)
def _investigation_answers(model):
    model.add_rule(
        lambda r: r.prompt_name == "investigation_plan",
        _plan("How many customers are in each city?", "How many products are in each category?"),
    )
    model.add_rule(
        lambda r: r.prompt_name == "investigation_synthesis",
        _synthesis("Mumbai holds the most customers.", ["Q1.row1"]),
    )
    # Each sub-question runs through the ordinary query graph.
    model.add_rule(
        lambda r: r.prompt_name.startswith(("generate_sql", "repair_sql")),
        json.dumps({"sql": "SELECT city, COUNT(*) AS n FROM customers GROUP BY city"}),
    )


class TestCausalLint:
    @pytest.mark.parametrize(
        "text",
        [
            "Revenue fell because of the West region.",
            "The decline was caused by fewer orders.",
            "Lower pricing led to the shortfall.",
            "The drop was due to seasonality.",
            "Churn drove the decrease.",
        ],
    )
    def test_flags_causal_language(self, text: str):
        assert find_causal_claims(text)

    @pytest.mark.parametrize(
        "text",
        [
            "The largest observed contributor was the West region, accounting for 61% of the decrease.",
            "Mumbai holds the most customers.",
            "Orders fell 12% month over month.",
        ],
    )
    def test_leaves_descriptive_language_alone(self, text: str):
        assert find_causal_claims(text) == []


class TestEvidence:
    def test_citation_ids_cover_rows_and_columns(self):
        evidence = SubQueryEvidence(
            ref="Q1",
            question="revenue by region",
            status="completed",
            columns=["region", "revenue"],
            rows=[["West", 100], ["East", 200]],
            row_count=2,
        )
        ids = evidence.citation_ids()
        assert "Q1" in ids and "Q1.row2" in ids and "Q1.aggregate.revenue" in ids
        assert "Q1.row3" not in ids, "a citation to a row that does not exist must be impossible"

    def test_an_unanswered_sub_question_offers_no_citations(self):
        evidence = SubQueryEvidence(ref="Q2", question="x", status="blocked", error="refused")
        assert not evidence.answered
        assert "no result" in evidence.render()


class TestPlanning:
    def test_the_plan_is_produced_and_approved(self, investigate):
        result = investigate("Why are customers concentrated?", auto_approve_plan=True)
        assert result["status"] == RunStatus.COMPLETED.value, result.get("error")
        assert len(result["plan"]["sub_questions"]) == 2

    def test_the_ceiling_is_enforced_regardless_of_what_the_model_returns(self, investigate, model):
        model.add_rule(
            lambda r: r.prompt_name == "investigation_plan",
            _plan(*[f"Question {i}?" for i in range(12)]),
        )
        result = investigate("Why?", auto_approve_plan=True)
        assert len(result["plan"]["sub_questions"]) == MAX_SUBQUESTIONS

    def test_a_lower_ceiling_is_respected(self, investigate):
        result = investigate("Why?", auto_approve_plan=True, max_subquestions=1)
        assert len(result["plan"]["sub_questions"]) == 1

    def test_an_empty_plan_blocks_the_run(self, investigate, model):
        model.add_rule(lambda r: r.prompt_name == "investigation_plan", _plan())
        result = investigate("Why?", auto_approve_plan=True)
        assert result["status"] == RunStatus.BLOCKED.value
        assert "sub-questions" in result["error"]


class TestPlanApproval:
    def test_the_run_pauses_for_the_plan(self, investigate):
        result = investigate("Why are customers concentrated?")
        payload = pending_interrupt(result)
        assert payload is not None
        assert payload["type"] == "investigation_plan"
        assert len(payload["sub_questions"]) == 2
        assert not result.get("evidence"), "nothing may run before the plan is approved"

    def test_approving_runs_the_plan(self, investigate):
        result = investigate(
            "Why are customers concentrated?", resume={"approved": True}, thread="inv-approve"
        )
        assert result["status"] == RunStatus.COMPLETED.value
        assert len(result["evidence"]) == 2

    def test_rejecting_runs_nothing(self, investigate):
        result = investigate(
            "Why are customers concentrated?", resume={"approved": False}, thread="inv-reject"
        )
        assert result["status"] == RunStatus.BLOCKED.value
        assert "not approved" in result["error"]
        assert not result.get("evidence")

    def test_the_plan_can_be_edited(self, investigate):
        result = investigate(
            "Why are customers concentrated?",
            resume={"approved": True, "sub_questions": ["How many customers are there?"]},
            thread="inv-edit",
        )
        assert result["status"] == RunStatus.COMPLETED.value
        assert len(result["plan"]["sub_questions"]) == 1
        assert result["plan"]["sub_questions"][0]["question"] == "How many customers are there?"


class TestEvidenceGathering:
    def test_every_sub_question_produces_evidence(self, investigate):
        result = investigate("Why?", auto_approve_plan=True)
        evidence = result["evidence"]
        assert [e["ref"] for e in evidence] == ["Q1", "Q2"]
        assert all(e["sql"] for e in evidence)
        assert all(e["status"] == RunStatus.COMPLETED.value for e in evidence)

    def test_sub_queries_go_through_the_policy_engine(self, investigate, model):
        """A sub-question is not privileged: unsafe SQL is refused there too."""
        model.add_rule(
            lambda r: r.prompt_name.startswith(("generate_sql", "repair_sql")),
            json.dumps({"sql": "DROP TABLE customers"}),
        )
        result = investigate("Why?", auto_approve_plan=True)
        assert result["status"] == RunStatus.BLOCKED.value
        assert all(e["status"] == RunStatus.BLOCKED.value for e in result["evidence"])
        assert "nothing to conclude" in result["error"]

    def test_one_failing_sub_question_does_not_abort_the_rest(self, investigate, model):
        answers = iter(
            [
                json.dumps({"sql": "SELECT nope FROM customers"}),
                json.dumps({"sql": "SELECT nope FROM customers"}),
                json.dumps({"sql": "SELECT nope FROM customers"}),
                json.dumps({"sql": "SELECT city FROM customers"}),
            ]
        )
        model.add_rule(
            lambda r: r.prompt_name.startswith(("generate_sql", "repair_sql")),
            lambda _r: next(answers, json.dumps({"sql": "SELECT city FROM customers"})),
        )
        result = investigate("Why?", auto_approve_plan=True)
        statuses = [e["status"] for e in result["evidence"]]
        assert len(statuses) == 2, "both sub-questions must be attempted"


class TestSynthesis:
    def test_findings_carry_citations(self, investigate):
        result = investigate("Why?", auto_approve_plan=True)
        payload = investigation_payload(result)
        assert payload["executive_finding"]
        assert payload["observations"][0]["citations"] == ["Q1.row1"]
        assert payload["uncited_statements"] == []

    def test_an_invented_citation_is_caught(self, investigate, model):
        model.add_rule(
            lambda r: r.prompt_name == "investigation_synthesis",
            _synthesis("Sales in Antarctica tripled.", ["Q9.row42"]),
        )
        result = investigate("Why?", auto_approve_plan=True)
        assert result["uncited_statements"] == ["Sales in Antarctica tripled."]

    def test_a_statement_with_no_citation_is_caught(self, investigate, model):
        model.add_rule(
            lambda r: r.prompt_name == "investigation_synthesis",
            _synthesis("Everything is fine.", []),
        )
        result = investigate("Why?", auto_approve_plan=True)
        assert result["uncited_statements"] == ["Everything is fine."]

    def test_causal_language_is_reported(self, investigate, model):
        model.add_rule(
            lambda r: r.prompt_name == "investigation_synthesis",
            _synthesis("The concentration was caused by marketing spend.", ["Q1.row1"]),
        )
        result = investigate("Why?", auto_approve_plan=True)
        assert "caused by" in result["causal_warnings"]

    def test_no_model_falls_back_to_a_deterministic_narrative(self, investigate, model):
        from app.llm.types import FailureKind

        def _fail_synthesis(_request):
            raise AssertionError("unreachable")

        model.add_rule(
            lambda r: r.prompt_name.startswith("investigation_synthesis"),
            "not json at all, repeatedly",
        )
        result = investigate("Why?", auto_approve_plan=True)
        assert result["status"] == RunStatus.COMPLETED.value
        payload = investigation_payload(result)
        assert "deterministically" in " ".join(payload["limitations"])
        assert payload["observations"], "the fallback still reports what each sub-query returned"
        assert FailureKind  # imported to document that this path is a provider failure


class TestPayload:
    def test_is_serialisable_and_complete(self, investigate):
        result = investigate("Why?", auto_approve_plan=True)
        payload = investigation_payload(result)
        json.dumps(payload, default=str)
        for key in ("plan", "evidence", "observations", "versions", "steps"):
            assert key in payload
        assert payload["versions"]["investigation"].startswith("investigation_graph@")

    def test_the_trace_names_each_stage(self, investigate):
        result = investigate("Why?", auto_approve_plan=True)
        names = [s["name"] for s in result["steps"]]
        assert names == ["plan", "approve_plan", "investigate", "synthesize"]
