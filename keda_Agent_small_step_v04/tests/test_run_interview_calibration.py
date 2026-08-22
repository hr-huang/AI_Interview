from __future__ import annotations

from datetime import datetime, timezone
import io
import os
from pathlib import Path
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from profile_agent.calibration.schemas import (
    CalibrationAssertion,
    InterviewCalibrationRun,
)


def _run(case_id: str, run_number: int, *, passed: bool = True):
    return InterviewCalibrationRun(
        case_id=case_id,
        run_number=run_number,
        initial_state={},
        final_state={},
        selected_rule_ids=[],
        assertions=[
            CalibrationAssertion(
                code="path",
                passed=passed,
                message="ok" if passed else "failed",
            )
        ],
    )


class RunInterviewCalibrationCliTest(unittest.TestCase):
    NOW = datetime(2026, 8, 22, 10, 15, 30, tzinfo=timezone.utc)

    def test_selects_case_runs_each_number_and_writes_timestamped_artifacts(self) -> None:
        from run_interview_calibration import main

        runner_calls: list[tuple[str, int]] = []
        writer_calls: list[tuple[Path, str, int]] = []

        def runner(case, *, run_number: int):
            runner_calls.append((case.id, run_number))
            return _run(case.id, run_number)

        def writer(root: Path, case, runs):
            writer_calls.append((root, case.id, len(runs)))
            return root / case.id

        with patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}, clear=False):
            code = main(
                ["--case", "C03", "--runs", "2", "--artifact-root", "tmp-artifacts"],
                runner=runner,
                artifact_writer=writer,
                now_provider=lambda: self.NOW,
            )

        self.assertEqual(code, 0)
        self.assertEqual(runner_calls, [("C03", 1), ("C03", 2)])
        self.assertEqual(
            writer_calls,
            [(Path("tmp-artifacts/20260822T101530Z"), "C03", 2)],
        )

    def test_all_runs_six_cases_without_network(self) -> None:
        from run_interview_calibration import main

        calls: list[str] = []

        def runner(case, *, run_number: int):
            calls.append(case.id)
            return _run(case.id, run_number)

        with (
            patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}, clear=False),
            patch("run_interview_calibration.write_interview_calibration_artifacts"),
        ):
            code = main(["--case", "ALL"], runner=runner, now_provider=lambda: self.NOW)

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["C01", "C02", "C03", "C04", "C05", "C06"])

    def test_failed_assertion_returns_one(self) -> None:
        from run_interview_calibration import main

        output = io.StringIO()
        with (
            patch.dict(os.environ, {"QWEN_API_KEY": "secret-key"}, clear=False),
            patch("run_interview_calibration.write_interview_calibration_artifacts"),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            code = main(
                ["--case", "C05"],
                runner=lambda case, *, run_number: _run(
                    case.id, run_number, passed=False
                ),
                now_provider=lambda: self.NOW,
            )

        self.assertEqual(code, 1)
        self.assertIn("FAIL", output.getvalue())
        self.assertNotIn("secret-key", output.getvalue())

    def test_missing_key_and_invalid_runs_return_two_without_runner(self) -> None:
        from run_interview_calibration import main

        runner = Mock()
        with patch.dict(os.environ, {"QWEN_API_KEY": ""}, clear=False):
            missing_key_code = main(["--case", "C03"], runner=runner)
        with patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}, clear=False):
            invalid_runs_code = main(["--runs", "0"], runner=runner)

        self.assertEqual(missing_key_code, 2)
        self.assertEqual(invalid_runs_code, 2)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
