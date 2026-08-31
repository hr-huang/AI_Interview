import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from fastapi.testclient import TestClient

from profile_agent.llm import LLMProviderError
from profile_agent.services.interview_planner_service import (
    finalize_interview_plan,
)
from profile_agent.web.app import create_app
from profile_agent.web.container import WebContainer
from profile_agent.web.document_ingestion import (
    ExtractedDocument,
    MAX_FILE_BYTES,
)
from profile_agent.web.repository import SqliteAssessmentRepository
from profile_agent.web.schemas import AssessmentStatus
from tests.test_interview_planner_guards import make_timed_draft


class InlineDispatcher:
    def submit(self, function, *args) -> None:
        function(*args)

    def close(self) -> None:
        return None


class CloseSpy:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class RecordingExtractor:
    def __init__(self) -> None:
        self.read_lengths: list[int] = []

    def extract(self, filename: str, content: bytes) -> ExtractedDocument:
        self.read_lengths.append(len(content))
        return ExtractedDocument(
            text="有 LangGraph 状态管理和失败恢复项目经验",
            file_type="txt",
            used_ocr_pages=[],
        )


def make_api_plan(duration: int):
    draft = make_timed_draft(10)
    # The API uses the production 2026-H2 role pack when freezing. Keep the
    # fake graph's planned dimension valid for that pack.
    draft.targets[0].evidence_requirements[0].planned_role_dimension_id = (
        "role_dim_01"
    )
    return finalize_interview_plan(draft, duration)


class FakeDraftGraph:
    def __init__(self) -> None:
        self.invoke_count = 0

    def invoke(self, state):
        self.invoke_count += 1
        return {
            **state,
            "interview_plan": make_api_plan(
                state["interview_duration_minutes"]
            ),
        }


class FailOnceDraftGraph(FakeDraftGraph):
    def invoke(self, state):
        self.invoke_count += 1
        if self.invoke_count == 1:
            raise LLMProviderError("模拟 provider 超时 secret-value")
        return {
            **state,
            "interview_plan": make_api_plan(
                state["interview_duration_minutes"]
            ),
        }


class AssessmentApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = SqliteAssessmentRepository(
            Path(self.temp_dir.name) / "web.db"
        )
        self.graph = FakeDraftGraph()
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

    def _create(self, **changes):
        payload = {
            "target_role": "AI 应用工程师",
            "jd_text": "负责 Agent Workflow 与可靠性评估",
            "resume_text": "有 LangGraph 状态管理和失败恢复项目经验",
            "idempotency_key": "create_1",
        }
        payload.update(changes)
        return self.client.post("/api/assessments", data=payload)

    def test_create_with_text_reaches_plan_review(self) -> None:
        response = self._create()

        self.assertEqual(response.status_code, 202)
        assessment_id = response.json()["assessment_id"]
        status = self.client.get(f"/api/assessments/{assessment_id}")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "PLAN_REVIEW")
        self.assertEqual(self.graph.invoke_count, 1)

    def test_create_is_idempotent_without_repeating_analysis(self) -> None:
        first = self._create()
        duplicate = self._create()

        self.assertEqual(duplicate.status_code, 202)
        self.assertEqual(
            first.json()["assessment_id"],
            duplicate.json()["assessment_id"],
        )
        self.assertEqual(self.graph.invoke_count, 1)

        conflict = self._create(jd_text="另一份 JD")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.graph.invoke_count, 1)

    def test_create_uses_requested_interview_duration(self) -> None:
        response = self._create(
            interview_duration_minutes="60",
            idempotency_key="duration_case",
        )

        self.assertEqual(response.status_code, 202)
        assessment_id = response.json()["assessment_id"]
        plan = self.client.get(f"/api/assessments/{assessment_id}/plan")
        self.assertEqual(plan.status_code, 200)
        self.assertEqual(plan.json()["original_plan"]["duration_minutes"], 60)

    def test_upload_read_is_bounded_to_one_byte_over_file_limit(self) -> None:
        extractor = RecordingExtractor()
        self.container.document_extractor = extractor
        read_sizes: list[int] = []

        async def bounded_read(upload, size: int = -1) -> bytes:
            read_sizes.append(size)
            return b"bounded upload"

        with patch(
            "starlette.datastructures.UploadFile.read",
            new=bounded_read,
        ):
            response = self.client.post(
                "/api/assessments",
                data={
                    "target_role": "AI 应用工程师",
                    "jd_text": "Agent Workflow",
                    "idempotency_key": "bounded_upload",
                },
                files={"resume_file": ("resume.txt", b"ignored")},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(read_sizes, [MAX_FILE_BYTES + 1])
        self.assertEqual(extractor.read_lengths, [len(b"bounded upload")])

    def test_default_app_lifespan_closes_owned_resources_only(self) -> None:
        owned_repository = CloseSpy()
        owned_dispatcher = CloseSpy()
        owned = WebContainer(
            repository=owned_repository,
            pre_interview_graph=object(),
            document_extractor=object(),
            dispatcher=owned_dispatcher,
            role_profile=self.container.role_profile,
        )
        with patch(
            "profile_agent.web.app.WebContainer.default",
            return_value=owned,
        ):
            with TestClient(create_app()):
                pass

        self.assertTrue(owned_repository.closed)
        self.assertTrue(owned_dispatcher.closed)

        injected_repository = CloseSpy()
        injected_dispatcher = CloseSpy()
        injected = WebContainer(
            repository=injected_repository,
            pre_interview_graph=object(),
            document_extractor=object(),
            dispatcher=injected_dispatcher,
            role_profile=self.container.role_profile,
        )
        with TestClient(create_app(injected)):
            pass

        self.assertFalse(injected_repository.closed)
        self.assertFalse(injected_dispatcher.closed)

    def test_idempotency_key_conflicts_when_duration_changes(self) -> None:
        first = self._create(
            interview_duration_minutes="30",
            idempotency_key="duration_conflict",
        )
        self.assertEqual(first.status_code, 202)

        changed = self._create(
            interview_duration_minutes="60",
            idempotency_key="duration_conflict",
        )
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(self.graph.invoke_count, 1)

    def test_requires_exactly_one_resume_source(self) -> None:
        missing = self.client.post(
            "/api/assessments",
            data={
                "target_role": "AI 应用工程师",
                "jd_text": "Agent Workflow",
                "idempotency_key": "missing",
            },
        )
        self.assertEqual(missing.status_code, 422)

        both = self.client.post(
            "/api/assessments",
            data={
                "target_role": "AI 应用工程师",
                "jd_text": "Agent Workflow",
                "resume_text": "文本简历",
                "idempotency_key": "both",
            },
            files={"resume_file": ("resume.txt", b"file resume")},
        )
        self.assertEqual(both.status_code, 422)

    def test_plan_override_and_freeze_return_candidate_url_once(self) -> None:
        assessment_id = self._create().json()["assessment_id"]
        plan = self.client.get(f"/api/assessments/{assessment_id}/plan")
        self.assertEqual(plan.status_code, 200)
        target_id = plan.json()["original_plan"]["targets"][0]["id"]

        override = self.client.put(
            f"/api/assessments/{assessment_id}/plan-overrides",
            json={
                "duration_minutes": 45,
                "target_updates": [
                    {"target_id": target_id, "time_budget_minutes": 15}
                ],
            },
        )
        self.assertEqual(override.status_code, 200)
        self.assertEqual(override.json()["preview_plan"]["duration_minutes"], 45)

        freeze = self.client.post(f"/api/assessments/{assessment_id}/freeze")
        self.assertEqual(freeze.status_code, 200)
        self.assertRegex(freeze.json()["candidate_url"], r"^/interviews/")
        self.assertEqual(
            self.repository.get(assessment_id).status.value,
            "READY",
        )
        self.assertIsNotNone(
            self.repository.get(assessment_id).scoring_blueprint
        )
        repeated = self.client.post(f"/api/assessments/{assessment_id}/freeze")
        self.assertEqual(repeated.status_code, 409)

    def test_plan_includes_role_profile_and_editing_guardrails(self) -> None:
        assessment_id = self._create(idempotency_key="plan_metadata").json()[
            "assessment_id"
        ]
        response = self.client.get(f"/api/assessments/{assessment_id}/plan")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["role_profile"]["role_family"],
            "ai_application_engineering",
        )
        self.assertEqual(
            payload["guardrails"]["allowed_duration_minutes"],
            [30, 45, 60],
        )
        self.assertEqual(
            payload["guardrails"]["editable_target_fields"],
            ["priority", "objective", "time_budget_minutes"],
        )
        self.assertIn("must_cover", payload["guardrails"]["immutable_fields"])

    def test_concurrent_freeze_returns_one_token_and_one_conflict(self) -> None:
        assessment_id = self._create(idempotency_key="freeze_race").json()[
            "assessment_id"
        ]
        barrier = Barrier(2)
        original_save_if_version = self.repository.save_if_version

        def gated_save_if_version(record, expected_version):
            if record.status is AssessmentStatus.READY:
                barrier.wait(timeout=5)
            return original_save_if_version(record, expected_version)

        self.repository.save_if_version = gated_save_if_version
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda _: self.client.post(
                        f"/api/assessments/{assessment_id}/freeze"
                    ),
                    (1, 2),
                )
            )

        self.assertEqual(
            sorted(response.status_code for response in responses),
            [200, 409],
        )
        self.assertEqual(
            sum("candidate_url" in response.json() for response in responses),
            1,
        )

    def test_failed_analysis_can_retry_without_leaking_provider_detail(self) -> None:
        self.container.pre_interview_graph = FailOnceDraftGraph()
        response = self._create(idempotency_key="retry_case")
        assessment_id = response.json()["assessment_id"]

        failed = self.client.get(f"/api/assessments/{assessment_id}")
        self.assertEqual(failed.json()["status"], "FAILED")
        self.assertNotIn("secret-value", failed.text)
        self.assertTrue(failed.json()["retryable"])

        retried = self.client.post(f"/api/assessments/{assessment_id}/retry")
        self.assertEqual(retried.status_code, 202)
        current = self.client.get(f"/api/assessments/{assessment_id}")
        self.assertEqual(current.json()["status"], "PLAN_REVIEW")

    def test_missing_assessment_is_404(self) -> None:
        self.assertEqual(
            self.client.get("/api/assessments/missing").status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
