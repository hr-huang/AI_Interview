from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from profile_agent.schemas.runtime_schema import (
    InterviewRuntimeState,
    RequirementProgress,
)


class RuntimeSchemaTest(unittest.TestCase):
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
