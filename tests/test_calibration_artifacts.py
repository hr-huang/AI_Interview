from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from profile_agent.calibration.artifacts import (
    write_report_calibration_artifacts,
)
from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.calibration.report_runner import run_report_calibration_case
from tests.test_report_calibration_runner import FakeSemanticServices


class CalibrationArtifactsTest(unittest.TestCase):
    def test_writes_deterministic_safe_artifacts_for_each_run(self) -> None:
        case = get_report_calibration_case("C04")
        runs = run_report_calibration_case(
            case,
            runs=2,
            semantic_services=FakeSemanticServices(case),
        )
        sentinel = "sentinel-calibration-api-key"

        with (
            tempfile.TemporaryDirectory() as first_root,
            tempfile.TemporaryDirectory() as second_root,
            patch.dict(os.environ, {"MIMO_API_KEY": sentinel}, clear=False),
        ):
            first_dir = write_report_calibration_artifacts(
                Path(first_root), case, runs
            )
            second_dir = write_report_calibration_artifacts(
                Path(second_root), case, runs
            )
            self._assert_artifacts(
                Path(first_root),
                Path(second_root),
                first_dir,
                second_dir,
                case,
                runs,
                sentinel,
            )

    def _assert_artifacts(
        self,
        first_root: Path,
        second_root: Path,
        first_dir: Path,
        second_dir: Path,
        case,
        runs,
        sentinel: str,
    ) -> None:
        self.assertEqual(first_dir, first_root / case.id)
        self.assertEqual(second_dir, second_root / case.id)

        expected_summary = {
            "case_id": case.id,
            "title": case.title,
            "runs": [
                {
                    "run_number": run.run_number,
                    "passed": run.passed,
                    "assertions": [
                        {"code": assertion.code, "passed": assertion.passed}
                        for assertion in run.assertions
                    ],
                }
                for run in runs
            ],
        }

        first_summary_json = first_dir / "summary.json"
        first_summary_md = first_dir / "summary.md"
        second_summary_md = second_dir / "summary.md"
        self.assertTrue(first_summary_json.is_file())
        self.assertTrue(first_summary_md.is_file())
        self.assertEqual(
            json.loads(first_summary_json.read_text(encoding="utf-8")),
            expected_summary,
        )
        self.assertEqual(
            first_summary_json.read_text(encoding="utf-8"),
            json.dumps(
                expected_summary,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        self.assertEqual(
            first_summary_md.read_text(encoding="utf-8"),
            second_summary_md.read_text(encoding="utf-8"),
        )
        summary_text = first_summary_md.read_text(encoding="utf-8")
        self.assertIn(case.id, summary_text)
        for run in runs:
            self.assertIn(f"run-{run.run_number:02d}", summary_text)
            for assertion in run.assertions:
                self.assertIn(assertion.code, summary_text)
                self.assertIn("PASS" if assertion.passed else "FAIL", summary_text)

        serialized_paths = [first_summary_json, first_summary_md]
        for run in runs:
            run_dir = first_dir / f"run-{run.run_number:02d}"
            expected_files = {
                "input.json": case.model_dump(mode="json"),
                "blueprint.json": run.blueprint.model_dump(mode="json"),
                "rubric_matches.json": run.rubric_matches.model_dump(mode="json"),
                "report.json": run.report.model_dump(mode="json"),
                "assertions.json": [
                    assertion.model_dump(mode="json")
                    for assertion in run.assertions
                ],
            }
            for filename, expected_payload in expected_files.items():
                path = run_dir / filename
                self.assertTrue(path.is_file(), path)
                serialized_paths.append(path)
                self.assertEqual(
                    json.loads(path.read_text(encoding="utf-8")),
                    expected_payload,
                )
                self.assertEqual(
                    path.read_text(encoding="utf-8"),
                    json.dumps(
                        expected_payload,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                )

        for path in serialized_paths:
            self.assertNotIn(sentinel, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
