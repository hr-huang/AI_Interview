import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from profile_agent.calibration.report_cases import (
    build_public_student_showcase_case,
    get_report_calibration_case,
)
from profile_agent.calibration.offline_runner import run_offline_calibration_case
from profile_agent.schemas.report_schema import (
    AssessmentReport,
    JobMatchResult,
    RadarDimensionResult,
    ReportNarrativeDraft,
    ScoreSnapshot,
)
from profile_agent.web.app import create_app
from profile_agent.web.container import WebContainer
from profile_agent.web.repository import SqliteAssessmentRepository
from profile_agent.web.schemas import AssessmentRecord, AssessmentStatus
from tests.report_test_helpers import (
    make_test_candidate_overview,
    make_test_enterprise_assessment,
)


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
            for endpoint, case_id, expected_count in (
                ("/api/demo/assessment", "DEMO_STUDENT", 5),
                ("/api/demo/assessment/boundary", "C03", 2),
            ):
                with self.subTest(endpoint=endpoint):
                    response = self.client.get(endpoint)

                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    self.assertTrue(payload["demo"])
                    self.assertEqual(
                        payload["target_role"],
                        "AI Agent应用工程师（校招/初级）",
                    )
                    self.assertEqual(
                        len(payload["interview_transcript"]), expected_count
                    )
                    case = (
                        build_public_student_showcase_case()
                        if case_id == "DEMO_STUDENT"
                        else get_report_calibration_case(case_id)
                    )
                    self.assertEqual(
                        [item["turn_id"] for item in payload["interview_transcript"]],
                        [turn.id for turn in case.turns],
                    )
                    self.assertEqual(
                        [item["question"] for item in payload["interview_transcript"]],
                        [turn.question for turn in case.turns],
                    )
                    self.assertEqual(
                        [item["answer"] for item in payload["interview_transcript"]],
                        [turn.answer for turn in case.turns],
                    )

    def test_public_showcase_is_student_scoped_and_has_varied_scores(self) -> None:
        run = run_offline_calibration_case(build_public_student_showcase_case())
        failures = [item.message for item in run.assertions if not item.passed]
        self.assertTrue(run.passed, failures)

        response = self.client.get("/api/demo/assessment")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["target_role"],
            "AI Agent应用工程师（校招/初级）",
        )
        self.assertIn("应届", payload["demo_case_description"])
        scores = [
            item["score"]
            for item in payload["radar_dimensions"]
            if item["score"] is not None
        ]
        self.assertGreaterEqual(len(set(scores)), 3)
        self.assertTrue(any(item["level"] == "UNVERIFIED" for item in payload["radar_dimensions"]))
        enterprise = payload.get("enterprise_assessment")
        self.assertIsNotNone(enterprise)
        self.assertEqual(
            enterprise["decision"],
            "CONDITIONAL_PROCEED",
        )
        self.assertLessEqual(
            len(enterprise["reinterview_plan"]),
            3,
        )
        self.assertTrue(enterprise["overall_assessment"])
        serialized = json.dumps(payload, ensure_ascii=False)
        for token in ("RubricMatch", "Requirement", "d03_min_02", "ev_DEMO_STUDENT"):
            self.assertNotIn(token, serialized)

    def test_saved_and_demo_reports_share_safe_enterprise_contract(self) -> None:
        case = build_public_student_showcase_case()
        run = run_offline_calibration_case(case)
        record = AssessmentRecord.new(
            assessment_id="ast_demo_student_report",
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

        saved = self.client.get("/api/assessments/ast_demo_student_report/report")
        demo = self.client.get("/api/demo/assessment")

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(demo.status_code, 200)
        saved_payload = saved.json()
        demo_payload = demo.json()
        self.assertEqual(set(saved_payload), set(demo_payload))
        for payload in (saved_payload, demo_payload):
            enterprise = payload.get("enterprise_assessment")
            self.assertIsNotNone(enterprise)
            self.assertEqual(
                enterprise["decision"],
                "CONDITIONAL_PROCEED",
            )
            self.assertLessEqual(
                len(enterprise["reinterview_plan"]),
                3,
            )
            self.assertTrue(enterprise["overall_assessment"])

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

    def test_saved_report_with_turns_and_no_evidence_keeps_transcript_unverified(self) -> None:
        case = get_report_calibration_case("C03")
        profile = self.container.role_profile
        report = AssessmentReport(
            target_role=case.target_role,
            score_snapshot=ScoreSnapshot(
                role_family=profile.role_family,
                role_profile_version=profile.version,
                scoring_engine_version="test-engine",
                radar_dimensions=[
                    RadarDimensionResult(
                        dimension_id=dimension.id,
                        name=dimension.name,
                        score=None,
                        level="UNVERIFIED",
                        coverage=0,
                        confidence="low",
                    )
                    for dimension in profile.dimensions
                ],
                job_match=JobMatchResult(
                    published=False,
                    coverage=0,
                    confidence="low",
                ),
            ),
            narrative=ReportNarrativeDraft(executive_summary="无证据的完整转录。"),
            candidate_overview=make_test_candidate_overview(
                case.target_role,
                candidate_id="ast_transcript_without_evidence",
                interview_rounds=len(case.turns),
            ),
            enterprise_assessment=make_test_enterprise_assessment(),
        )
        record = AssessmentRecord.new(
            assessment_id="ast_transcript_without_evidence",
            target_role=case.target_role,
            jd_text="Agent Workflow",
            resume_text="candidate",
        ).model_copy(
            update={
                "status": AssessmentStatus.COMPLETE,
                "report": report.model_dump(mode="json"),
                "final_plan": case.plan.model_dump(mode="json"),
            }
        )
        self.repository.create(record)
        self.graph.values = {
            "interview_turns": case.turns,
            "evidences": [],
        }

        response = self.client.get(
            "/api/assessments/ast_transcript_without_evidence/report"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["interview_transcript"]), len(case.turns))
        self.assertTrue(payload["interview_transcript"])
        self.assertTrue(
            all(
                    turn["evidence_status"] == "none"
                and "evidence_ids" not in turn
                and turn["evidence_cta"] == "查看本轮依据"
                for turn in payload["interview_transcript"]
            )
        )
        self.assertTrue(all(item["reasons"] == [] for item in payload["radar_dimensions"]))

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
