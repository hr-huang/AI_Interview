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
    record_question_asked,
    record_requirement_evidence,
    request_stop,
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


class RuntimeUpdateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = make_plan()
        self.runtime = initialize_runtime_state(self.plan)

    def test_record_question_updates_primary_requirement(self) -> None:
        updated = record_question_asked(
            self.plan,
            self.runtime,
            target_id="target_01",
            primary_requirement_id="target_01_req_01",
        )

        self.assertEqual(updated.question_count, 1)
        self.assertEqual(updated.current_target_id, "target_01")
        self.assertEqual(updated.visited_target_ids, ["target_01"])
        self.assertEqual(
            updated.requirement_progress["target_01_req_01"].attempt_count,
            1,
        )
        self.assertEqual(
            updated.requirement_progress["target_01_req_01"].status,
            "in_progress",
        )
        self.assertEqual(self.runtime.question_count, 0)

    def test_record_question_rejects_when_stop_was_requested(self) -> None:
        stopped = request_stop(self.runtime, "manual_stop")

        with self.assertRaisesRegex(ValueError, "stop_requested"):
            record_question_asked(
                self.plan,
                stopped,
                target_id="target_01",
                primary_requirement_id="target_01_req_01",
            )

    def test_record_question_rejects_when_interview_time_is_exhausted(self) -> None:
        now = self.runtime.started_at + timedelta(
            minutes=self.plan.duration_minutes
        )

        with self.assertRaisesRegex(ValueError, "时间已耗尽"):
            record_question_asked(
                self.plan,
                self.runtime,
                target_id="target_01",
                primary_requirement_id="target_01_req_01",
                now=now,
            )

    def test_record_question_rejects_missing_runtime_progress(self) -> None:
        runtime = self.runtime.model_copy(update={"requirement_progress": {}})

        with self.assertRaisesRegex(ValueError, "requirement_progress"):
            record_question_asked(
                self.plan,
                runtime,
                target_id="target_01",
                primary_requirement_id="target_01_req_01",
            )

    def test_record_question_deep_copies_nested_runtime_fields(self) -> None:
        updated = record_question_asked(
            self.plan,
            self.runtime,
            target_id="target_01",
            primary_requirement_id="target_01_req_01",
        )

        original_progress = self.runtime.requirement_progress[
            "target_01_req_01"
        ]
        updated_progress = updated.requirement_progress["target_01_req_01"]
        self.assertEqual(self.runtime.visited_target_ids, [])
        self.assertEqual(original_progress.attempt_count, 0)
        self.assertIsNot(
            self.runtime.visited_target_ids,
            updated.visited_target_ids,
        )
        self.assertIsNot(original_progress, updated_progress)


    def test_unknown_requirement_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "不存在的 requirement_id"):
            record_question_asked(
                self.plan,
                self.runtime,
                target_id="target_01",
                primary_requirement_id="target_99_req_01",
            )

    def test_mismatched_progress_key_is_rejected(self) -> None:
        self.runtime.requirement_progress[
            "target_01_req_01"
        ].requirement_id = "target_01_req_02"

        with self.assertRaisesRegex(ValueError, "key.*requirement_id"):
            record_requirement_evidence(
                self.runtime,
                requirement_id="target_01_req_01",
                status="in_progress",
                supporting_evidence_ids=[],
                contradicting_evidence_ids=[],
                known_evidence_ids=set(),
            )

    def test_question_limit_is_enforced(self) -> None:
        self.runtime.question_count = self.plan.max_questions

        with self.assertRaisesRegex(ValueError, "达到问题数量上限"):
            record_question_asked(
                self.plan,
                self.runtime,
                target_id="target_01",
                primary_requirement_id="target_01_req_01",
            )

    def test_evidence_is_validated_deduplicated_and_linked(self) -> None:
        updated = record_requirement_evidence(
            self.runtime,
            requirement_id="target_01_req_01",
            status="sufficient",
            supporting_evidence_ids=["evidence_01", "evidence_01"],
            contradicting_evidence_ids=[],
            known_evidence_ids={"evidence_01"},
        )

        progress = updated.requirement_progress["target_01_req_01"]
        self.assertEqual(progress.status, "sufficient")
        self.assertEqual(progress.supporting_evidence_ids, ["evidence_01"])

    def test_evidence_id_cannot_be_both_supporting_and_contradicting(self) -> None:
        with self.assertRaisesRegex(ValueError, "supporting.*contradicting"):
            record_requirement_evidence(
                self.runtime,
                requirement_id="target_01_req_01",
                status="contradictory",
                supporting_evidence_ids=["evidence_01"],
                contradicting_evidence_ids=["evidence_01"],
                known_evidence_ids={"evidence_01"},
            )

    def test_new_supporting_cannot_conflict_with_existing_contradicting(self) -> None:
        seeded = record_requirement_evidence(
            self.runtime,
            requirement_id="target_01_req_01",
            status="contradictory",
            supporting_evidence_ids=[],
            contradicting_evidence_ids=["evidence_01"],
            known_evidence_ids={"evidence_01"},
        )
        original = seeded.model_dump()

        with self.assertRaisesRegex(ValueError, "supporting.*contradicting"):
            record_requirement_evidence(
                seeded,
                requirement_id="target_01_req_01",
                status="contradictory",
                supporting_evidence_ids=["evidence_01"],
                contradicting_evidence_ids=[],
                known_evidence_ids={"evidence_01"},
            )

        self.assertEqual(seeded.model_dump(), original)

    def test_new_contradicting_cannot_conflict_with_existing_supporting(self) -> None:
        seeded = record_requirement_evidence(
            self.runtime,
            requirement_id="target_01_req_01",
            status="in_progress",
            supporting_evidence_ids=["evidence_01"],
            contradicting_evidence_ids=[],
            known_evidence_ids={"evidence_01"},
        )
        original = seeded.model_dump()

        with self.assertRaisesRegex(ValueError, "contradicting.*supporting"):
            record_requirement_evidence(
                seeded,
                requirement_id="target_01_req_01",
                status="contradictory",
                supporting_evidence_ids=[],
                contradicting_evidence_ids=["evidence_01"],
                known_evidence_ids={"evidence_01"},
            )

        self.assertEqual(seeded.model_dump(), original)

    def test_record_evidence_deep_copies_evidence_lists(self) -> None:
        updated = record_requirement_evidence(
            self.runtime,
            requirement_id="target_01_req_01",
            status="in_progress",
            supporting_evidence_ids=["evidence_01"],
            contradicting_evidence_ids=["evidence_02"],
            known_evidence_ids={"evidence_01", "evidence_02"},
        )

        original_progress = self.runtime.requirement_progress[
            "target_01_req_01"
        ]
        updated_progress = updated.requirement_progress["target_01_req_01"]
        self.assertEqual(original_progress.supporting_evidence_ids, [])
        self.assertEqual(original_progress.contradicting_evidence_ids, [])
        self.assertIsNot(
            original_progress.supporting_evidence_ids,
            updated_progress.supporting_evidence_ids,
        )
        self.assertIsNot(
            original_progress.contradicting_evidence_ids,
            updated_progress.contradicting_evidence_ids,
        )

    def test_unknown_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "不存在的 evidence_id"):
            record_requirement_evidence(
                self.runtime,
                requirement_id="target_01_req_01",
                status="in_progress",
                supporting_evidence_ids=["evidence_missing"],
                contradicting_evidence_ids=[],
                known_evidence_ids=set(),
            )

    def test_request_stop_sets_consistent_fields(self) -> None:
        updated = request_stop(self.runtime, "time_exhausted")

        self.assertTrue(updated.stop_requested)
        self.assertEqual(updated.stop_reason, "time_exhausted")

    def test_blank_stop_reason_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "stop reason 不能为空"):
            request_stop(self.runtime, "  ")


if __name__ == "__main__":
    unittest.main()
