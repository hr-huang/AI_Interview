from datetime import datetime, timezone
import unittest

from profile_agent.schemas.runtime_schema import InterviewTurn
from profile_agent.schemas.scenario_rag_schema import QuestionProvenance
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


if __name__ == "__main__":
    unittest.main()
