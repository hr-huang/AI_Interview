import unittest

from profile_agent.calibration.report_cases import (
    get_report_calibration_case,
    load_report_calibration_cases,
)
from profile_agent.services.role_profile_service import load_role_profile


class ReportCalibrationCasesTest(unittest.TestCase):
    def test_exactly_six_frozen_cases_exist_in_stable_order(self) -> None:
        cases = load_report_calibration_cases()

        self.assertEqual(
            [case.id for case in cases],
            ["C01", "C02", "C03", "C04", "C05", "C06"],
        )
        self.assertEqual(cases, load_report_calibration_cases())

    def test_all_cases_are_terminal_and_provenance_safe(self) -> None:
        for case in load_report_calibration_cases():
            with self.subTest(case=case.id):
                self.assertTrue(case.runtime_state.stop_requested)
                self.assertTrue(case.runtime_state.stop_reason)

                turn_by_id = {turn.id: turn for turn in case.turns}
                target_ids = {target.id for target in case.plan.targets}
                requirement_ids = {
                    requirement.id
                    for target in case.plan.targets
                    for requirement in target.evidence_requirements
                }

                self.assertTrue(turn_by_id)
                self.assertTrue(
                    all(turn.target_id in target_ids for turn in case.turns)
                )
                self.assertTrue(
                    all(
                        turn.primary_requirement_id in requirement_ids
                        for turn in case.turns
                    )
                )
                self.assertTrue(
                    all(evidence.turn_id in turn_by_id for evidence in case.evidences)
                )
                self.assertTrue(
                    all(
                        set(evidence.requirement_ids) <= requirement_ids
                        for evidence in case.evidences
                    )
                )
                self.assertTrue(
                    all(
                        evidence.source_excerpt
                        in turn_by_id[evidence.turn_id].answer
                        for evidence in case.evidences
                    )
                )

    def test_candidate_answers_are_frozen_nonempty_text(self) -> None:
        for case in load_report_calibration_cases():
            with self.subTest(case=case.id):
                self.assertTrue(case.turns)
                self.assertTrue(
                    all(turn.answer and turn.answer.strip() for turn in case.turns)
                )
                self.assertEqual(
                    case.model_dump(), get_report_calibration_case(case.id).model_dump()
                )

    def test_expectations_use_real_profile_criterion_ids(self) -> None:
        profile = load_role_profile("ai_application_engineering", "2026-H2")
        criterion_ids = {
            criterion.id
            for dimension in profile.dimensions
            for criterion in (
                dimension.minimum_criteria
                + dimension.excellence_signals
                + dimension.critical_errors
                + dimension.accepted_alternatives
            )
        }

        for case in load_report_calibration_cases():
            with self.subTest(case=case.id):
                expectation = case.expectation
                referenced_ids = [
                    criterion_id
                    for criterion_ids_for_requirement in (
                        expectation.required_rubric_hits.values()
                    )
                    for criterion_id in criterion_ids_for_requirement
                ] + [
                    criterion_id
                    for criterion_ids_for_requirement in (
                        expectation.forbidden_rubric_hits.values()
                    )
                    for criterion_id in criterion_ids_for_requirement
                ]
                self.assertTrue(set(referenced_ids) <= criterion_ids)
                self.assertTrue(
                    all(criterion_id.startswith("d0") for criterion_id in referenced_ids)
                )

    def test_frozen_case_expectations_cover_the_boundary_examples(self) -> None:
        cases = {case.id: case for case in load_report_calibration_cases()}

        self.assertEqual(
            set(cases["C01"].expectation.requirement_level_ranges),
            {"req_01", "req_02", "req_03", "req_04", "req_05", "req_06"},
        )
        self.assertEqual(cases["C01"].expectation.job_match_published, True)

        self.assertEqual(cases["C02"].expectation.job_match_published, False)
        self.assertEqual(
            set(cases["C02"].expectation.requirement_level_ranges),
            {"req_01", "req_02", "req_03", "req_04", "req_05"},
        )
        self.assertTrue(
            all(
                level_range.min_level == level_range.max_level == "L1"
                for level_range in cases[
                    "C02"
                ].expectation.requirement_level_ranges.values()
            )
        )
        self.assertEqual(
            cases["C02"].expectation.expected_unverified_requirements,
            [],
        )

        self.assertEqual(
            cases["C03"].expectation.requirement_level_ranges["req_01"].max_level,
            "L3",
        )
        self.assertTrue(cases["C03"].expectation.forbidden_rubric_hits["req_01"])

        self.assertEqual(
            set(cases["C04"].expectation.requirement_level_ranges),
            {"req_01", "req_05"},
        )
        self.assertTrue(cases["C04"].expectation.required_rubric_hits["req_01"])
        c04_answers = "\n".join(turn.answer or "" for turn in cases["C04"].turns)
        self.assertIn("相比", c04_answers)
        self.assertIn("宁可", c04_answers)
        self.assertTrue(
            all(
                "_exc_" not in criterion_id
                for criterion_id in cases["C04"].expectation.required_rubric_hits["req_01"]
            )
        )

        self.assertEqual(
            cases["C05"].expectation.requirement_level_ranges["req_05"].max_level,
            "L1",
        )
        self.assertTrue(cases["C05"].expectation.required_rubric_hits["req_05"])

        self.assertEqual(
            set(cases["C06"].expectation.expected_unverified_dimensions),
            {"role_dim_03", "role_dim_04", "role_dim_05", "role_dim_06"},
        )
        self.assertEqual(cases["C06"].expectation.job_match_published, False)


if __name__ == "__main__":
    unittest.main()
