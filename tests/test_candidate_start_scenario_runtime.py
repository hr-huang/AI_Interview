import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from profile_agent.graphs.interview import build_interview_graph
from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
    GeneratedQuestion,
    InterviewPlan,
)
from profile_agent.schemas.report_schema import ScoringBlueprint
from profile_agent.services.scenario_bank_service import ScenarioCatalog
from profile_agent.web.app import create_app
from profile_agent.web.container import WebContainer
from profile_agent.web.repository import SqliteAssessmentRepository
from profile_agent.web.schemas import AssessmentRecord, AssessmentStatus


class InlineDispatcher:
    def submit(self, function, *args) -> None:
        function(*args)

    def close(self) -> None:
        return None


class CandidateStartScenarioRuntimeTest(unittest.TestCase):
    """Exercise the production-only ScenarioCatalog path used by candidate start."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.repository = SqliteAssessmentRepository(root / "web.db")
        self.checkpoint_connection = sqlite3.connect(
            root / "checkpoints.db",
            check_same_thread=False,
        )
        self.token = "candidate-scenario-runtime-token"
        self.assessment_id = "ast_scenario_runtime"

    def tearDown(self) -> None:
        self.checkpoint_connection.close()
        self.repository.close()
        self.temp_dir.cleanup()

    @staticmethod
    def _unsupported_hard_filter_plan() -> InterviewPlan:
        # role_dim_01 has reviewed scenario/system-design modules, but none of
        # them accepts experience_verification.  Production Scenario RAG must
        # degrade safely instead of turning candidate start into HTTP 500.
        return InterviewPlan(
            duration_minutes=30,
            max_questions=3,
            closing_buffer_minutes=0,
            targets=[
                AssessmentTarget(
                    id="target_a",
                    objective="核验候选人是否真实参与过 Agent 工作流设计",
                    target_type="experience_verification",
                    competency_ids=[],
                    evidence_requirements=[
                        EvidenceRequirement(
                            id="requirement_a",
                            description="核验候选人对 Agent 工作流编排的真实参与边界",
                            candidate_focus="工作流编排",
                            planned_role_dimension_id="role_dim_01",
                        )
                    ],
                    related_claim_ids=[],
                    priority="high",
                    must_cover=True,
                    time_budget_minutes=10,
                    preferred_modes=["scenario"],
                )
            ],
        )

    def _create_ready_assessment(self, plan: InterviewPlan) -> None:
        blueprint = ScoringBlueprint(
            role_family="ai_application_engineering",
            role_profile_version="2026-H2",
            bindings=[],
        )
        record = AssessmentRecord.new(
            assessment_id=self.assessment_id,
            target_role="AI Agent应用工程师（校招/初级）",
            jd_text="负责 Agent Workflow 与工具编排",
            resume_text="参与过 LangGraph Agent 项目",
            interview_duration_minutes=30,
        ).model_copy(
            update={
                "status": AssessmentStatus.READY,
                "pre_interview_state": {
                    "target_role": "AI Agent应用工程师（校招/初级）",
                    "claim_registry": ClaimRegistry().model_dump(mode="json"),
                },
                "original_plan": plan.model_dump(mode="json"),
                "final_plan": plan.model_dump(mode="json"),
                "scoring_blueprint": blueprint.model_dump(mode="json"),
                "candidate_token_hash": hashlib.sha256(
                    self.token.encode("utf-8")
                ).hexdigest(),
            }
        )
        self.repository.create(record)

    def test_candidate_start_survives_no_compatible_reviewed_scenario_module(self) -> None:
        plan = self._unsupported_hard_filter_plan()
        self._create_ready_assessment(plan)

        def question_generator(**_kwargs) -> GeneratedQuestion:
            return GeneratedQuestion(text="请结合一次真实经历说明你的 Agent 工作流编排职责。")

        graph = build_interview_graph(
            question_generator=question_generator,
            checkpointer=SqliteSaver(self.checkpoint_connection),
            scenario_catalog=ScenarioCatalog.load(),
            scenario_retriever=None,
        )
        container = WebContainer.for_test(
            repository=self.repository,
            pre_interview_graph=object(),
            dispatcher=InlineDispatcher(),
            interview_graph=graph,
            scenario_catalog=ScenarioCatalog.load(),
            checkpoint_connection=self.checkpoint_connection,
        )

        # Keep server exceptions as HTTP responses so this test verifies the
        # same browser-visible contract that failed in the real manual run.
        with TestClient(create_app(container), raise_server_exceptions=False) as client:
            response = client.post(f"/api/interviews/{self.token}/start")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["state"], "waiting_for_answer")
        self.assertEqual(payload["turn"]["id"], "turn_001")
        self.assertIn("工作流编排", payload["turn"]["question"])
        self.assertEqual(
            self.repository.get(self.assessment_id).status,
            AssessmentStatus.IN_PROGRESS,
        )


if __name__ == "__main__":
    unittest.main()
