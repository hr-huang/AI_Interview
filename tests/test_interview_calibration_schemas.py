from __future__ import annotations

import unittest

from pydantic import ValidationError

from profile_agent.calibration.schemas import (
    CalibrationAssertion,
    InterviewCalibrationCase,
    InterviewCalibrationRun,
    InterviewPathExpectation,
    LevelRange,
    ScriptedAnswerRule,
)


def _rule(rule_id: str = "C03_transfer") -> ScriptedAnswerRule:
    return ScriptedAnswerRule(
        id=rule_id,
        match_any=["迁移", "新场景", "适配"],
        answer="我会直接复制旧流程，不重新验证。",
        max_uses=1,
    )


def _expectation() -> InterviewPathExpectation:
    return InterviewPathExpectation(
        required_topics={"transfer": ["迁移", "新场景"]},
        radar_level_ranges={
            "role_dim_01": LevelRange(min_level="L2", max_level="L3")
        },
        max_questions=8,
    )


class InterviewCalibrationSchemaTest(unittest.TestCase):
    def test_scripted_answer_rule_accepts_semantic_terms(self) -> None:
        rule = _rule()

        self.assertEqual(rule.id, "C03_transfer")
        self.assertEqual(rule.max_uses, 1)

    def test_scripted_answer_rule_rejects_empty_terms_answer_and_use_limit(self) -> None:
        invalid_values = (
            {"match_any": []},
            {"match_any": ["  "]},
            {"answer": "  "},
            {"max_uses": 0},
        )
        base = _rule().model_dump()
        for replacement in invalid_values:
            with self.subTest(replacement=replacement):
                with self.assertRaises(ValidationError):
                    ScriptedAnswerRule(**(base | replacement))

    def test_path_expectation_rejects_required_and_forbidden_topic_overlap(self) -> None:
        with self.assertRaises(ValidationError):
            InterviewPathExpectation(
                required_topics={"transfer": ["迁移"]},
                forbidden_repeated_topics=["transfer"],
                max_questions=8,
            )

    def test_path_expectation_rejects_non_positive_question_limit(self) -> None:
        with self.assertRaises(ValidationError):
            InterviewPathExpectation(max_questions=0)

    def test_case_rejects_duplicate_rule_ids_and_blank_inputs(self) -> None:
        base = {
            "id": "C03",
            "title": "项目强但迁移弱",
            "resume_text": "候选人有一个 Agent 项目。",
            "jd_text": "岗位要求 Agent 编排和迁移能力。",
            "target_role": "AI Agent / AI 应用工程师",
            "answer_rules": [_rule("duplicate"), _rule("duplicate")],
            "path_expectation": _expectation(),
        }
        with self.assertRaises(ValidationError):
            InterviewCalibrationCase(**base)

        for field in ("id", "title", "resume_text", "jd_text", "target_role"):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    InterviewCalibrationCase(
                        **(base | {field: " ", "answer_rules": [_rule()]})
                    )

    def test_run_preserves_selected_rule_ids_and_requires_positive_number(self) -> None:
        run = InterviewCalibrationRun(
            case_id="C03",
            run_number=1,
            initial_state={"interview_plan": {}},
            final_state={"assessment_report": {}},
            selected_rule_ids=["C03_project", "C03_transfer"],
            assertions=[
                CalibrationAssertion(code="terminal_state", passed=True, message="ok")
            ],
        )

        self.assertEqual(run.selected_rule_ids, ["C03_project", "C03_transfer"])
        with self.assertRaises(ValidationError):
            InterviewCalibrationRun(
                **(run.model_dump() | {"run_number": 0})
            )


if __name__ == "__main__":
    unittest.main()
