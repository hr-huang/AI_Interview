from datetime import datetime, timedelta, timezone
import unittest

from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
    InterviewPlan,
)
from profile_agent.services.runtime_state_service import (
    calculate_remaining_seconds,
    initialize_runtime_state,
)


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=10,
        closing_buffer_minutes=2,
        targets=[
            AssessmentTarget(
                id="target_01",
                objective="验证 Agent Workflow 设计能力",
                target_type="implementation",
                competency_ids=["competency_01"],
                evidence_requirements=[
                    EvidenceRequirement(
                        id="target_01_req_01",
                        description="能够解释 Workflow 数据流",
                    ),
                    EvidenceRequirement(
                        id="target_01_req_02",
                        description="能够解释 State 设计",
                    ),
                ],
                related_claim_ids=["claim_01"],
                priority="high",
                must_cover=True,
                time_budget_minutes=10,
                preferred_modes=["project_deep_dive", "scenario"],
            )
        ],
    )


class RuntimeInitializationTest(unittest.TestCase):
    def test_initialize_creates_one_progress_per_requirement(self) -> None:
        started_at = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

        runtime = initialize_runtime_state(make_plan(), started_at=started_at)

        self.assertEqual(runtime.started_at, started_at)
        self.assertEqual(
            list(runtime.requirement_progress),
            ["target_01_req_01", "target_01_req_02"],
        )
        self.assertTrue(
            all(
                item.status == "not_started"
                for item in runtime.requirement_progress.values()
            )
        )
        self.assertTrue(
            all(
                requirement_id == progress.requirement_id
                for requirement_id, progress in runtime.requirement_progress.items()
            )
        )

    def test_duplicate_requirement_id_is_rejected(self) -> None:
        plan = make_plan()
        plan.targets[0].evidence_requirements.append(
            EvidenceRequirement(
                id="target_01_req_01",
                description="重复 ID",
            )
        )

        with self.assertRaisesRegex(ValueError, "重复的 requirement_id"):
            initialize_runtime_state(plan)

    def test_empty_plan_is_rejected(self) -> None:
        plan = InterviewPlan(
            duration_minutes=30,
            max_questions=10,
            closing_buffer_minutes=2,
            targets=[],
        )

        with self.assertRaisesRegex(ValueError, "至少包含一个 Target"):
            initialize_runtime_state(plan)

    def test_remaining_seconds_is_derived_from_clock(self) -> None:
        started_at = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        runtime = initialize_runtime_state(make_plan(), started_at=started_at)

        remaining = calculate_remaining_seconds(
            make_plan(),
            runtime,
            now=started_at + timedelta(minutes=4, seconds=30),
        )

        self.assertEqual(remaining, 25 * 60 + 30)

    def test_remaining_seconds_never_becomes_negative(self) -> None:
        started_at = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        runtime = initialize_runtime_state(make_plan(), started_at=started_at)

        remaining = calculate_remaining_seconds(
            make_plan(),
            runtime,
            now=started_at + timedelta(minutes=40),
        )

        self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
