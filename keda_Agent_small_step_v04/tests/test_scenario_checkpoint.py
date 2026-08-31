from datetime import datetime, timezone
import unittest

from profile_agent.schemas.runtime_schema import (
    InterviewRuntimeState,
    InterviewTurn,
    RequirementProgress,
)
from profile_agent.schemas.scenario_rag_schema import LockedScenarioContext, QuestionProvenance
from profile_agent.state.checkpoint_serialization import InterviewCheckpointSerializer


class ScenarioCheckpointTests(unittest.TestCase):
    def test_private_scenario_provenance_survives_checkpoint_round_trip(self) -> None:
        provenance = QuestionProvenance(
            target_requirement_id="req_01",
            primary_dimension_id="role_dim_03",
            retrieval_unit_id="knowledge::rag",
            scenario_id="knowledge",
            module_id="rag",
            revealed_constraint_ids=["delete_memory"],
            retrieval_status="hit",
        )
        turn = InterviewTurn(
            id="turn_001",
            sequence_number=1,
            target_id="target_01",
            primary_requirement_id="req_01",
            question_mode="scenario",
            question="请设计方案",
            asked_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            question_provenance=provenance,
        )
        self.assertNotIn("question_provenance", turn.model_dump())
        serializer = InterviewCheckpointSerializer()
        decoded = serializer.loads_typed(serializer.dumps_typed([turn]))
        self.assertEqual(decoded[0].question_provenance, provenance)

    def test_requirement_progress_gap_tags_survive_checkpoint_round_trip(self) -> None:
        runtime_state = InterviewRuntimeState(
            started_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            requirement_progress={
                "req_01": RequirementProgress(
                    requirement_id="req_01",
                    status="in_progress",
                    latest_gap_tags=["版本", "引用"],
                )
            },
        )
        serializer = InterviewCheckpointSerializer()

        decoded = serializer.loads_typed(serializer.dumps_typed(runtime_state))

        self.assertEqual(
            decoded.requirement_progress["req_01"].latest_gap_tags,
            ["版本", "引用"],
        )

    def test_legacy_requirement_progress_defaults_gap_tags_to_empty(self) -> None:
        legacy_payload = {
            "question_count": 1,
            "started_at": datetime(2026, 8, 29, tzinfo=timezone.utc),
            "requirement_progress": {
                "req_01": {
                    "requirement_id": "req_01",
                    "status": "in_progress",
                    "attempt_count": 1,
                    "supporting_evidence_ids": [],
                    "contradicting_evidence_ids": [],
                }
            },
        }

        decoded = InterviewRuntimeState.model_validate(legacy_payload)

        self.assertEqual(
            decoded.requirement_progress["req_01"].latest_gap_tags,
            [],
        )

    def test_legacy_context_payload_discards_canonical_objects(self) -> None:
        legacy_payload = {
            "scenario_id": "knowledge",
            "module_id": "rag",
            "retrieval_unit_id": "knowledge::rag",
            "business_goal": "支持知识问答",
            "opening_goal": "验证检索设计",
            "retrieval_status": "hit",
            "provenance": {
                "target_requirement_id": "req_01",
                "primary_dimension_id": "role_dim_03",
                "retrieval_unit_id": "knowledge::rag",
                "scenario_id": "knowledge",
                "module_id": "rag",
                "retrieval_status": "hit",
            },
            "scenario": {"base_constraints": ["PRIVATE_BASE_CONSTRAINT"]},
            "module": {
                "evidence_signals": ["PRIVATE_EVIDENCE_SIGNAL"],
                "critical_errors": ["PRIVATE_CRITICAL_ERROR"],
            },
        }

        context = LockedScenarioContext.model_validate(legacy_payload)

        self.assertEqual(context.primary_dimension_id, "role_dim_03")
        self.assertNotIn("scenario", context.model_dump())
        self.assertNotIn("module", context.model_dump())
        self.assertNotIn("PRIVATE_BASE_CONSTRAINT", repr(context.model_dump()))
        self.assertNotIn("PRIVATE_EVIDENCE_SIGNAL", repr(context.model_dump()))


if __name__ == "__main__":
    unittest.main()
