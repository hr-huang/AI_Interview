import tempfile
import unittest
from pathlib import Path

from profile_agent.web.assessment_service import AssessmentService
from profile_agent.web.container import WebContainer
from profile_agent.web.repository import SqliteAssessmentRepository
from profile_agent.web.schemas import AssessmentRecord, AssessmentStatus


class ExplodingDraftGraph:
    def invoke(self, _state):
        raise RuntimeError("provider transport leaked-secret-detail")


class InlineDispatcher:
    def submit(self, function, *args) -> None:
        function(*args)

    def close(self) -> None:
        return None


class AssessmentRuntimeFailureBoundaryTest(unittest.TestCase):
    def test_unexpected_runtime_error_transitions_analysis_to_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SqliteAssessmentRepository(Path(temp_dir) / "web.db")
            try:
                container = WebContainer.for_test(
                    repository=repository,
                    pre_interview_graph=ExplodingDraftGraph(),
                    dispatcher=InlineDispatcher(),
                )
                record = AssessmentRecord.new(
                    assessment_id="ast_runtime_failure",
                    target_role="AI 应用工程师",
                    jd_text="负责 Agent Workflow 与可靠性评估",
                    resume_text="有 LangGraph 项目经验",
                )
                repository.create(record)

                result = AssessmentService(container).analyze(record.id)
                persisted = repository.get(record.id)

                self.assertEqual(result.status, AssessmentStatus.FAILED)
                self.assertEqual(persisted.status, AssessmentStatus.FAILED)
                self.assertEqual(persisted.failed_stage, "ANALYZING")
                self.assertTrue(persisted.retryable)
                self.assertEqual(
                    persisted.error_message,
                    "面试计划分析失败，请稍后重试。",
                )
                self.assertNotIn(
                    "leaked-secret-detail",
                    persisted.model_dump_json(),
                )
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
