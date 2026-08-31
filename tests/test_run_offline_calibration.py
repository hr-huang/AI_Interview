import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from profile_agent.services import scoring_blueprint_service


class RunOfflineCalibrationCliTest(unittest.TestCase):
    def test_all_runs_three_cases_without_provider_configuration(self) -> None:
        from run_offline_calibration import main

        output = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {
                    "QWEN_API_KEY": "",
                    "MIMO_API_KEY": "",
                    "GLM_API_KEY": "",
                },
                clear=False,
            ),
            patch.object(
                scoring_blueprint_service.llm,
                "structured",
                side_effect=AssertionError("offline CLI must not call LLM"),
            ),
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            code = main(["--case", "ALL"])

        self.assertEqual(code, 0)
        rendered = output.getvalue()
        for case_id in ("C01", "C03", "C06"):
            self.assertIn(f"{case_id}: PASS", rendered)

    def test_single_case_and_invalid_case_have_stable_exit_codes(self) -> None:
        from run_offline_calibration import main

        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(main(["--case", "C03"]), 0)
            self.assertEqual(main(["--case", "C02"]), 2)

        self.assertIn("C03: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
