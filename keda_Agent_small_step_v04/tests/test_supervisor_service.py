from datetime import datetime, timedelta, timezone
import unittest

from profile_agent.schemas.claim_schema import ClaimItem, ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AskAction,
    AssessmentTarget,
    EvidenceRequirement,
    FinishAction,
    InterviewPlan,
)
from profile_agent.schemas.runtime_schema import (
    Evidence,
    InterviewRuntimeState,
    InterviewTurn,
    RequirementProgress,
)
from profile_agent.services.supervisor_service import (
    CandidateRequirement,
    SupervisorContext,
    SupervisorRequirementContext,
    build_supervisor_context,
    decide_next_action,
)


STARTED_AT = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def make_target(
    target_id: str,
    requirement_ids: list[str],
    *,
    priority: str = "medium",
    must_cover: bool = False,
    related_claim_ids: list[str] | None = None,
    preferred_modes: list[str] | None = None,
) -> AssessmentTarget:
    return AssessmentTarget(
        id=target_id,
        objective=f"验证 {target_id}",
        target_type="implementation",
        competency_ids=[],
        evidence_requirements=[
            EvidenceRequirement(
                id=requirement_id,
                description=f"描述 {requirement_id}",
            )
            for requirement_id in requirement_ids
        ],
        related_claim_ids=related_claim_ids or [],
        priority=priority,
        must_cover=must_cover,
        time_budget_minutes=5,
        preferred_modes=preferred_modes or ["foundation", "scenario"],
    )


def make_plan(
    targets: list[AssessmentTarget] | None = None,
    *,
    duration_minutes: int = 30,
    max_questions: int = 6,
    closing_buffer_minutes: int = 2,
) -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=duration_minutes,
        max_questions=max_questions,
        closing_buffer_minutes=closing_buffer_minutes,
        targets=targets
        or [
            make_target(
                "target_01",
                ["target_01_req_01"],
                priority="high",
                must_cover=True,
                related_claim_ids=["claim_01"],
                preferred_modes=["foundation", "project_deep_dive", "scenario"],
            )
        ],
    )


def make_runtime(
    plan: InterviewPlan,
    *,
    statuses: dict[str, str] | None = None,
    attempts: dict[str, int] | None = None,
    question_count: int = 0,
    current_target_id: str | None = None,
    stop_requested: bool = False,
    stop_reason: str | None = None,
    started_at: datetime = STARTED_AT,
) -> InterviewRuntimeState:
    statuses = statuses or {}
    attempts = attempts or {}
    progress: dict[str, RequirementProgress] = {}
    for target in plan.targets:
        for requirement in target.evidence_requirements:
            requirement_id = requirement.id
            progress[requirement_id] = RequirementProgress(
                requirement_id=requirement_id,
                status=statuses.get(requirement_id, "not_started"),
                attempt_count=attempts.get(requirement_id, 0),
            )

    return InterviewRuntimeState(
        question_count=question_count,
        started_at=started_at,
        current_target_id=current_target_id,
        requirement_progress=progress,
        stop_requested=stop_requested,
        stop_reason=stop_reason,
    )


def make_turn(sequence_number: int, requirement_id: str) -> InterviewTurn:
    return InterviewTurn(
        id=f"turn_{sequence_number:02d}",
        sequence_number=sequence_number,
        target_id="target_01",
        primary_requirement_id=requirement_id,
        question_mode="scenario",
        question=f"问题 {sequence_number}",
        answer=f"回答 {sequence_number}",
        asked_at=STARTED_AT + timedelta(seconds=sequence_number),
    )


def make_claim_registry() -> ClaimRegistry:
    return ClaimRegistry(
        claims=[
            ClaimItem(
                id="claim_01",
                text="候选人声称实现过 Agent Workflow",
                source_section="project",
                claim_type="experience",
            )
        ]
    )


class SupervisorContextTest(unittest.TestCase):
    def test_context_contains_bounded_runtime_and_resolved_recent_context(self) -> None:
        plan = make_plan(
            targets=[
                make_target(
                    "target_01",
                    ["target_01_req_01"],
                    related_claim_ids=["claim_01"],
                )
            ],
            max_questions=6,
        )
        runtime = make_runtime(
            plan,
            question_count=2,
            current_target_id="target_01",
        )
        turns = [make_turn(index, "target_01_req_01") for index in range(1, 5)]
        evidences = [
            Evidence(
                id="evidence_01",
                turn_id="turn_01",
                requirement_ids=["target_01_req_01"],
                polarity="supporting",
                strength="strong",
                observation="能够解释 State 数据流",
                source_excerpt="回答中的关键摘录",
            )
        ]

        context = build_supervisor_context(
            plan,
            runtime,
            turns,
            evidences,
            claim_registry=make_claim_registry(),
            now=STARTED_AT + timedelta(seconds=90),
        )

        self.assertIsInstance(context, SupervisorContext)
        self.assertEqual(context.remaining_seconds, 30 * 60 - 90)
        self.assertEqual(context.remaining_questions, 4)
        self.assertEqual(context.closing_buffer_seconds, 2 * 60)
        self.assertEqual(context.current_target_id, "target_01")
        self.assertEqual(
            [turn.id for turn in context.recent_turns],
            ["turn_02", "turn_03", "turn_04"],
        )
        self.assertEqual(len(context.candidates), 1)

        candidate = context.candidates[0]
        self.assertIs(CandidateRequirement, SupervisorRequirementContext)
        self.assertEqual(
            set(candidate.model_dump()),
            {
                "target_id",
                "target_objective",
                "requirement_id",
                "requirement_description",
                "priority",
                "must_cover",
                "status",
                "attempt_count",
                "preferred_modes",
                "related_claims",
                "evidence_summaries",
            },
        )
        self.assertIn("candidates", context.model_dump())
        self.assertNotIn("candidate_requirements", context.model_dump())
        self.assertEqual(candidate.target_id, "target_01")
        self.assertEqual(candidate.target_objective, "验证 target_01")
        self.assertEqual(candidate.requirement_id, "target_01_req_01")
        self.assertEqual(candidate.requirement_description, "描述 target_01_req_01")
        self.assertEqual(candidate.priority, "medium")
        self.assertFalse(candidate.must_cover)
        self.assertEqual(candidate.status, "not_started")
        self.assertEqual(candidate.attempt_count, 0)
        self.assertEqual(candidate.preferred_modes, ["foundation", "scenario"])
        self.assertEqual(
            candidate.related_claims,
            ["候选人声称实现过 Agent Workflow"],
        )
        self.assertEqual(candidate.evidence_summaries, ["能够解释 State 数据流"])

    def test_context_keeps_only_the_last_three_turns_even_when_history_is_longer(self) -> None:
        plan = make_plan()
        runtime = make_runtime(plan)
        turns = [make_turn(index, "target_01_req_01") for index in range(1, 8)]

        context = build_supervisor_context(
            plan, runtime, turns, [], now=STARTED_AT
        )

        self.assertEqual(
            [turn.sequence_number for turn in context.recent_turns],
            [5, 6, 7],
        )

    def test_context_rejects_missing_or_extra_runtime_requirement_ids(self) -> None:
        plan = make_plan(
            targets=[
                make_target("target_01", ["req_01", "req_02"]),
            ]
        )
        missing = InterviewRuntimeState(
            started_at=STARTED_AT,
            requirement_progress={
                "req_01": RequirementProgress(requirement_id="req_01"),
            },
        )
        extra = InterviewRuntimeState(
            started_at=STARTED_AT,
            requirement_progress={
                "req_01": RequirementProgress(requirement_id="req_01"),
                "req_02": RequirementProgress(requirement_id="req_02"),
                "req_extra": RequirementProgress(requirement_id="req_extra"),
            },
        )

        with self.assertRaisesRegex(ValueError, "requirement.*完全一致"):
            build_supervisor_context(plan, missing, [], [], now=STARTED_AT)
        with self.assertRaisesRegex(ValueError, "requirement.*完全一致"):
            build_supervisor_context(plan, extra, [], [], now=STARTED_AT)

    def test_context_rejects_progress_key_that_disagrees_with_nested_requirement_id(self) -> None:
        plan = make_plan()
        runtime = make_runtime(plan)
        runtime.requirement_progress["target_01_req_01"].requirement_id = "other_req"

        with self.assertRaisesRegex(ValueError, "key.*requirement_id"):
            build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

    def test_context_uses_custom_attempt_limit_for_candidate_filtering(self) -> None:
        plan = make_plan()
        runtime = make_runtime(plan, attempts={"target_01_req_01": 1})

        context = build_supervisor_context(
            plan,
            runtime,
            [],
            [],
            now=STARTED_AT,
            max_attempts=1,
        )

        self.assertEqual(context.candidates, [])


class CandidateSelectionTest(unittest.TestCase):
    def test_candidates_exclude_sufficient_skipped_and_attempt_limited_requirements(self) -> None:
        targets = [
            make_target("target_01", ["req_sufficient", "req_skipped"]),
            make_target("target_02", ["req_limited", "req_available"]),
        ]
        plan = make_plan(targets=targets)
        runtime = make_runtime(
            plan,
            statuses={
                "req_sufficient": "sufficient",
                "req_skipped": "skipped",
                "req_limited": "in_progress",
                "req_available": "not_started",
            },
            attempts={"req_limited": 2},
        )

        context = build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

        self.assertEqual(
            [candidate.requirement_id for candidate in context.candidates],
            ["req_available"],
        )

    def test_candidates_sort_by_all_priority_tiers_before_plan_order(self) -> None:
        targets = [
            make_target("target_01", ["optional_claim"], priority="high", related_claim_ids=["claim_01"]),
            make_target("target_02", ["optional_active", "optional_current"], priority="high"),
            make_target("target_03", ["optional_first"], priority="high"),
            make_target("target_04", ["must_low"], priority="low", must_cover=True),
            make_target("target_05", ["must_high", "must_high_active"], priority="high", must_cover=True),
        ]
        plan = make_plan(targets=targets)
        runtime = make_runtime(
            plan,
            statuses={"optional_active": "contradictory", "must_high_active": "in_progress"},
            attempts={"optional_active": 1, "must_high_active": 1},
            current_target_id="target_02",
        )

        context = build_supervisor_context(
            plan,
            runtime,
            [],
            [],
            claim_registry=make_claim_registry(),
            now=STARTED_AT,
        )

        self.assertEqual(
            [candidate.requirement_id for candidate in context.candidates],
            [
                "must_high_active",
                "must_high",
                "must_low",
                "optional_active",
                "optional_claim",
                "optional_current",
                "optional_first",
            ],
        )

    def test_sort_ties_use_the_plan_requirement_order(self) -> None:
        targets = [
            make_target("target_01", ["req_02", "req_01"]),
        ]
        plan = make_plan(targets=targets)
        runtime = make_runtime(plan)

        context = build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

        self.assertEqual(
            [candidate.requirement_id for candidate in context.candidates],
            ["req_02", "req_01"],
        )


class SupervisorDecisionTest(unittest.TestCase):
    def test_stop_requested_has_highest_hard_stop_priority(self) -> None:
        plan = make_plan()
        runtime = make_runtime(
            plan,
            question_count=plan.max_questions,
            stop_requested=True,
            stop_reason="manual_stop",
        )
        context = build_supervisor_context(
            plan,
            runtime,
            [],
            [],
            now=STARTED_AT + timedelta(minutes=29),
        )

        action = decide_next_action(context)

        self.assertIsInstance(action, FinishAction)
        self.assertIn("manual_stop", action.reason)

    def test_entering_closing_buffer_finishes_before_question_limit(self) -> None:
        plan = make_plan()
        runtime = make_runtime(plan, question_count=plan.max_questions - 1)
        context = build_supervisor_context(
            plan,
            runtime,
            [],
            [],
            now=STARTED_AT + timedelta(minutes=28),
        )

        action = decide_next_action(context)

        self.assertIsInstance(action, FinishAction)
        self.assertIn("closing buffer", action.reason)

    def test_question_exhaustion_finishes_before_coverage_and_candidate_checks(self) -> None:
        plan = make_plan()
        runtime = make_runtime(plan, question_count=plan.max_questions)
        context = build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

        action = decide_next_action(context)

        self.assertIsInstance(action, FinishAction)
        self.assertIn("question", action.reason)

    def test_all_must_cover_requirements_sufficient_finishes_before_optional_candidates(self) -> None:
        plan = make_plan(
            targets=[
                make_target("target_01", ["must_req"], must_cover=True),
                make_target("target_02", ["optional_req"], must_cover=False),
            ]
        )
        runtime = make_runtime(plan, statuses={"must_req": "sufficient"})
        context = build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

        action = decide_next_action(context)

        self.assertIsInstance(action, FinishAction)
        self.assertIn("must_cover", action.reason)

    def test_no_candidates_finishes_when_no_hard_stop_precedes_it(self) -> None:
        plan = make_plan()
        runtime = make_runtime(
            plan,
            statuses={"target_01_req_01": "skipped"},
        )
        context = build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

        action = decide_next_action(context)

        self.assertIsInstance(action, FinishAction)
        self.assertIn("candidate", action.reason)

    def test_attempted_incomplete_requirement_uses_follow_up(self) -> None:
        plan = make_plan(
            targets=[
                make_target(
                    "target_01",
                    ["req_01"],
                    preferred_modes=["foundation", "scenario"],
                )
            ]
        )
        runtime = make_runtime(
            plan,
            statuses={"req_01": "in_progress"},
            attempts={"req_01": 1},
        )
        context = build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

        action = decide_next_action(context)

        self.assertIsInstance(action, AskAction)
        self.assertEqual(action.question_mode, "follow_up")

    def test_in_progress_with_zero_attempts_uses_preferred_mode_not_follow_up(self) -> None:
        plan = make_plan(
            targets=[
                make_target(
                    "target_01",
                    ["req_01"],
                    preferred_modes=["foundation", "scenario"],
                )
            ]
        )
        runtime = make_runtime(
            plan,
            statuses={"req_01": "in_progress"},
            attempts={"req_01": 0},
        )
        context = build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

        action = decide_next_action(context)

        self.assertIsInstance(action, AskAction)
        self.assertEqual(action.question_mode, "foundation")

    def test_positive_attempt_on_not_started_uses_preferred_mode_not_follow_up(self) -> None:
        plan = make_plan(
            targets=[
                make_target(
                    "target_01",
                    ["req_01"],
                    preferred_modes=["follow_up", "system_design"],
                )
            ]
        )
        runtime = make_runtime(
            plan,
            statuses={"req_01": "not_started"},
            attempts={"req_01": 1},
        )
        context = build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

        action = decide_next_action(context)

        self.assertIsInstance(action, AskAction)
        self.assertEqual(action.question_mode, "system_design")

    def test_first_claim_linked_requirement_prefers_project_deep_dive(self) -> None:
        plan = make_plan(
            targets=[
                make_target(
                    "target_01",
                    ["req_01"],
                    related_claim_ids=["claim_01"],
                    preferred_modes=["foundation", "project_deep_dive"],
                )
            ]
        )
        runtime = make_runtime(plan)
        context = build_supervisor_context(
            plan,
            runtime,
            [],
            [],
            claim_registry=make_claim_registry(),
            now=STARTED_AT,
        )

        action = decide_next_action(context)

        self.assertIsInstance(action, AskAction)
        self.assertEqual(action.question_mode, "project_deep_dive")

    def test_first_requirement_uses_first_non_follow_up_preferred_mode(self) -> None:
        plan = make_plan(
            targets=[
                make_target(
                    "target_01",
                    ["req_01"],
                    preferred_modes=["follow_up", "system_design", "scenario"],
                )
            ]
        )
        runtime = make_runtime(plan)
        context = build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

        action = decide_next_action(context)

        self.assertIsInstance(action, AskAction)
        self.assertEqual(action.question_mode, "system_design")

    def test_first_requirement_falls_back_to_scenario_without_non_follow_up_mode(self) -> None:
        plan = make_plan(
            targets=[
                make_target(
                    "target_01",
                    ["req_01"],
                    preferred_modes=["follow_up"],
                )
            ]
        )
        runtime = make_runtime(plan)
        context = build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

        action = decide_next_action(context)

        self.assertIsInstance(action, AskAction)
        self.assertEqual(action.question_mode, "scenario")

    def test_decision_exposes_selected_target_and_primary_requirement(self) -> None:
        plan = make_plan()
        runtime = make_runtime(plan)
        context = build_supervisor_context(plan, runtime, [], [], now=STARTED_AT)

        action = decide_next_action(context)

        self.assertIsInstance(action, AskAction)
        self.assertEqual(action.target_id, "target_01")
        self.assertEqual(action.primary_requirement_id, "target_01_req_01")
        self.assertTrue(action.reason)


if __name__ == "__main__":
    unittest.main()
