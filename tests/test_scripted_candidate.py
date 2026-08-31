from __future__ import annotations

import unittest

from profile_agent.calibration.schemas import ScriptedAnswerRule
from profile_agent.calibration.scripted_candidate import (
    ScriptedAnswerSelectionError,
    select_scripted_answer,
)
from profile_agent.schemas.interview_schema import (
    AskAction,
    AssessmentTarget,
    EvidenceRequirement,
    InterviewPlan,
)


def _target(
    target_id: str,
    requirement_id: str,
    description: str,
) -> AssessmentTarget:
    return AssessmentTarget(
        id=target_id,
        objective="验证能力",
        target_type="implementation",
        competency_ids=[],
        evidence_requirements=[
            EvidenceRequirement(id=requirement_id, description=description)
        ],
        related_claim_ids=[],
        priority="high",
        must_cover=True,
        time_budget_minutes=10,
        preferred_modes=["scenario", "follow_up"],
    )


def _plan(*targets: AssessmentTarget) -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=45,
        max_questions=10,
        closing_buffer_minutes=2,
        targets=list(targets),
    )


def _payload(
    requirement_id: str = "req_transfer",
    *,
    reason: str = "验证迁移能力",
    question: str = "请谈谈安全授权。",
) -> dict[str, object]:
    return {
        "question": question,
        "action": AskAction(
            target_id="target_01",
            primary_requirement_id=requirement_id,
            question_mode="scenario",
            reason=reason,
        ).model_dump(mode="json"),
    }


def _rule(
    rule_id: str,
    terms: list[str],
    answer: str,
    *,
    max_uses: int = 1,
) -> ScriptedAnswerRule:
    return ScriptedAnswerRule(
        id=rule_id,
        match_any=terms,
        answer=answer,
        max_uses=max_uses,
    )


class ScriptedCandidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = _plan(
            _target(
                "target_01",
                "req_transfer",
                "能够把 Agent Workflow 迁移到新场景并说明适配方法",
            )
        )

    def test_matches_requirement_and_reason_without_reading_question_text(self) -> None:
        rules = [
            _rule("transfer", ["迁移", "新场景"], "迁移回答"),
            _rule("safety", ["安全授权"], "安全回答"),
        ]

        answer, rule_id = select_scripted_answer(
            payload=_payload(question="请详细说明安全授权。"),
            plan=self.plan,
            rules=rules,
            usage_counts={},
        )

        self.assertEqual((answer, rule_id), ("迁移回答", "transfer"))

    def test_matching_is_case_insensitive(self) -> None:
        answer, rule_id = select_scripted_answer(
            payload=_payload(reason="Validate WORKFLOW Transfer"),
            plan=self.plan,
            rules=[_rule("english", ["workflow transfer"], "answer")],
            usage_counts={},
        )

        self.assertEqual((answer, rule_id), ("answer", "english"))

    def test_exhausted_rule_is_skipped(self) -> None:
        rules = [
            _rule("first", ["迁移", "新场景"], "first answer"),
            _rule("second", ["迁移"], "second answer", max_uses=2),
        ]

        answer, rule_id = select_scripted_answer(
            payload=_payload(),
            plan=self.plan,
            rules=rules,
            usage_counts={"first": 1},
        )

        self.assertEqual((answer, rule_id), ("second answer", "second"))

    def test_equal_hit_count_uses_rule_order(self) -> None:
        answer, rule_id = select_scripted_answer(
            payload=_payload(),
            plan=self.plan,
            rules=[
                _rule("first", ["迁移"], "first answer"),
                _rule("second", ["新场景"], "second answer"),
            ],
            usage_counts={},
        )

        self.assertEqual((answer, rule_id), ("first answer", "first"))

    def test_explicit_fallback_is_used_only_when_no_semantic_rule_matches(self) -> None:
        fallback = _rule("fallback", ["*"], "无相关实践", max_uses=2)

        answer, rule_id = select_scripted_answer(
            payload=_payload(reason="验证完全不同的能力"),
            plan=self.plan,
            rules=[fallback],
            usage_counts={},
        )
        self.assertEqual((answer, rule_id), ("无相关实践", "fallback"))

        answer, rule_id = select_scripted_answer(
            payload=_payload(),
            plan=self.plan,
            rules=[fallback, _rule("transfer", ["迁移"], "迁移回答")],
            usage_counts={},
        )
        self.assertEqual((answer, rule_id), ("迁移回答", "transfer"))

    def test_no_semantic_match_raises_clear_error(self) -> None:
        with self.assertRaises(ScriptedAnswerSelectionError) as context:
            select_scripted_answer(
                payload=_payload(reason="验证迁移能力"),
                plan=self.plan,
                rules=[_rule("safety", ["安全授权"], "安全回答")],
                usage_counts={},
            )

        message = str(context.exception)
        self.assertIn("req_transfer", message)
        self.assertIn("迁移到新场景", message)
        self.assertIn("验证迁移能力", message)

    def test_unknown_or_duplicate_requirement_is_rejected(self) -> None:
        with self.assertRaisesRegex(ScriptedAnswerSelectionError, "不存在"):
            select_scripted_answer(
                payload=_payload("req_missing"),
                plan=self.plan,
                rules=[_rule("transfer", ["迁移"], "回答")],
                usage_counts={},
            )

        duplicate_plan = _plan(
            _target("target_01", "req_transfer", "迁移到新场景"),
            _target("target_02", "req_transfer", "迁移到新场景"),
        )
        with self.assertRaisesRegex(ScriptedAnswerSelectionError, "重复"):
            select_scripted_answer(
                payload=_payload(),
                plan=duplicate_plan,
                rules=[_rule("transfer", ["迁移"], "回答")],
                usage_counts={},
            )

    def test_malformed_interrupt_payload_is_rejected(self) -> None:
        for payload in ({}, {"action": {"action": "finish", "reason": "done"}}):
            with self.subTest(payload=payload):
                with self.assertRaises(ScriptedAnswerSelectionError):
                    select_scripted_answer(
                        payload=payload,
                        plan=self.plan,
                        rules=[_rule("transfer", ["迁移"], "回答")],
                        usage_counts={},
                    )


if __name__ == "__main__":
    unittest.main()
