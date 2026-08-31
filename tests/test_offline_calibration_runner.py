import unittest
from unittest.mock import patch

from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.services import scoring_blueprint_service


class OfflineCalibrationRunnerTest(unittest.TestCase):
    def test_replays_c01_c03_c06_through_current_scoring_pipeline(self) -> None:
        from profile_agent.calibration.offline_runner import (
            run_offline_calibration_case,
        )

        with patch.object(
            scoring_blueprint_service.llm,
            "structured",
            side_effect=AssertionError("offline runner must not call LLM"),
        ):
            runs = {
                case_id: run_offline_calibration_case(
                    get_report_calibration_case(case_id)
                )
                for case_id in ("C01", "C03", "C06")
            }

        self.assertTrue(all(run.passed for run in runs.values()))
        self.assertEqual(
            [
                binding.primary_dimension_id
                for binding in runs["C01"].blueprint.bindings
            ],
            [f"role_dim_{index:02d}" for index in range(1, 7)],
        )

        c03_assessment = next(
            item
            for item in runs["C03"].report.score_snapshot.requirement_assessments
            if item.requirement_id == "req_01"
        )
        self.assertIn("ev_C03_002", c03_assessment.limiting_evidence_ids)

        c06_unverified = {
            item.dimension_id
            for item in runs["C06"].report.score_snapshot.radar_dimensions
            if item.level == "UNVERIFIED"
        }
        self.assertEqual(
            c06_unverified,
            {"role_dim_03", "role_dim_04", "role_dim_05", "role_dim_06"},
        )


if __name__ == "__main__":
    unittest.main()
