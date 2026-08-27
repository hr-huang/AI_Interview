import unittest

from pydantic import ValidationError

from profile_agent.services.interview_planner_service import (
    finalize_interview_plan,
)
from profile_agent.services.plan_review_service import (
    PlanOverrideSet,
    TargetUpdate,
    freeze_reviewed_plan,
)
from tests.test_interview_planner_guards import (
    make_role_profile,
    make_timed_draft,
)


class PlanReviewServiceTest(unittest.TestCase):
    def test_core_target_cannot_be_demoted(self) -> None:
        plan = finalize_interview_plan(make_timed_draft(10), 30)
        target = plan.targets[0]
        overrides = PlanOverrideSet(
            target_updates=[
                TargetUpdate(target_id=target.id, priority="low")
            ]
        )

        with self.assertRaisesRegex(ValueError, "核心目标"):
            freeze_reviewed_plan(plan, overrides, make_role_profile())

    def test_core_target_cannot_be_zero_budget(self) -> None:
        with self.assertRaises(ValidationError):
            TargetUpdate(target_id="target_01", time_budget_minutes=0)

    def test_valid_priority_and_duration_update_rebuilds_blueprint(self) -> None:
        plan = finalize_interview_plan(make_timed_draft(10), 30)
        original_dump = plan.model_dump()
        target = plan.targets[0]
        overrides = PlanOverrideSet(
            duration_minutes=45,
            target_updates=[
                TargetUpdate(
                    target_id=target.id,
                    priority="high",
                    objective="验证 Agent 工作流可靠性",
                    time_budget_minutes=15,
                )
            ],
        )

        final_plan, blueprint = freeze_reviewed_plan(
            plan,
            overrides,
            make_role_profile(),
        )

        self.assertEqual(final_plan.duration_minutes, 45)
        self.assertEqual(final_plan.max_questions, 14)
        self.assertEqual(final_plan.targets[0].time_budget_minutes, 15)
        self.assertEqual(plan.model_dump(), original_dump)
        self.assertEqual(
            {binding.requirement_id for binding in blueprint.bindings},
            {
                requirement.id
                for target in final_plan.targets
                for requirement in target.evidence_requirements
            },
        )

    def test_unknown_and_duplicate_target_updates_are_rejected(self) -> None:
        plan = finalize_interview_plan(make_timed_draft(10), 30)
        duplicate = TargetUpdate(target_id=plan.targets[0].id)
        with self.assertRaisesRegex(ValueError, "重复修改"):
            freeze_reviewed_plan(
                plan,
                PlanOverrideSet(target_updates=[duplicate, duplicate]),
                make_role_profile(),
            )
        with self.assertRaisesRegex(ValueError, "不存在"):
            freeze_reviewed_plan(
                plan,
                PlanOverrideSet(
                    target_updates=[TargetUpdate(target_id="missing")]
                ),
                make_role_profile(),
            )

    def test_invalid_duration_and_excessive_budget_are_rejected(self) -> None:
        plan = finalize_interview_plan(make_timed_draft(10), 30)
        with self.assertRaisesRegex(ValueError, "30、45 或 60"):
            freeze_reviewed_plan(
                plan,
                PlanOverrideSet(duration_minutes=20),
                make_role_profile(),
            )
        with self.assertRaisesRegex(ValueError, "时间预算"):
            freeze_reviewed_plan(
                plan,
                PlanOverrideSet(
                    target_updates=[
                        TargetUpdate(
                            target_id=plan.targets[0].id,
                            time_budget_minutes=29,
                        )
                    ]
                ),
                make_role_profile(),
            )

    def test_custom_target_must_use_existing_role_dimension(self) -> None:
        plan = finalize_interview_plan(make_timed_draft(10), 30)
        custom = make_timed_draft(5).targets[0]
        custom.must_cover = False
        custom.objective = "客服 Agent 上线应急处置"
        custom.evidence_requirements[0].planned_role_dimension_id = (
            "missing_dim"
        )
        with self.assertRaisesRegex(
            ValueError,
            "企业补充目标.*Role Dimension",
        ):
            freeze_reviewed_plan(
                plan,
                PlanOverrideSet(custom_targets=[custom]),
                make_role_profile(),
            )

    def test_custom_target_cannot_claim_core_status(self) -> None:
        plan = finalize_interview_plan(make_timed_draft(10), 30)
        custom = make_timed_draft(5).targets[0]
        custom.must_cover = True
        with self.assertRaisesRegex(
            ValueError,
            "企业补充目标.*must_cover",
        ):
            freeze_reviewed_plan(
                plan,
                PlanOverrideSet(custom_targets=[custom]),
                make_role_profile(),
            )

    def test_valid_custom_target_is_appended_and_bound(self) -> None:
        plan = finalize_interview_plan(make_timed_draft(10), 30)
        custom = make_timed_draft(5).targets[0]
        custom.must_cover = False
        custom.priority = "medium"

        final_plan, blueprint = freeze_reviewed_plan(
            plan,
            PlanOverrideSet(custom_targets=[custom]),
            make_role_profile(),
        )

        self.assertEqual([target.id for target in final_plan.targets], [
            "target_01",
            "custom_01",
        ])
        custom_requirement = final_plan.targets[-1].evidence_requirements[0]
        self.assertEqual(custom_requirement.id, "custom_01_req_01")
        self.assertIn(
            custom_requirement.id,
            {binding.requirement_id for binding in blueprint.bindings},
        )

    def test_custom_target_requires_time_and_evidence(self) -> None:
        plan = finalize_interview_plan(make_timed_draft(10), 30)
        custom = make_timed_draft(5).targets[0]
        custom.must_cover = False
        custom.time_budget_minutes = 0
        with self.assertRaisesRegex(ValueError, "时间预算"):
            freeze_reviewed_plan(
                plan,
                PlanOverrideSet(custom_targets=[custom]),
                make_role_profile(),
            )

        custom.time_budget_minutes = 5
        custom.evidence_requirements = []
        with self.assertRaisesRegex(ValueError, "证据要求"):
            freeze_reviewed_plan(
                plan,
                PlanOverrideSet(custom_targets=[custom]),
                make_role_profile(),
            )


if __name__ == "__main__":
    unittest.main()
