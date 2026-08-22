from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from profile_agent.calibration.artifacts import write_interview_calibration_artifacts
from profile_agent.calibration.interview_cases import get_interview_calibration_case
from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.calibration.schemas import (
    CalibrationAssertion,
    InterviewCalibrationRun,
)
from tests.test_report_calibration_assertions import _report_for


class InterviewCalibrationArtifactsTest(unittest.TestCase):
    def test_writes_safe_machine_readable_path_artifacts(self) -> None:
        case = get_interview_calibration_case("C03")
        report_case = get_report_calibration_case("C03")
        report = _report_for(report_case)
        run = InterviewCalibrationRun(
            case_id="C03",
            run_number=1,
            initial_state={
                "interview_plan": report_case.plan,
                "claim_registry": report_case.claim_registry,
            },
            final_state={
                "interview_plan": report_case.plan,
                "runtime_state": report_case.runtime_state,
                "interview_turns": report_case.turns,
                "evidences": report_case.evidences,
                "assessment_report": report,
            },
            selected_rule_ids=["C03_project", "C03_transfer"],
            assertions=[
                CalibrationAssertion(code="terminal_state", passed=True, message="ok")
            ],
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with patch.dict(os.environ, {"MIMO_API_KEY": "secret-sentinel"}):
                case_dir = write_interview_calibration_artifacts(root, case, [run])

            run_dir = case_dir / "run-01"
            expected_files = {
                "initial_state.json",
                "turns.json",
                "evidences.json",
                "report.json",
                "selected_rules.json",
                "assertions.json",
            }
            self.assertTrue(expected_files.issubset({path.name for path in run_dir.iterdir()}))
            self.assertTrue((case_dir / "summary.json").exists())
            self.assertTrue((case_dir / "summary.md").exists())
            self.assertEqual(
                json.loads((run_dir / "selected_rules.json").read_text(encoding="utf-8")),
                ["C03_project", "C03_transfer"],
            )
            for path in case_dir.rglob("*"):
                if path.is_file():
                    self.assertNotIn("secret-sentinel", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
