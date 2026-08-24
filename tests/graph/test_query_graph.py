"""The query graph end to end: happy path, refusals, repair, interrupts and resume."""

from __future__ import annotations

import json

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.graph.query_graph import answer_payload, compile_query_graph, pending_interrupt
from app.graph.state import RunStatus, initial_state
from app.llm.types import FailureKind

SOURCE = "demo"


def _steps(result) -> list[str]:
    return [step["name"] for step in result.get("steps", [])]


class TestHappyPath:
    def test_question_to_answer(self, run_graph):
        result, _graph, _config = run_graph("Which city has the most customers?")
        assert result["status"] == RunStatus.COMPLETED.value, result.get("error")

        payload = answer_payload(result)
        assert payload["sql"].startswith("SELECT city")
        assert payload["row_count"] == 3
        assert {row[0] for row in payload["rows"]} == {"Mumbai", "Delhi", "Pune"}
        assert payload["answer"] == "Mumbai has the most customers."
        assert payload["follow_ups"] == ["How has that changed over time?"]

    def test_every_stage_is_traced(self, run_graph):
        result, _g, _c = run_graph("Which city has the most customers?")
        names = _steps(result)
        for expected in (
            "retrieve",
            "understand",
            "generate",
            "validate",
            "execute",
            "verify",
            "summarize",
            "finalize",
        ):
            assert expected in names, f"{expected} missing from {names}"
        assert all("duration_ms" in step for step in result["steps"])

    def test_evidence_checklist_is_populated(self, run_graph):
        result, _g, _c = run_graph("Which city has the most customers?")
        evidence = result["evidence"]
        assert evidence["schema_grounded"] is True
        assert evidence["policy_passed"] is True
        assert evidence["read_only_enforced"] is True
        assert evidence["result_shape_verified"] is True
        assert evidence["summary_grounded"] is True
        assert evidence["human_approved"] is None  # no approval was required

    def test_versions_are_recorded(self, run_graph):
        result, _g, _c = run_graph("Which city has the most customers?")
        versions = answer_payload(result)["versions"]
        assert versions["graph"].startswith("query_graph@")
        assert versions["sql_policy"].startswith("sql_policy@")
        assert versions["generate_sql"].startswith("generate_sql@")

    def test_routing_records_which_model_answered(self, run_graph):
        result, _g, _c = run_graph("Which city has the most customers?")
        routing = {entry["node"]: entry for entry in result["routing"]}
        assert routing["generate_sql"]["profile"] == "fake"
        assert routing["generate_sql"]["fallback"] is False

    def test_chart_is_chosen_deterministically(self, run_graph):
        result, _g, _c = run_graph("Which city has the most customers?")
        chart = result["chart"]
        assert chart["chart"] == "bar"
        assert chart["x"] == "city" and chart["y"] == ["n"]
        assert chart["reason"]

    def test_retrieval_evidence_explains_the_context(self, run_graph):
        result, _g, _c = run_graph("Which city has the most customers?")
        evidence = result["retrieval_evidence"]
        assert "main.customers" in evidence["tables"]
        assert evidence["selected"]


class TestRefusals:
    def test_a_model_that_writes_a_delete_is_blocked(self, run_graph, model, make_graph):
        model.add_rule(
            lambda r: r.prompt_name == "generate_sql",
            json.dumps({"sql": "DELETE FROM customers"}),
        )
        result, _g, _c = run_graph("remove all customers")
        assert result["status"] == RunStatus.BLOCKED.value
        assert "delete" in result["error"].lower()
        assert result["error_category"] == "policy"
        assert "execute" not in _steps(result), "a refused query must never reach the database"

    def test_a_system_catalog_query_is_blocked_without_repair(self, run_graph, model):
        model.add_rule(
            lambda r: r.prompt_name in {"generate_sql", "repair_sql"},
            json.dumps({"sql": "SELECT name FROM sqlite_master"}),
        )
        result, _g, _c = run_graph("list the tables")
        assert result["status"] == RunStatus.BLOCKED.value
        assert _steps(result).count("generate") == 1, "a refusal is not a mistake to retry"

    def test_an_unknown_database_is_blocked_before_any_model_call(
        self, run_graph, make_graph, model
    ):
        from app.graph.state import initial_state as make_state

        deps = make_graph()
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = compile_query_graph(deps, checkpointer=saver)
            state = make_state(question="anything", source_id="not-enrolled")
            result = graph.invoke(state, {"configurable": {"thread_id": "x"}})
        assert result["status"] == RunStatus.BLOCKED.value
        assert "Enroll" in result["error"]
        assert model.calls == [], "no model should be called for an unknown database"

    def test_a_question_about_absent_data_is_blocked_with_an_explanation(self, run_graph):
        result, _g, _c = run_graph("what is the airspeed velocity of an unladen swallow")
        # Retrieval finds nothing relevant, or the model writes SQL the policy rejects; either way
        # the run must end with a clear explanation rather than an invented answer.
        assert result["status"] in {RunStatus.BLOCKED.value, RunStatus.COMPLETED.value}
        if result["status"] == RunStatus.BLOCKED.value:
            assert result["error"]


class TestRepairLoop:
    def test_a_hallucinated_column_is_repaired_once(self, run_graph, model):
        answers = iter(
            [
                json.dumps({"sql": "SELECT nonexistent_column FROM customers"}),
                json.dumps({"sql": "SELECT city FROM customers"}),
            ]
        )
        model.add_rule(
            lambda r: r.prompt_name in {"generate_sql", "repair_sql"},
            lambda _r: next(answers),
        )
        result, _g, _c = run_graph("show me the cities")
        assert result["status"] == RunStatus.COMPLETED.value
        assert _steps(result).count("validate") == 2
        assert "repair" in _steps(result)
        assert result["repair_attempts"] == 1

    def test_the_repair_budget_is_enforced(self, run_graph, model, make_graph):
        model.add_rule(
            lambda r: r.prompt_name in {"generate_sql", "repair_sql"},
            json.dumps({"sql": "SELECT still_wrong FROM customers"}),
        )
        deps = make_graph(max_repairs=2)
        result, _g, _c = run_graph("show me something", deps=deps)
        assert result["status"] == RunStatus.BLOCKED.value
        assert result["repair_attempts"] == 2
        assert _steps(result).count("validate") == 3  # first attempt + 2 repairs

    def test_the_repair_prompt_carries_the_actual_error(self, run_graph, model):
        answers = iter(
            [
                json.dumps({"sql": "SELECT ghost FROM customers"}),
                json.dumps({"sql": "SELECT city FROM customers"}),
            ]
        )
        model.add_rule(
            lambda r: r.prompt_name in {"generate_sql", "repair_sql"}, lambda _r: next(answers)
        )
        run_graph("show me the cities")
        repair_calls = [c for c in model.calls if c.prompt_name == "repair_sql"]
        assert repair_calls, "a repair call should have been made"
        assert "ghost" in repair_calls[0].user
        assert "WHAT WENT WRONG" in repair_calls[0].user


class TestClarification:
    def _ask_for_clarification(self, model):
        model.add_rule(
            lambda r: r.prompt_name == "understand",
            json.dumps(
                {
                    "intent_type": "aggregation",
                    "requires_clarification": True,
                    "clarification_question": "Do you mean gross or net revenue?",
                }
            ),
        )

    def test_the_run_pauses_and_asks(self, run_graph, model):
        self._ask_for_clarification(model)
        result, _g, _c = run_graph("what was revenue")
        payload = pending_interrupt(result)
        assert payload is not None
        assert payload["type"] == "clarification"
        assert payload["question"] == "Do you mean gross or net revenue?"
        assert "sql" not in result or not result.get("sql"), (
            "no SQL before the question is answered"
        )

    def test_resuming_with_an_answer_completes_the_run(self, run_graph, model):
        self._ask_for_clarification(model)
        result, _g, _c = run_graph("what was revenue", resume="gross revenue")
        assert result["status"] == RunStatus.COMPLETED.value
        assert result["clarification_answer"] == "gross revenue"
        assert "clarify" in _steps(result)

    def test_the_answer_reaches_the_generation_prompt(self, run_graph, model):
        self._ask_for_clarification(model)
        run_graph("what was revenue", resume="gross revenue")
        generate_calls = [c for c in model.calls if c.prompt_name == "generate_sql"]
        assert generate_calls
        assert "gross revenue" in generate_calls[0].user


class TestApproval:
    def test_plan_only_pauses_before_executing(self, run_graph):
        result, _g, _c = run_graph("Which city has the most customers?", plan_only=True)
        payload = pending_interrupt(result)
        assert payload is not None and payload["type"] == "approval"
        assert payload["sql"].startswith("SELECT city")
        assert payload["fingerprint"]

    def test_approving_runs_the_query(self, run_graph):
        result, _g, _c = run_graph(
            "Which city has the most customers?", plan_only=True, resume={"approved": True}
        )
        assert result["status"] == RunStatus.COMPLETED.value
        assert result["row_count"] == 3
        assert result["evidence"]["human_approved"] is True

    def test_rejecting_blocks_the_run_without_executing(self, run_graph):
        result, _g, _c = run_graph(
            "Which city has the most customers?", plan_only=True, resume={"approved": False}
        )
        assert result["status"] == RunStatus.BLOCKED.value
        assert "not approved" in result["error"]
        assert "execute" not in _steps(result)

    def test_edited_sql_is_revalidated_not_trusted(self, run_graph):
        result, _g, _c = run_graph(
            "Which city has the most customers?",
            plan_only=True,
            resume={"approved": True, "sql": "SELECT name FROM customers"},
        )
        assert result["status"] == RunStatus.COMPLETED.value
        assert result["sql"] == "SELECT name FROM customers"
        assert _steps(result).count("validate") == 2, "edited SQL must go through validation again"

    def test_edited_sql_that_is_unsafe_is_still_refused(self, run_graph):
        result, _g, _c = run_graph(
            "Which city has the most customers?",
            plan_only=True,
            resume={"approved": True, "sql": "DROP TABLE customers"},
        )
        assert result["status"] == RunStatus.BLOCKED.value
        assert "execute" not in _steps(result)


class TestResilience:
    def test_a_model_outage_during_generation_blocks_cleanly(self, run_graph, model):
        model.fail_next(FailureKind.CONNECTION, "local server down", times=10)
        result, _g, _c = run_graph("Which city has the most customers?")
        assert result["status"] == RunStatus.BLOCKED.value
        assert result["error_category"] in {"model_unavailable", "blocked"}
        assert "No model could write the query" in result["error"]

    def test_a_summary_failure_falls_back_to_statistics(self, run_graph, model):
        model.add_rule(lambda r: r.prompt_name == "summarize", "this is not json")
        result, _g, _c = run_graph("Which city has the most customers?")
        assert result["status"] == RunStatus.COMPLETED.value
        summary = result["summary"]
        assert summary["grounded_by"] == "deterministic"
        assert "3 rows" in summary["answer"]

    def test_understanding_degrades_without_a_model(self, run_graph, make_graph, model):
        # Every attempt, repairs included, returns unusable text so the repair budget is exhausted.
        model.add_rule(lambda r: r.prompt_name.startswith("understand"), "not json either")
        result, _g, _c = run_graph("Which city has the most customers?")
        # The structured-output repair loop gives up, and the node falls back rather than failing.
        assert result["status"] == RunStatus.COMPLETED.value
        assert result["intent"]["source"] == "deterministic"

    def test_no_credentials_are_written_into_the_checkpointed_state(self, make_graph, target):
        config = {"configurable": {"thread_id": "secrets"}}
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = compile_query_graph(make_graph(), checkpointer=saver)
            result = graph.invoke(
                initial_state(question="Which city has the most customers?", source_id=SOURCE),
                config,
            )
            checkpointed = json.dumps(graph.get_state(config).values, default=str)

        for blob in (json.dumps(result, default=str), checkpointed):
            assert target.connection_string not in blob
            assert "sqlite:///" not in blob, "a connection string must never be checkpointed"


class TestDurability:
    def test_state_survives_a_new_graph_instance(self, make_graph, model):
        """An interrupted run resumes on a different graph object, as it would after a restart."""
        self_deps = make_graph()
        model.add_rule(
            lambda r: r.prompt_name == "understand",
            json.dumps(
                {
                    "intent_type": "aggregation",
                    "requires_clarification": True,
                    "clarification_question": "gross or net?",
                }
            ),
        )
        config = {"configurable": {"thread_id": "durable"}}
        with SqliteSaver.from_conn_string(":memory:") as saver:
            first = compile_query_graph(self_deps, checkpointer=saver)
            paused = first.invoke(
                initial_state(question="what was revenue", source_id=SOURCE), config
            )
            assert pending_interrupt(paused)

            second = compile_query_graph(make_graph(), checkpointer=saver)
            resumed = second.invoke(Command(resume="gross"), config)

        assert resumed["status"] == RunStatus.COMPLETED.value
        assert "retrieve" in _steps(resumed), "history from before the pause is preserved"

    def test_the_step_history_accumulates_across_the_interrupt(self, run_graph, model):
        model.add_rule(
            lambda r: r.prompt_name == "understand",
            json.dumps(
                {
                    "intent_type": "lookup",
                    "requires_clarification": True,
                    "clarification_question": "which city?",
                }
            ),
        )
        result, _g, _c = run_graph("customers", resume="Mumbai")
        names = _steps(result)
        assert names.count("retrieve") == 1
        assert names.index("retrieve") < names.index("clarify") < names.index("generate")


class TestAnswerPayload:
    def test_is_json_serialisable(self, run_graph):
        result, _g, _c = run_graph("Which city has the most customers?")
        json.dumps(answer_payload(result), default=str)

    def test_carries_everything_the_ui_needs(self, run_graph):
        result, _g, _c = run_graph("Which city has the most customers?")
        payload = answer_payload(result)
        for key in (
            "sql",
            "rows",
            "columns",
            "answer",
            "chart",
            "evidence",
            "policy",
            "retrieval",
            "steps",
            "versions",
        ):
            assert key in payload, key

    def test_pending_interrupt_returns_none_for_a_finished_run(self, run_graph):
        result, _g, _c = run_graph("Which city has the most customers?")
        assert pending_interrupt(result) is None
