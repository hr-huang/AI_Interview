from datetime import date, datetime, timezone
from pathlib import Path
import unittest

from profile_agent.schemas.interview_schema import AssessmentTarget, AskAction, EvidenceRequirement, InterviewPlan
from profile_agent.schemas.runtime_schema import InterviewTurn
from profile_agent.schemas.scenario_rag_schema import QuestionProvenance, ScenarioSelection
from profile_agent.services.scenario_bank_service import ScenarioCatalog
from profile_agent.services.question_context_service import prepare_question_context


ROOT = Path(__file__).resolve().parents[1] / "profile_agent" / "knowledge" / "scenario_banks" / "ai_application_engineering_2026_h2"


def make_plan(mode: str = "scenario") -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=5,
        closing_buffer_minutes=0,
        targets=[AssessmentTarget(
            id="target_01",
            objective="验证 Context 和 Memory 业务链路",
            target_type="system_design",
            competency_ids=[],
            evidence_requirements=[EvidenceRequirement(
                id="req_01",
                description="验证 Memory 写入和删除边界",
                planned_role_dimension_id="role_dim_03",
            )],
            related_claim_ids=[],
            priority="high",
            must_cover=True,
            time_budget_minutes=10,
            preferred_modes=[mode],
        )],
    )


class FakeRetriever:
    def __init__(self, selection: ScenarioSelection) -> None:
        self.selection = selection
        self.calls: list[object] = []

    def retrieve(self, request: object, *, as_of: date) -> ScenarioSelection:
        self.calls.append((request, as_of))
        return self.selection


def action(mode: str = "scenario") -> AskAction:
    return AskAction(
        target_id="target_01",
        primary_requirement_id="req_01",
        question_mode=mode,
        reason="验证 Memory 删除边界",
    )


class PrepareQuestionContextTests(unittest.TestCase):
    AS_OF = date(2026, 8, 29)

    def test_new_scenario_calls_retriever_once_and_builds_opening_provenance(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        retriever = FakeRetriever(ScenarioSelection(
            status="hit",
            retrieval_unit_id="enterprise_knowledge_assistant::knowledge_rag_memory",
        ))

        context = prepare_question_context(
            action=action(), plan=make_plan(), history=[], catalog=catalog,
            retriever=retriever, as_of=self.AS_OF, evidence_gap_tags=["删除"],
        )

        self.assertEqual(len(retriever.calls), 1)
        self.assertEqual(context.retrieval_unit_id, "enterprise_knowledge_assistant::knowledge_rag_memory")
        self.assertIsNone(context.selected_constraint)
        self.assertEqual(context.revealed_constraint_ids, [])
        self.assertEqual(context.provenance.target_requirement_id, "req_01")
        self.assertEqual(context.provenance.retrieval_unit_id, context.retrieval_unit_id)

    def test_foundation_bypasses_scenario_retriever(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        retriever = FakeRetriever(ScenarioSelection(status="hit", retrieval_unit_id="bad::id"))

        context = prepare_question_context(
            action=action("foundation"), plan=make_plan("foundation"), history=[],
            catalog=catalog, retriever=retriever, as_of=self.AS_OF,
        )

        self.assertIsNone(context)
        self.assertEqual(retriever.calls, [])

    def test_scenario_without_retriever_uses_deterministic_fallback(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)

        context = prepare_question_context(
            action=action(), plan=make_plan(), history=[], catalog=catalog,
            retriever=None, as_of=self.AS_OF,
        )

        self.assertEqual(context.retrieval_status, "fallback")
        self.assertEqual(context.fallback_reason, "scenario retriever unavailable")
        self.assertEqual(context.module.primary_dimension_id, "role_dim_03")
        self.assertIsNone(context.selected_constraint)

    def test_follow_up_reuses_latest_module_and_releases_one_unused_constraint(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        module = catalog.get_module("knowledge_rag_memory")
        first_id = module.constraint_ids[0]
        history = [InterviewTurn(
            id="turn_001", sequence_number=1, target_id="target_01",
            primary_requirement_id="req_01", question_mode="scenario", question="上一题",
            asked_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            question_provenance=QuestionProvenance(
                target_requirement_id="req_01", primary_dimension_id="role_dim_03",
                retrieval_unit_id=module.retrieval_unit_id, scenario_id=module.scenario_id,
                module_id=module.module_id, selected_constraint_id=first_id,
                revealed_constraint_ids=[first_id], retrieval_status="hit",
            ),
        )]
        retriever = FakeRetriever(ScenarioSelection(status="hit", retrieval_unit_id="bad::id"))

        context = prepare_question_context(
            action=action("follow_up"), plan=make_plan("follow_up"), history=history,
            catalog=catalog, retriever=retriever, as_of=self.AS_OF,
        )

        self.assertEqual(retriever.calls, [])
        self.assertIsNotNone(context.selected_constraint)
        self.assertNotEqual(context.selected_constraint.constraint_id, first_id)
        self.assertEqual(context.revealed_constraint_ids, [first_id, context.selected_constraint.constraint_id])
        self.assertEqual(context.provenance.revealed_constraint_ids, context.revealed_constraint_ids)

    def test_follow_up_without_prior_scenario_bypasses(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        retriever = FakeRetriever(ScenarioSelection(status="hit", retrieval_unit_id="bad::id"))

        context = prepare_question_context(
            action=action("follow_up"), plan=make_plan("follow_up"), history=[],
            catalog=catalog, retriever=retriever, as_of=self.AS_OF,
        )

        self.assertIsNone(context)
        self.assertEqual(retriever.calls, [])


if __name__ == "__main__":
    unittest.main()
