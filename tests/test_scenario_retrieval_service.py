from datetime import date
import unittest

from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    AskAction,
    EvidenceRequirement,
    InterviewPlan,
)
from profile_agent.services.scenario_retrieval_service import (
    build_scenario_retrieval_request,
    select_fallback_module,
    validate_scenario_selection,
)
from profile_agent.services.scenario_bank_service import ScenarioCatalog
from profile_agent.schemas.scenario_rag_schema import ScenarioSelection


ROOT = __import__("pathlib").Path(__file__).resolve().parents[1] / "profile_agent" / "knowledge" / "scenario_banks" / "ai_application_engineering_2026_h2"


def make_plan(dimension: str = "role_dim_03", description: str = "验证 Memory 写入和删除边界") -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=5,
        closing_buffer_minutes=0,
        targets=[
            AssessmentTarget(
                id="target_01",
                objective="验证候选人能否设计可靠的 Context 和 Memory 业务链路",
                target_type="system_design",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id="req_01",
                        description=description,
                        planned_role_dimension_id=dimension,
                    )
                ],
                related_claim_ids=[],
                priority="high",
                must_cover=True,
                time_budget_minutes=10,
                preferred_modes=["system_design"],
            )
        ],
    )


class ScenarioRetrievalServiceTests(unittest.TestCase):
    def test_request_uses_current_gap_without_raw_profile_text(self) -> None:
        request = build_scenario_retrieval_request(
            action=AskAction(
                target_id="target_01",
                primary_requirement_id="req_01",
                question_mode="system_design",
                reason="需要验证长期记忆和删除边界",
            ),
            plan=make_plan(),
            evidence_gap_tags=["长期记忆", "隐私删除"],
        )
        self.assertEqual(request.role_family, "ai_application_engineering")
        self.assertEqual(request.role_profile_version, "2026-H2")
        self.assertEqual(request.primary_dimension_id, "role_dim_03")
        self.assertIn("长期记忆", request.semantic_query)
        self.assertEqual(
            request.semantic_query,
            "\n".join(
                [
                    "验证候选人能否设计可靠的 Context 和 Memory 业务链路",
                    "验证 Memory 写入和删除边界",
                    "长期记忆",
                    "隐私删除",
                    "需要验证长期记忆和删除边界",
                ]
            ),
        )

    def test_validator_rejects_cross_dimension_result(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=date(2026, 8, 29))
        request = build_scenario_retrieval_request(
            action=AskAction(
                target_id="target_01",
                primary_requirement_id="req_01",
                question_mode="system_design",
                reason="验证可观测性",
            ),
            plan=make_plan("role_dim_03", "验证 Memory 检索"),
        )
        selection = ScenarioSelection(
            status="hit",
            retrieval_unit_id="enterprise_cost_monitor::cost_monitor_observability",
            scenario_id="enterprise_cost_monitor",
            module_id="cost_monitor_observability",
        )
        with self.assertRaisesRegex(ValueError, "primary_dimension_id"):
            validate_scenario_selection(request, selection, catalog, date(2026, 8, 29))

    def test_fallback_is_a_reviewed_module_with_all_hard_filters(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=date(2026, 8, 29))
        request = build_scenario_retrieval_request(
            action=AskAction(
                target_id="target_01",
                primary_requirement_id="req_01",
                question_mode="system_design",
                reason="验证 Memory",
            ),
            plan=make_plan("role_dim_03", "验证 Memory 检索和更新"),
        )
        selection = select_fallback_module(request, catalog, "qdrant unavailable")
        self.assertEqual(selection.status, "fallback")
        self.assertEqual(selection.module.primary_dimension_id, "role_dim_03")
        self.assertIn("qdrant unavailable", selection.fallback_reason)
        self.assertIn("system_design", selection.module.supported_modes)
        self.assertIn("system_design", selection.module.supported_requirement_types)


if __name__ == "__main__":
    unittest.main()
