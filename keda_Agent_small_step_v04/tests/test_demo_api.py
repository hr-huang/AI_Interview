import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.calibration.offline_runner import run_offline_calibration_case
from profile_agent.web.app import create_app
from profile_agent.web.container import WebContainer
from profile_agent.web.repository import SqliteAssessmentRepository
from profile_agent.web.schemas import AssessmentRecord, AssessmentStatus


class SavedStateGraph:
    def __init__(self) -> None:
        self.values = {}

    def get_state(self, _config):
        return {"values": self.values}


class TrackingLock:
    def __init__(self) -> None:
        self.enter_count = 0

    def __enter__(self):
        self.enter_count += 1
        return self

    def __exit__(self, *_args) -> bool:
        return False


class DemoApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = SqliteAssessmentRepository(
            Path(self.temp_dir.name) / "web.db"
        )
        self.graph = SavedStateGraph()
        self.lock = TrackingLock()
        self.container = WebContainer.for_test(
            repository=self.repository,
            pre_interview_graph=object(),
            dispatcher=object(),
            interview_graph=self.graph,
            interview_lock=self.lock,
        )
        self.client = TestClient(create_app(self.container))

    def tearDown(self) -> None:
        self.client.close()
        self.repository.close()
        self.temp_dir.cleanup()

    def test_demo_endpoint_is_zero_api(self) -> None:
        def fail(*_args, **_kwargs):
            raise AssertionError("demo must not call an LLM boundary")

        with patch.multiple(
            "profile_agent.llm.LLM",
            invoke=fail,
            structured=fail,
        ), patch(
            "profile_agent.services.rubric_matcher_service.llm.structured",
            fail,
        ), patch(
            "profile_agent.services.report_writer_service.llm.structured",
            fail,
        ), patch(
            "profile_agent.services.scoring_blueprint_service.llm.structured",
            fail,
        ):
            response = self.client.get("/api/demo/assessment")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["demo"])
        self.assertEqual(payload["target_role"], "AI Agent / AI应用工程师")

    def test_saved_report_endpoint_builds_view(self) -> None:
        case = get_report_calibration_case("C03")
        run = run_offline_calibration_case(case)
        record = AssessmentRecord.new(
            assessment_id="ast_report",
            target_role=case.target_role,
            jd_text="Agent Workflow",
            resume_text="candidate",
        ).model_copy(
            update={
                "status": AssessmentStatus.COMPLETE,
                "report": run.report.model_dump(mode="json"),
                "final_plan": case.plan.model_dump(mode="json"),
            }
        )
        self.repository.create(record)
        self.graph.values = {
            "interview_plan": case.plan,
            "interview_turns": case.turns,
            "evidences": case.evidences,
        }

        response = self.client.get("/api/assessments/ast_report/report")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["demo"])
        self.assertEqual(
            response.json()["radar_dimensions"][0]["dimension_id"],
            "role_dim_01",
        )

    def test_saved_report_checkpoint_read_uses_interview_lock(self) -> None:
        case = get_report_calibration_case("C03")
        run = run_offline_calibration_case(case)
        record = AssessmentRecord.new(
            assessment_id="ast_locked_report",
            target_role=case.target_role,
            jd_text="Agent Workflow",
            resume_text="candidate",
        ).model_copy(
            update={
                "status": AssessmentStatus.COMPLETE,
                "report": run.report.model_dump(mode="json"),
                "final_plan": case.plan.model_dump(mode="json"),
            }
        )
        self.repository.create(record)
        self.graph.values = {
            "interview_turns": case.turns,
            "evidences": case.evidences,
        }

        before = self.lock.enter_count
        response = self.client.get("/api/assessments/ast_locked_report/report")

        self.assertEqual(response.status_code, 200)
        self.assertGreater(self.lock.enter_count, before)

    def test_saved_report_requires_complete_status(self) -> None:
        case = get_report_calibration_case("C03")
        run = run_offline_calibration_case(case)
        record = AssessmentRecord.new(
            assessment_id="ast_reporting_report",
            target_role=case.target_role,
            jd_text="Agent Workflow",
            resume_text="candidate",
        ).model_copy(
            update={
                "status": AssessmentStatus.REPORTING,
                "report": run.report.model_dump(mode="json"),
                "final_plan": case.plan.model_dump(mode="json"),
            }
        )
        self.repository.create(record)
        self.graph.values = {
            "interview_turns": case.turns,
            "evidences": case.evidences,
        }

        response = self.client.get("/api/assessments/ast_reporting_report/report")

        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
