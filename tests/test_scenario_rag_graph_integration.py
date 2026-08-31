from datetime import date, datetime, timezone
import inspect
from pathlib import Path
import unittest
from unittest.mock import patch

from langgraph.types import Command

from profile_agent.graphs.interview import build_interview_graph
from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AskAction,
    AssessmentTarget,
    EvidenceRequirement,
    GeneratedQuestion,
    InterviewPlan,
)
from profile_agent.schemas.runtime_schema import (
    AnswerProcessingResult,
    EvidenceDraft,
    RequirementAssessment,
    TurnAssessment,
)
from profile_agent.services.answer_processor_service import process_answer
from profile_agent.schemas.scenario_rag_schema import ScenarioSelection
from profile_agent.services.runtime_state_service import initialize_runtime_state
from profile_agent.services.scenario_bank_service import ScenarioCatalog


ROOT = Path(__file__).resolve().parents[1] / "profile_agent" / "knowledge" / "scenario_banks" / "ai_application_engineering_2026_h2"
NOW = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)


def make_plan(mode: str = "scenario") -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30, max_questions=4, closing_buffer_minutes=0,
        targets=[AssessmentTarget(
            id="target_01", objective="验证 Context 和 Memory 业务链路",
            target_type="system_design", competency_ids=[],
            evidence_requirements=[EvidenceRequirement(
                id="req_01", description="验证 Memory 写入和删除边界",
                planned_role_dimension_id="role_dim_03",
            )], related_claim_ids=[], priority="high", must_cover=True,
            time_budget_minutes=10, preferred_modes=[mode],
        )],
    )


class ScenarioRetrieverSpy:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def retrieve(self, request: object, *, as_of: date):
        self.calls.append((request, as_of))
        return ScenarioSelection(
            status="hit",
            retrieval_unit_id="enterprise_knowledge_assistant::knowledge_rag_memory",
        )


class GeneratorSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, *, action, plan, claim_registry=None, recent_turns=None, retrieval_result=None, scenario_context=None):
        self.calls.append({"action": action, "scenario_context": scenario_context, "retrieval_result": retrieval_result})
        return GeneratedQuestion(text=f"问题 {len(self.calls)}")


def no_op_answer_processor(*, plan, runtime_state, turn, existing_evidences, claim_registry=None):
    return AnswerProcessingResult(new_evidences=[], runtime_state=runtime_state)


class StructuredAnswerProcessor:
    """Exercise the graph with the real AnswerProcessor service boundary."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(
        self,
        *,
        plan,
        runtime_state,
        turn,
        existing_evidences,
        claim_registry=None,
        allowed_gap_tags=(),
    ):
        self.calls.append({"turn": turn, "allowed_gap_tags": tuple(allowed_gap_tags)})
        kwargs = {}
        if "allowed_gap_tags" in inspect.signature(process_answer).parameters:
            kwargs["allowed_gap_tags"] = allowed_gap_tags
        return process_answer(
            plan=plan,
            runtime_state=runtime_state,
            turn=turn,
            existing_evidences=existing_evidences,
            claim_registry=claim_registry,
            llm_client=StructuredAnswerLLM(),
            **kwargs,
        )


class StructuredAnswerLLM:
    def structured(self, messages, schema):
        return TurnAssessment(
            answer_relevance="high",
            evidence_drafts=[
                EvidenceDraft(
                    requirement_ids=["req_01"],
                    related_claim_ids=[],
                    polarity="supporting",
                    strength="medium",
                    observation="候选人说明了 Memory 删除，但未证明版本与引用。",
                    source_excerpt="Memory 删除已说明",
                )
            ],
            requirement_assessments=[
                RequirementAssessment(
                    requirement_id="req_01",
                    recommended_status="in_progress",
                    rationale="Memory 删除已说明，版本与引用未证明",
                    missing_evidence_tags=["版本", "引用"],
                )
            ],
        )


class ScenarioRagGraphIntegrationTests(unittest.TestCase):
    def state(self, plan: InterviewPlan) -> dict:
        return {
            "assessment_id": "scenario-graph",
            "interview_plan": plan,
            "claim_registry": ClaimRegistry(),
            "runtime_state": initialize_runtime_state(plan, started_at=NOW),
            "interview_turns": [],
            "evidences": [],
        }

    @staticmethod
    def payload(result: dict) -> dict:
        return result["__interrupt__"][0].value

    def test_scenario_graph_has_one_prepare_node_and_persists_opening_provenance(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=NOW.date())
        retriever = ScenarioRetrieverSpy()
        generator = GeneratorSpy()
        graph = build_interview_graph(
            question_generator=generator,
            answer_processor=no_op_answer_processor,
            report_generator=lambda **_: None,
            now_provider=lambda: NOW,
            scenario_catalog=catalog,
            scenario_retriever=retriever,
        )
        with patch("profile_agent.graphs.interview.decide_next_action", return_value=AskAction(
            target_id="target_01", primary_requirement_id="req_01",
            question_mode="scenario", reason="验证 Memory",
        )):
            result = graph.invoke(self.state(make_plan()), {"configurable": {"thread_id": "scenario-open"}})
        state = graph.get_state({"configurable": {"thread_id": "scenario-open"}}).values
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        self.assertIn(("supervisor", "prepare_question_context"), edges)
        self.assertIn(("prepare_question_context", "generate_question"), edges)
        self.assertNotIn("retrieve_question", {node.name for node in graph.get_graph().nodes.values()})
        self.assertEqual(len(retriever.calls), 1)
        self.assertEqual(generator.calls[0]["scenario_context"].retrieval_status, "hit")
        self.assertEqual(state["interview_turns"][0].question_provenance.retrieval_unit_id, "enterprise_knowledge_assistant::knowledge_rag_memory")
        self.assertIsNone(state["interview_turns"][0].retrieval_trace)
        self.assertIsNone(state.get("scenario_context"))
        self.assertEqual(self.payload(result)["question"], "问题 1")

    def test_follow_up_reuses_context_without_second_retrieval_and_releases_one_constraint(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=NOW.date())
        retriever = ScenarioRetrieverSpy()
        generator = GeneratorSpy()
        actions = iter([
            AskAction(target_id="target_01", primary_requirement_id="req_01", question_mode="scenario", reason="验证 Memory"),
            AskAction(target_id="target_01", primary_requirement_id="req_01", question_mode="follow_up", reason="追问缺口"),
        ])
        graph = build_interview_graph(
            question_generator=generator, answer_processor=no_op_answer_processor,
            report_generator=lambda **_: None, now_provider=lambda: NOW,
            scenario_catalog=catalog, scenario_retriever=retriever,
        )
        config = {"configurable": {"thread_id": "scenario-follow"}}
        with patch("profile_agent.graphs.interview.decide_next_action", side_effect=lambda _: next(actions)):
            graph.invoke(self.state(make_plan()), config)
            graph.invoke(Command(resume="候选人回答"), config)
        self.assertEqual(len(retriever.calls), 1)
        self.assertIsNotNone(generator.calls[1]["scenario_context"].selected_constraint)
        self.assertLessEqual(len(generator.calls[1]["scenario_context"].revealed_constraint_ids), 1)

    def test_follow_up_selects_constraint_from_missing_evidence_gaps(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=NOW.date())
        retriever = ScenarioRetrieverSpy()
        generator = GeneratorSpy()
        answer_processor = StructuredAnswerProcessor()
        actions = iter([
            AskAction(
                target_id="target_01",
                primary_requirement_id="req_01",
                question_mode="scenario",
                reason="验证 Memory",
            ),
            AskAction(
                target_id="target_01",
                primary_requirement_id="req_01",
                question_mode="follow_up",
                reason="追问缺口",
            ),
        ])
        graph = build_interview_graph(
            question_generator=generator,
            answer_processor=answer_processor,
            report_generator=lambda **_: None,
            now_provider=lambda: NOW,
            scenario_catalog=catalog,
            scenario_retriever=retriever,
        )
        config = {"configurable": {"thread_id": "scenario-gap"}}
        answer = "Memory 删除已说明，但未证明版本与引用。"
        with patch(
            "profile_agent.graphs.interview.decide_next_action",
            side_effect=lambda _: next(actions),
        ):
            graph.invoke(self.state(make_plan()), config)
            graph.invoke(Command(resume=answer), config)

        self.assertEqual(retriever.calls[0][0].evidence_gap, [])
        self.assertEqual(
            answer_processor.calls[0]["allowed_gap_tags"],
            ("Memory", "删除", "RAG", "版本", "引用"),
        )
        state = graph.get_state(config).values
        self.assertEqual(
            state["runtime_state"].requirement_progress["req_01"].latest_gap_tags,
            ["版本", "引用"],
        )
        context = generator.calls[1]["scenario_context"]
        self.assertEqual(context.primary_dimension_id, "role_dim_03")
        self.assertEqual(
            context.provenance.selected_constraint_id,
            "knowledge_policy_version_stale",
        )
        self.assertNotEqual(
            context.provenance.selected_constraint_id,
            "knowledge_memory_delete",
        )
        dumped = context.model_dump(mode="json")
        for forbidden in (
            "scenario",
            "module",
            "opening_goal",
            "evidence_signals",
            "critical_errors",
            "base_constraints",
            "constraint_id",
        ):
            self.assertNotIn(forbidden, dumped)
        self.assertEqual(set(dumped["selected_constraint"]), {"fact"})

    def test_foundation_bypasses_scenario_context(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=NOW.date())
        retriever = ScenarioRetrieverSpy()
        generator = GeneratorSpy()
        graph = build_interview_graph(
            question_generator=generator, answer_processor=no_op_answer_processor,
            report_generator=lambda **_: None, now_provider=lambda: NOW,
            scenario_catalog=catalog, scenario_retriever=retriever,
        )
        with patch("profile_agent.graphs.interview.decide_next_action", return_value=AskAction(
            target_id="target_01", primary_requirement_id="req_01",
            question_mode="foundation", reason="验证基础",
        )):
            graph.invoke(self.state(make_plan("foundation")), {"configurable": {"thread_id": "scenario-foundation"}})
        self.assertEqual(retriever.calls, [])
        self.assertIsNone(generator.calls[0]["scenario_context"])


if __name__ == "__main__":
    unittest.main()
