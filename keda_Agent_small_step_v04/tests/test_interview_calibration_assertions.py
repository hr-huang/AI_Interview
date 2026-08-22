from __future__ import annotations

import unittest

from profile_agent.calibration.interview_assertions import evaluate_interview_path
from profile_agent.calibration.interview_cases import get_interview_calibration_case
from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.schemas.report_schema import AssessmentReport
from tests.test_report_calibration_assertions import _report_for


def _assertion_map(assertions):
    return {assertion.code: assertion for assertion in assertions}


def _report_with_critical_error(case_id: str) -> AssessmentReport:
    report_case = get_report_calibration_case(case_id)
    report = _report_for(report_case)
    assessments = []
    for assessment in report.score_snapshot.requirement_assessments:
        if assessment.dimension_id == "role_dim_05":
            assessment = assessment.model_copy(
                update={"unresolved_critical_error_ids": ["d05_err_01"]}
            )
        assessments.append(assessment)
    snapshot = report.score_snapshot.model_copy(
        update={"requirement_assessments": assessments}
    )
    return report.model_copy(update={"score_snapshot": snapshot})


def _final_state(case_id: str, *, report: AssessmentReport | None = None):
    report_case = get_report_calibration_case(case_id)
    return {
        "interview_plan": report_case.plan,
        "runtime_state": report_case.runtime_state,
        "interview_turns": report_case.turns,
        "evidences": report_case.evidences,
        "assessment_report": report or _report_for(report_case),
    }


class InterviewCalibrationAssertionsTest(unittest.TestCase):
    def test_c03_valid_path_covers_transfer_and_report_boundaries(self) -> None:
        case = get_interview_calibration_case("C03")
        assertions = evaluate_interview_path(
            case,
            _final_state("C03"),
            ["C03_project", "C03_transfer"],
        )
        by_code = _assertion_map(assertions)

        for code in (
            "terminal_state",
            "question_limit",
            "answered_turns",
            "required_topic:transfer",
            "evidence_provenance",
            "scripted_rule_usage",
            "radar_level:role_dim_01",
        ):
            with self.subTest(code=code):
                self.assertTrue(by_code[code].passed, by_code[code].message)
        self.assertIn("evidence_refs", by_code)

    def test_missing_required_topic_fails_independently(self) -> None:
        case = get_interview_calibration_case("C03")
        final_state = _final_state("C03")
        final_state["interview_plan"] = final_state["interview_plan"].model_copy(
            update={
                "targets": [
                    target.model_copy(
                        update={
                            "evidence_requirements": [
                                requirement.model_copy(
                                    update={"description": "验证一般实现能力"}
                                )
                                for requirement in target.evidence_requirements
                            ]
                        }
                    )
                    for target in final_state["interview_plan"].targets
                ]
            }
        )
        final_state["interview_turns"] = [
            turn.model_copy(update={"question": "请继续说明。"})
            for turn in final_state["interview_turns"]
        ]

        by_code = _assertion_map(
            evaluate_interview_path(
                case,
                final_state,
                ["C03_project", "C03_transfer"],
            )
        )

        self.assertFalse(by_code["required_topic:transfer"].passed)

    def test_question_answer_and_rule_usage_failures_are_separate(self) -> None:
        case = get_interview_calibration_case("C03")
        case = case.model_copy(
            update={
                "path_expectation": case.path_expectation.model_copy(
                    update={"max_questions": 1}
                )
            }
        )
        final_state = _final_state("C03")
        turns = list(final_state["interview_turns"])
        turns[-1] = turns[-1].model_copy(
            update={"answer": None, "answered_at": None}
        )
        final_state["interview_turns"] = turns

        by_code = _assertion_map(
            evaluate_interview_path(
                case,
                final_state,
                ["C03_transfer", "C03_transfer", "C03_transfer"],
            )
        )

        self.assertFalse(by_code["question_limit"].passed)
        self.assertFalse(by_code["answered_turns"].passed)
        self.assertFalse(by_code["scripted_rule_usage"].passed)

    def test_evidence_excerpt_must_trace_to_answer(self) -> None:
        case = get_interview_calibration_case("C03")
        final_state = _final_state("C03")
        evidences = list(final_state["evidences"])
        evidences[0] = evidences[0].model_copy(
            update={"source_excerpt": "回答中不存在的片段"}
        )
        final_state["evidences"] = evidences

        by_code = _assertion_map(
            evaluate_interview_path(
                case,
                final_state,
                ["C03_project", "C03_transfer"],
            )
        )

        self.assertFalse(by_code["evidence_provenance"].passed)

    def test_c05_requires_critical_error_in_reliability_dimension(self) -> None:
        case = get_interview_calibration_case("C05")
        passing = _assertion_map(
            evaluate_interview_path(
                case,
                _final_state("C05", report=_report_with_critical_error("C05")),
                ["C05_unsafe"],
            )
        )
        failing = _assertion_map(
            evaluate_interview_path(
                case,
                _final_state("C05"),
                ["C05_unsafe"],
            )
        )

        self.assertTrue(passing["critical_dimension:role_dim_05"].passed)
        self.assertFalse(failing["critical_dimension:role_dim_05"].passed)

    def test_c06_preserves_unverified_dimensions_and_unpublished_match(self) -> None:
        case = get_interview_calibration_case("C06")
        by_code = _assertion_map(
            evaluate_interview_path(
                case,
                _final_state("C06"),
                ["C06_agent", "C06_business"],
            )
        )

        for dimension_id in case.path_expectation.expected_unverified_dimensions:
            self.assertTrue(by_code[f"unverified_dimension:{dimension_id}"].passed)
        self.assertTrue(by_code["job_match_publication"].passed)


if __name__ == "__main__":
    unittest.main()
