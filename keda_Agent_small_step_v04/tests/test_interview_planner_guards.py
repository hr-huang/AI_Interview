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


class InterviewPlannerGuardTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
