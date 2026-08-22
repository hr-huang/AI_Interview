from datetime import datetime, timezone
import unittest

from tests.report_test_helpers import make_test_report
from profile_agent.schemas.report_schema import AssessmentReport
from profile_agent.schemas.runtime_schema import InterviewRuntimeState
from profile_agent.state.main_state import MainState


class MainStateRuntimeTest(unittest.TestCase):
    def test_main_state_accepts_runtime_state_block(self) -> None:
        runtime = InterviewRuntimeState(
            started_at=datetime.now(timezone.utc)
        )
        state: MainState = {"runtime_state": runtime}

        self.assertIs(state["runtime_state"], runtime)

    def test_runtime_state_is_not_embedded_in_interview_plan(self) -> None:
        annotations = MainState.__annotations__

        self.assertIn("runtime_state", annotations)
        self.assertIn("interview_plan", annotations)
        self.assertNotEqual(
            annotations["runtime_state"],
            annotations["interview_plan"],
        )

    def test_main_state_accepts_optional_assessment_report(self) -> None:
        report = make_test_report()
        state: MainState = {"assessment_report": report}

        self.assertIs(state["assessment_report"], report)
        self.assertEqual(
            MainState.__annotations__["assessment_report"],
            AssessmentReport,
        )


if __name__ == "__main__":
    unittest.main()
