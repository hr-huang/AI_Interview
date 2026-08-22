from __future__ import annotations

import unittest

from profile_agent.calibration.interview_cases import (
    get_interview_calibration_case,
    load_interview_calibration_cases,
)


class InterviewCalibrationCasesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = load_interview_calibration_cases()
        self.by_id = {case.id: case for case in self.cases}

    def test_exactly_six_cases_exist_in_canonical_order(self) -> None:
        self.assertEqual(
            [case.id for case in self.cases],
            ["C01", "C02", "C03", "C04", "C05", "C06"],
        )

    def test_cases_have_common_current_role_jd_and_unique_rules(self) -> None:
        self.assertEqual(len({case.jd_text for case in self.cases}), 1)
        for case in self.cases:
            with self.subTest(case=case.id):
                self.assertTrue(case.resume_text.strip())
                self.assertTrue(case.jd_text.strip())
                self.assertEqual(case.target_role, "AI Agent / AI 应用工程师")
                rule_ids = [rule.id for rule in case.answer_rules]
                self.assertEqual(len(rule_ids), len(set(rule_ids)))
                self.assertLessEqual(case.path_expectation.max_questions, 10)

    def test_c02_answers_remain_keyword_only(self) -> None:
        answers = [rule.answer for rule in self.by_id["C02"].answer_rules]

        self.assertTrue(all(len(answer) <= 40 for answer in answers))
        self.assertTrue(all("具体" not in answer for answer in answers))
        self.assertIn("depth_probe", self.by_id["C02"].path_expectation.required_topics)

    def test_c03_freezes_strong_project_and_weak_transfer(self) -> None:
        case = self.by_id["C03"]
        answers = {rule.id: rule.answer for rule in case.answer_rules}
        terms = {rule.id: rule.match_any for rule in case.answer_rules}

        self.assertIn("状态", answers["C03_project"])
        self.assertIn("order_id", answers["C03_project"])
        self.assertIn("幂等键", answers["C03_project"])
        self.assertIn("低置信度", answers["C03_project"])
        self.assertIn("任务成功率", answers["C03_project"])
        self.assertIn("原样复制", answers["C03_transfer"])
        self.assertNotIn("Agent", terms["C03_project"])
        self.assertIn("工作流", terms["C03_project"])
        self.assertIn("动态路由", terms["C03_project"])
        self.assertIn("业务目标", terms["C03_unverified"])
        self.assertIn("*", terms["C03_unverified"])
        fallback = next(rule for rule in case.answer_rules if "*" in rule.match_any)
        self.assertEqual(fallback.max_uses, case.path_expectation.max_questions)
        for rule_id in ("C03_project", "C03_transfer"):
            rule = next(rule for rule in case.answer_rules if rule.id == rule_id)
            self.assertEqual(rule.max_uses, case.path_expectation.max_questions)
        rule_ids = [rule.id for rule in case.answer_rules]
        self.assertLess(rule_ids.index("C03_transfer"), rule_ids.index("C03_project"))
        self.assertIn("transfer", case.path_expectation.required_topics)
        level_range = case.path_expectation.radar_level_ranges["role_dim_01"]
        self.assertEqual((level_range.min_level, level_range.max_level), ("L2", "L3"))

    def test_c05_contains_exact_unsafe_boundary_and_critical_expectation(self) -> None:
        case = self.by_id["C05"]
        unsafe_answers = [
            rule.answer for rule in case.answer_rules if rule.id == "C05_unsafe"
        ]

        self.assertEqual(len(unsafe_answers), 1)
        self.assertIn("没有授权、审批或人工确认", unsafe_answers[0])
        self.assertIn("safety", case.path_expectation.required_topics)
        self.assertEqual(
            case.path_expectation.required_critical_dimensions,
            ["role_dim_05"],
        )

    def test_c06_preserves_four_unverified_dimensions(self) -> None:
        case = self.by_id["C06"]
        low_information_answers = [
            rule.answer for rule in case.answer_rules if "unverified" in rule.id
        ]

        self.assertTrue(low_information_answers)
        self.assertTrue(
            all("没有相关实践" in answer for answer in low_information_answers)
        )
        self.assertEqual(
            case.path_expectation.expected_unverified_dimensions,
            ["role_dim_03", "role_dim_04", "role_dim_05", "role_dim_06"],
        )
        self.assertFalse(case.path_expectation.job_match_published)

    def test_get_case_rejects_unknown_id(self) -> None:
        self.assertIs(get_interview_calibration_case("C04"), self.cases[3])
        with self.assertRaises(KeyError):
            get_interview_calibration_case("C99")


if __name__ == "__main__":
    unittest.main()
