import unittest
from datetime import date
from unittest.mock import patch

from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.competency_schema import CompetencyItem, CompetencyModel
from profile_agent.schemas.interview_schema import (
    AssessmentTargetDraft,
    EvidenceRequirementDraft,
    InterviewPlanDraft,
)
from profile_agent.schemas.report_schema import (
    CompetencyDimensionRubric,
    RoleCompetencyProfile,
    RubricCriterion,
)
from profile_agent.services import interview_planner_service


def make_oversized_draft() -> InterviewPlanDraft:
    return InterviewPlanDraft(
        targets=[
            AssessmentTargetDraft(
                objective=f"目标 {index}",
                target_type="knowledge",
                competency_ids=[],
                evidence_requirements=[],
                related_claim_ids=[],
                priority="low",
                must_cover=False,
                time_budget_minutes=0,
                preferred_modes=["foundation"],
            )
            for index in range(6)
        ]
    )


def make_timed_draft(minutes: int) -> InterviewPlanDraft:
    return InterviewPlanDraft(
        targets=[
            AssessmentTargetDraft(
                objective="验证核心能力",
                target_type="knowledge",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirementDraft(
                        description="在约束不同的新场景中迁移方法",
                        planned_role_dimension_id="role_dim_gate",
                        requires_transfer_validation=True,
                    )
                ],
                related_claim_ids=[],
                priority="high",
                must_cover=True,
                time_budget_minutes=minutes,
                preferred_modes=["foundation", "scenario"],
            )
        ]
    )


def make_role_profile() -> RoleCompetencyProfile:
    return RoleCompetencyProfile(
        role_family="ai_application_engineering",
        display_name="AI Agent / AI应用工程师",
        version="2026-H2-test",
        valid_from=date(2026, 7, 1),
        knowledge_as_of=date(2026, 8, 24),
        source_refs=["test-role-pack"],
        dimensions=[
            CompetencyDimensionRubric(
                id="role_dim_gate",
                name="可靠性与安全",
                weight=0.6,
                is_gating=True,
                minimum_criteria=[
                    RubricCriterion(id="gate_min_01", text="失败恢复")
                ],
            ),
            CompetencyDimensionRubric(
                id="role_dim_optional",
                name="持续进化",
                weight=0.4,
                is_gating=False,
                minimum_criteria=[
                    RubricCriterion(id="optional_min_01", text="复盘优化")
                ],
            ),
        ],
    )


class InterviewPlannerGuardTest(unittest.TestCase):
    def test_finalization_preserves_dimension_and_transfer_intent(self) -> None:
        draft = make_timed_draft(10)
        draft.targets[0].evidence_requirements = [
            EvidenceRequirementDraft(
                description="将方法迁移到受监管新场景",
                planned_role_dimension_id="role_dim_gate",
                requires_transfer_validation=True,
            )
        ]

        plan = interview_planner_service.finalize_interview_plan(draft, 30)

        requirement = plan.targets[0].evidence_requirements[0]
        self.assertEqual(requirement.planned_role_dimension_id, "role_dim_gate")
        self.assertTrue(requirement.requires_transfer_validation)

    def test_gating_role_dimensions_must_have_prioritized_requirements(self) -> None:
        draft = make_timed_draft(10)
        draft.targets[0].evidence_requirements = [
            EvidenceRequirementDraft(
                description="只覆盖可选维度",
                planned_role_dimension_id="role_dim_optional",
            )
        ]

        with self.assertRaisesRegex(ValueError, "gating Role Dimension"):
            interview_planner_service.validate_role_dimension_coverage(
                draft,
                make_role_profile(),
            )

    def test_unknown_role_dimension_is_rejected(self) -> None:
        draft = make_timed_draft(10)
        draft.targets[0].evidence_requirements[0].planned_role_dimension_id = (
            "role_dim_missing"
        )

        with self.assertRaisesRegex(ValueError, "Role Dimension ID"):
            interview_planner_service.validate_role_dimension_coverage(
                draft,
                make_role_profile(),
            )

    def test_project_claim_target_requires_transfer_scenario(self) -> None:
        draft = make_timed_draft(10)
        target = draft.targets[0]
        target.related_claim_ids = ["claim_01"]
        target.preferred_modes = ["project_deep_dive", "foundation"]
        target.evidence_requirements = [
            EvidenceRequirementDraft(
                description="解释原项目状态设计",
                planned_role_dimension_id="role_dim_gate",
            )
        ]

        with self.assertRaisesRegex(ValueError, "迁移"):
            interview_planner_service.validate_transfer_coverage(
                draft,
                interview_planner_service.DEFAULT_INTERVIEW_POLICY,
            )

    def test_transfer_target_requires_scenario_mode(self) -> None:
        draft = make_timed_draft(10)
        draft.targets[0].preferred_modes = ["foundation"]

        with self.assertRaisesRegex(ValueError, "scenario"):
            interview_planner_service.validate_transfer_coverage(
                draft,
                interview_planner_service.DEFAULT_INTERVIEW_POLICY,
            )

    def test_core_competencies_must_be_in_high_must_cover_targets(self) -> None:
        competency_model = CompetencyModel(
            competencies=[
                CompetencyItem(
                    id="competency_01",
                    name="系统可靠性",
                    importance="core",
                    target_expectation="能够处理失败恢复与安全授权",
                )
            ]
        )
        draft = make_timed_draft(10)
        draft.targets[0].competency_ids = ["competency_01"]
        draft.targets[0].priority = "medium"
        draft.targets[0].must_cover = False

        with self.assertRaisesRegex(ValueError, "core Competency"):
            interview_planner_service.validate_core_coverage(
                draft,
                competency_model,
            )

    def test_high_priority_requirements_leave_two_questions_for_followups(self) -> None:
        draft = make_timed_draft(10)
        draft.targets[0].evidence_requirements = [
            EvidenceRequirementDraft(description=f"证据 {index}")
            for index in range(9)
        ]

        with self.assertRaisesRegex(ValueError, "动态追问"):
            interview_planner_service.validate_question_capacity(
                draft,
                max_questions=10,
            )

    def test_prompt_forbids_question_modes_in_target_type(self) -> None:
        captured_messages = []

        def fake_structured(messages, _schema):
            captured_messages.extend(messages)
            return make_timed_draft(0)

        with patch.object(
            interview_planner_service.llm,
            "structured",
            side_effect=fake_structured,
        ):
            interview_planner_service.build_interview_plan(
                competency_model=CompetencyModel(),
                claim_registry=ClaimRegistry(),
                duration_minutes=30,
                role_profile=make_role_profile(),
            )

        system_prompt = captured_messages[0][1]
        self.assertIn("target_type 严禁使用任何 QuestionMode", system_prompt)
        self.assertIn("project_deep_dive 只能出现在 preferred_modes", system_prompt)
        self.assertIn("scenario 只能出现在 preferred_modes", system_prompt)
        self.assertIn('正确示例: "target_type": "problem_solving", "preferred_modes": ["scenario"]', system_prompt)
        self.assertIn('错误示例: "target_type": "scenario"', system_prompt)
        self.assertIn("targets 数量绝不能超过 InterviewPolicy.max_targets", system_prompt)
        self.assertIn(
            "至少一个 Evidence Requirement 必须验证新场景迁移或适配",
            system_prompt,
        )
        self.assertIn("迁移 Requirement 必须放在 high、must_cover 的核心 Target", system_prompt)
        self.assertIn("planned_role_dimension_id", system_prompt)

    def test_build_plan_rejects_more_targets_than_policy_allows(self) -> None:
        with patch.object(
            interview_planner_service.llm,
            "structured",
            return_value=make_oversized_draft(),
        ):
            with self.assertRaisesRegex(ValueError, "Target数量过多"):
                interview_planner_service.build_interview_plan(
                    competency_model=CompetencyModel(),
                    claim_registry=ClaimRegistry(),
                    duration_minutes=30,
                    role_profile=make_role_profile(),
                )

    def test_business_validation_failure_is_retried_with_exact_budget_reason(self) -> None:
        captured_messages = []

        def fake_structured(messages, _schema):
            captured_messages.append(messages)
            return make_timed_draft(29 if len(captured_messages) == 1 else 28)

        with patch.object(
            interview_planner_service.llm,
            "structured",
            side_effect=fake_structured,
        ):
            plan = interview_planner_service.build_interview_plan(
                competency_model=CompetencyModel(),
                claim_registry=ClaimRegistry(),
                duration_minutes=30,
                role_profile=make_role_profile(),
            )

        self.assertEqual(len(captured_messages), 2)
        self.assertIn("上一轮 InterviewPlanDraft 未通过业务校验", captured_messages[1][-1][1])
        self.assertIn("当前规划: 29 分钟", captured_messages[1][-1][1])
        self.assertEqual(plan.targets[0].time_budget_minutes, 28)


if __name__ == "__main__":
    unittest.main()
