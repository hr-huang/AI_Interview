from __future__ import annotations

import io
import os
from datetime import datetime, timezone
from pathlib import Path
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from openai import OpenAIError

from profile_agent.llm import LLMProviderError


class FakeAssertion:
    def __init__(self, code: str, passed: bool, message: str = "") -> None:
        self.code = code
        self.passed = passed
        self.message = message


class FakeRun:
    def __init__(self, case_id: str, run_number: int, passed: bool = True) -> None:
        self.case_id = case_id
        self.run_number = run_number
        self.assertions = [
            FakeAssertion(
                code="boundary",
                passed=passed,
                message="ok" if passed else "boundary failed",
            )
        ]

    @property
    def passed(self) -> bool:
        return all(assertion.passed for assertion in self.assertions)


class RunReportCalibrationCliTest(unittest.TestCase):
    def test_selects_one_case_passes_requested_runs_and_writes_artifact(self) -> None:
        from run_report_calibration import main

        calls: list[tuple[str, int]] = []
        artifact_calls: list[tuple[Path, str, int]] = []

        def fake_runner(case, *, runs: int):
            calls.append((case.id, runs))
            return [FakeRun(case.id, run_number) for run_number in range(1, runs + 1)]

        def fake_writer(root: Path, case, runs) -> Path:
            artifact_calls.append((root, case.id, len(runs)))
            return root / case.id

        with patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}, clear=False):
            code = main(
                ["--case", "C04", "--runs", "3", "--artifact-root", "tmp-artifacts"],
                runner=fake_runner,
                artifact_writer=fake_writer,
                now_provider=lambda: datetime(
                    2026,
                    8,
                    22,
                    9,
                    30,
                    45,
                    tzinfo=timezone.utc,
                ),
            )

        self.assertEqual(code, 0)
        self.assertEqual(calls, [("C04", 3)])
        self.assertEqual(
            artifact_calls,
            [(Path("tmp-artifacts/20260822T093045Z"), "C04", 3)],
        )

    def test_all_selects_all_six_cases_without_network(self) -> None:
        from run_report_calibration import main

        calls: list[tuple[str, int]] = []

        def fake_runner(case, *, runs: int):
            calls.append((case.id, runs))
            return [FakeRun(case.id, run_number) for run_number in range(1, runs + 1)]

        with (
            patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}, clear=False),
            patch(
                "run_report_calibration.write_report_calibration_artifacts",
                return_value=Path("tmp-artifacts"),
            ),
            patch(
                "profile_agent.llm.LLM._build_model",
                side_effect=AssertionError("network access is forbidden in CLI unit tests"),
            ),
        ):
            code = main(["--case", "ALL", "--runs", "2"], runner=fake_runner)

        self.assertEqual(code, 0)
        self.assertEqual(
            calls,
            [(case_id, 2) for case_id in ("C01", "C02", "C03", "C04", "C05", "C06")],
        )

    def test_rejects_non_positive_runs_without_calling_runner(self) -> None:
        from run_report_calibration import main

        calls: list[str] = []

        def fake_runner(case, *, runs: int):
            calls.append(case.id)
            return [FakeRun(case.id, 1)]

        for value in ("0", "-1"):
            with self.subTest(runs=value):
                with patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}, clear=False):
                    code = main(["--case", "C04", "--runs", value], runner=fake_runner)
                self.assertEqual(code, 2)

        self.assertEqual(calls, [])

    def test_invalid_case_is_argument_error_without_calling_runner(self) -> None:
        from run_report_calibration import main

        runner = Mock()
        with patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}, clear=False):
            code = main(["--case", "C99"], runner=runner)

        self.assertEqual(code, 2)
        runner.assert_not_called()

    def test_failed_assertion_returns_one_and_does_not_call_network(self) -> None:
        from run_report_calibration import main

        def fake_runner(case, *, runs: int):
            return [FakeRun(case.id, 1, passed=False)]

        output = io.StringIO()
        with (
            patch.dict(os.environ, {"QWEN_API_KEY": "secret-key"}, clear=False),
            patch(
                "run_report_calibration.write_report_calibration_artifacts",
                return_value=Path("tmp-artifacts"),
            ),
            patch(
                "profile_agent.llm.LLM._build_model",
                side_effect=AssertionError("network access is forbidden in CLI unit tests"),
            ),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            code = main(["--case", "C04"], runner=fake_runner)

        self.assertEqual(code, 1)
        self.assertIn("C04", output.getvalue())
        self.assertIn("FAIL", output.getvalue())
        self.assertNotIn("secret-key", output.getvalue())

    def test_missing_provider_configuration_returns_two_without_network(self) -> None:
        from run_report_calibration import main

        runner = Mock()
        output = io.StringIO()
        with (
            patch.dict(os.environ, {"QWEN_API_KEY": ""}, clear=False),
            patch(
                "profile_agent.llm.LLM._build_model",
                side_effect=AssertionError("network access is forbidden in CLI unit tests"),
            ),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            code = main(["--case", "C04"], runner=runner)

        self.assertEqual(code, 2)
        runner.assert_not_called()
        self.assertIn("QWEN_API_KEY", output.getvalue())

    def test_provider_errors_return_one_without_leaking_api_key(self) -> None:
        from run_report_calibration import main

        secret = "secret-provider-key"
        errors = (
            LLMProviderError(f"provider failed: {secret}"),
            OpenAIError(f"provider failed: {secret}"),
        )

        for error in errors:
            with self.subTest(error=type(error).__name__):
                def fake_runner(case, *, runs: int, error=error):
                    raise error

                output = io.StringIO()
                with (
                    patch.dict(os.environ, {"QWEN_API_KEY": secret}, clear=False),
                    patch(
                        "profile_agent.llm.LLM._build_model",
                        side_effect=AssertionError("network access is forbidden in CLI unit tests"),
                    ),
                    redirect_stdout(output),
                    redirect_stderr(output),
                ):
                    code = main(["--case", "C04"], runner=fake_runner)

                self.assertEqual(code, 1)
                self.assertNotIn(secret, output.getvalue())


if __name__ == "__main__":
    unittest.main()
