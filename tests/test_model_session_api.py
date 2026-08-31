from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from profile_agent.model_runtime import current_model_runtime_config
from profile_agent.services.interview_planner_service import finalize_interview_plan
from profile_agent.web.app import create_app
from profile_agent.web.container import WebContainer
from profile_agent.web.repository import SqliteAssessmentRepository
from tests.test_interview_planner_guards import make_timed_draft


class InlineDispatcher:
    def submit(self, function, *args) -> None:
        function(*args)

    def close(self) -> None:
        return None


class ConfigAwareDraftGraph:
    def __init__(self) -> None:
        self.models: list[str | None] = []

    def invoke(self, state):
        config = current_model_runtime_config()
        self.models.append(config.model if config is not None else None)
        draft = make_timed_draft(10)
        draft.targets[0].evidence_requirements[0].planned_role_dimension_id = "role_dim_01"
        return {
            **state,
            "interview_plan": finalize_interview_plan(
                draft,
                state["interview_duration_minutes"],
            ),
        }


class ModelSessionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = SqliteAssessmentRepository(Path(self.temp_dir.name) / "web.db")
        self.graph = ConfigAwareDraftGraph()
        self.container = WebContainer.for_test(
            repository=self.repository,
            pre_interview_graph=self.graph,
            dispatcher=InlineDispatcher(),
        )
        self.client = TestClient(create_app(self.container))

    def tearDown(self) -> None:
        self.client.close()
        self.repository.close()
        self.temp_dir.cleanup()

    def test_model_session_is_probed_then_bound_to_assessment_analysis(self) -> None:
        def fake_structured(_wrapper, _messages, schema):
            return schema(ok=True, message="compatible")

        with patch("profile_agent.web.routers.models.LLM.structured", fake_structured):
            model_response = self.client.post(
                "/api/model-sessions",
                json={
                    "provider": "deepseek",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "secret-key",
                    "model": "deepseek-chat",
                },
            )

        self.assertEqual(model_response.status_code, 200)
        body = model_response.json()
        self.assertEqual(body["provider"], "deepseek")
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertNotIn("api_key", body)

        assessment_response = self.client.post(
            "/api/assessments",
            data={
                "target_role": "AI 应用工程师",
                "jd_text": "负责 Agent Workflow 与可靠性评估",
                "resume_text": "有 LangGraph 项目经验",
                "idempotency_key": "byok_create_1",
                "model_session_id": body["model_session_id"],
            },
        )

        self.assertEqual(assessment_response.status_code, 202)
        self.assertEqual(self.graph.models, ["deepseek-chat"])
        self.assertIsNone(current_model_runtime_config())

    def test_unknown_model_session_is_rejected_before_assessment_creation(self) -> None:
        response = self.client.post(
            "/api/assessments",
            data={
                "target_role": "AI 应用工程师",
                "jd_text": "负责 Agent Workflow 与可靠性评估",
                "resume_text": "有 LangGraph 项目经验",
                "idempotency_key": "byok_missing",
                "model_session_id": "ms_missing",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.graph.models, [])


if __name__ == "__main__":
    unittest.main()
