from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from profile_agent.schemas.runtime_schema import (
    InterviewRuntimeState,
    RequirementAssessment,
    RequirementProgress,
)


class RuntimeSchemaTest(unittest.TestCase):
    def test_requirement_assessment_exposes_missing_evidence_tags(self) -> None:
        assessment = RequirementAssessment(
            requirement_id="req_01",
            recommended_status="in_progress",
            rationale="版本更新和引用核验尚未证明",
            missing_evidence_tags=["版本", "引用"],
        )

        self.assertEqual(
            assessment.missing_evidence_tags,
            ["版本", "引用"],
        )
        self.assertEqual(
            RequirementProgress(requirement_id="req_01").latest_gap_tags,
            [],
        )

    def test_old_checkpoint_without_latest_gap_tags_defaults_to_empty(self) -> None:
        progress = RequirementProgress.model_validate(
            {
                "requirement_id": "req_01",
                "status": "in_progress",
                "attempt_count": 1,
                "supporting_evidence_ids": [],
                "contradicting_evidence_ids": [],
            }
        )

        self.assertEqual(progress.latest_gap_tags, [])

    def test_invalid_requirement_status_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RequirementProgress(
                requirement_id="target_01_req_01",
                status="invalid_status",
            )

    def test_requirement_progress_key_must_match_nested_id(self) -> None:
        with self.assertRaisesRegex(ValidationError, "key.*requirement_id"):
            InterviewRuntimeState(
                started_at=datetime.now(timezone.utc),
                requirement_progress={
                    "target_01_req_01": RequirementProgress(
                        requirement_id="target_01_req_02"
                    )
                },
            )

    def test_requirement_progress_has_independent_mutable_defaults(self) -> None:
        first = RequirementProgress(requirement_id="target_01_req_01")
        second = RequirementProgress(requirement_id="target_01_req_02")

        first.supporting_evidence_ids.append("evidence_01")

        self.assertEqual(first.status, "not_started")
        self.assertEqual(first.attempt_count, 0)
        self.assertEqual(second.supporting_evidence_ids, [])

    def test_stop_request_requires_reason(self) -> None:
        with self.assertRaises(ValidationError):
            InterviewRuntimeState(
                started_at=datetime.now(timezone.utc),
                stop_requested=True,
            )

    def test_reason_is_rejected_when_stop_is_false(self) -> None:
        with self.assertRaises(ValidationError):
            InterviewRuntimeState(
                started_at=datetime.now(timezone.utc),
                stop_reason="time_exhausted",
            )

    def test_runtime_defaults_are_minimal(self) -> None:
        runtime = InterviewRuntimeState(
            started_at=datetime.now(timezone.utc)
        )

        self.assertEqual(runtime.question_count, 0)
        self.assertIsNone(runtime.current_target_id)
        self.assertEqual(runtime.requirement_progress, {})
        self.assertEqual(runtime.visited_target_ids, [])
        self.assertFalse(runtime.stop_requested)
        self.assertIsNone(runtime.stop_reason)


if __name__ == "__main__":
    unittest.main()
