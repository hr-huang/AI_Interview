import unittest
from unittest.mock import patch

from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.competency_schema import CompetencyModel
from profile_agent.schemas.interview_schema import (
    AssessmentTargetDraft,
    InterviewPlanDraft,
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
                evidence_requirements=[],
                related_claim_ids=[],
                priority="high",
                must_cover=True,
                time_budget_minutes=minutes,
                preferred_modes=["foundation"],
            )
        ]
    )


class InterviewPlannerGuardTest(unittest.TestCase):
    def test_prompt_forbids_question_modes_in_target_type(self) -> None:
        captured_messages = []

        def fake_structured(messages, _schema):
            captured_messages.extend(messages)
            return InterviewPlanDraft(targets=[])

        with patch.object(
            interview_planner_service.llm,
            "structured",
            side_effect=fake_structured,
        ):
            interview_planner_service.build_interview_plan(
                competency_model=CompetencyModel(),
                claim_registry=ClaimRegistry(),
                duration_minutes=30,
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
            )

        self.assertEqual(len(captured_messages), 2)
        self.assertIn("上一轮 InterviewPlanDraft 未通过业务校验", captured_messages[1][-1][1])
        self.assertIn("当前规划: 29 分钟", captured_messages[1][-1][1])
        self.assertEqual(plan.targets[0].time_budget_minutes, 28)


if __name__ == "__main__":
    unittest.main()
