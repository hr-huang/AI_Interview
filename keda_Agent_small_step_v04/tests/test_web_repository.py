import tempfile
import unittest
from pathlib import Path

from profile_agent.web.repository import SqliteAssessmentRepository
from profile_agent.web.schemas import (
    AssessmentRecord,
    AssessmentStatus,
    transition_assessment,
)


class WebRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "web.sqlite3"
        self.repo = SqliteAssessmentRepository(self.db_path)

    def tearDown(self) -> None:
        self.repo.close()
        self.temp_dir.cleanup()

    def test_round_trips_assessment_and_rejects_illegal_transition(self) -> None:
        record = AssessmentRecord.new(
            assessment_id="ast_001",
            target_role="AI 应用工程师",
            jd_text="负责 Agent Workflow 落地",
            resume_text="候选人有 LangGraph 项目",
        )
        self.repo.create(record)
        loaded = self.repo.get("ast_001")
        self.assertEqual(loaded.status, AssessmentStatus.DRAFT)

        analyzing = transition_assessment(loaded, AssessmentStatus.ANALYZING)
        self.repo.save(analyzing)
        self.assertEqual(
            self.repo.get("ast_001").status,
            AssessmentStatus.ANALYZING,
        )
        with self.assertRaisesRegex(ValueError, "非法评估状态转换"):
            transition_assessment(analyzing, AssessmentStatus.COMPLETE)

    def test_save_if_version_rejects_stale_update(self) -> None:
        record = AssessmentRecord.new(
            assessment_id="ast_versioned",
            target_role="AI 应用工程师",
            jd_text="Agent Workflow",
            resume_text="LangGraph 项目",
        )
        self.repo.create(record)
        analyzing = transition_assessment(record, AssessmentStatus.ANALYZING)

        self.assertTrue(self.repo.save_if_version(analyzing, record.version))
        stale_replay = analyzing.model_copy(
            update={"error_message": "stale update"}
        )
        self.assertFalse(
            self.repo.save_if_version(stale_replay, record.version)
        )
        current = self.repo.get(record.id)
        self.assertEqual(current.status, AssessmentStatus.ANALYZING)
        self.assertEqual(current.version, analyzing.version)

    def test_candidate_token_hash_is_indexed_and_rotatable(self) -> None:
        record = AssessmentRecord.new(
            assessment_id="ast_token",
            target_role="AI 应用工程师",
            jd_text="Agent Workflow",
            resume_text="LangGraph 项目",
        ).model_copy(update={"candidate_token_hash": "hash_v1"})
        self.repo.create(record)
        self.assertEqual(
            self.repo.get_by_candidate_token_hash("hash_v1").id,
            "ast_token",
        )

        self.repo.save(record.model_copy(update={"candidate_token_hash": "hash_v2"}))
        with self.assertRaises(KeyError):
            self.repo.get_by_candidate_token_hash("hash_v1")
        self.assertEqual(
            self.repo.get_by_candidate_token_hash("hash_v2").id,
            "ast_token",
        )

    def test_answer_idempotency_returns_first_response(self) -> None:
        response = {"state": "waiting", "turn_id": "turn_001"}
        self.assertTrue(
            self.repo.save_answer_response("token_hash", "idem_1", response)
        )
        self.assertFalse(
            self.repo.save_answer_response(
                "token_hash", "idem_1", {"state": "different"}
            )
        )
        self.assertEqual(
            self.repo.get_answer_response("token_hash", "idem_1"),
            response,
        )

    def test_missing_records_raise_key_error(self) -> None:
        with self.assertRaises(KeyError):
            self.repo.get("missing")
        with self.assertRaises(KeyError):
            self.repo.get_by_candidate_token_hash("missing")


if __name__ == "__main__":
    unittest.main()
