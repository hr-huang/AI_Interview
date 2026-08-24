import unittest
from unittest.mock import patch

from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.calibration.report_runner import run_report_calibration_case
from profile_agent.services import scoring_blueprint_service


class OfflineCalibrationServicesTest(unittest.TestCase):
    def test_services_leave_blueprint_to_production_deterministic_builder(self) -> None:
        from profile_agent.calibration.offline_services import (
            build_offline_semantic_services,
        )

        services = build_offline_semantic_services(
            get_report_calibration_case("C01")
        )

        self.assertNotIn("blueprint_builder", services)
        self.assertIn("rubric_matcher", services)
        self.assertIn("narrative_writer", services)

    def test_c01_c03_c06_run_without_any_blueprint_llm_call(self) -> None:
        from profile_agent.calibration.offline_services import (
            build_offline_semantic_services,
        )

        with patch.object(
            scoring_blueprint_service.llm,
            "structured",
            side_effect=AssertionError("offline replay must not call LLM"),
        ):
            for case_id in ("C01", "C03", "C06"):
                with self.subTest(case=case_id):
                    case = get_report_calibration_case(case_id)
                    run = run_report_calibration_case(
                        case,
                        semantic_services=build_offline_semantic_services(case),
                    )[0]
                    self.assertTrue(
                        run.passed,
                        msg="; ".join(
                            item.message
                            for item in run.assertions
                            if not item.passed
                        ),
                    )

    def test_c03_transfer_probe_becomes_limiting_evidence(self) -> None:
        from profile_agent.calibration.offline_services import (
            build_offline_semantic_services,
        )

        case = get_report_calibration_case("C03")
        run = run_report_calibration_case(
            case,
            semantic_services=build_offline_semantic_services(case),
        )[0]
        assessment = next(
            item
            for item in run.report.score_snapshot.requirement_assessments
            if item.requirement_id == "req_01"
        )

        self.assertIn("ev_C03_002", assessment.limiting_evidence_ids)
        self.assertNotIn("ev_C03_002", assessment.transfer_evidence_ids)


if __name__ == "__main__":
    unittest.main()
