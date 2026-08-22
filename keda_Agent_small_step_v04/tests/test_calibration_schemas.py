import unittest

from pydantic import ValidationError

from profile_agent.calibration.schemas import LevelRange, ReportCalibrationExpectation


class CalibrationSchemaTest(unittest.TestCase):
    def test_level_range_accepts_ordered_levels(self) -> None:
        value = LevelRange(min_level="L2", max_level="L3")
        self.assertEqual((value.min_level, value.max_level), ("L2", "L3"))

    def test_level_range_rejects_reversed_levels(self) -> None:
        with self.assertRaises(ValidationError):
            LevelRange(min_level="L3", max_level="L2")

    def test_expectation_rejects_conflicting_required_and_forbidden_hits(self) -> None:
        with self.assertRaises(ValidationError):
            ReportCalibrationExpectation(
                required_rubric_hits={"req_01": ["role_dim_01_min_01"]},
                forbidden_rubric_hits={"req_01": ["role_dim_01_min_01"]},
            )

    def test_expectation_rejects_level_and_unverified_conflict(self) -> None:
        with self.assertRaises(ValidationError):
            ReportCalibrationExpectation(
                requirement_level_ranges={
                    "req_01": LevelRange(min_level="L1", max_level="L1")
                },
                expected_unverified_requirements=["req_01"],
            )


if __name__ == "__main__":
    unittest.main()
