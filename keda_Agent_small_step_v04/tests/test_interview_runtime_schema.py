from datetime import datetime, timezone
import unittest

from pydantic import TypeAdapter, ValidationError

from profile_agent.schemas import interview_schema, runtime_schema
from profile_agent.state import main_state


def require_model(test_case: unittest.TestCase, module: object, name: str):
    test_case.assertTrue(
        hasattr(module, name),
        f"{module.__name__} 缺少 {name}",
    )
    return getattr(module, name)


class InterviewRuntimeSchemaTest(unittest.TestCase):
    def test_ask_action_has_ask_discriminator_and_context(self) -> None:
        ask_action = require_model(self, interview_schema, "AskAction")

        action = ask_action(
            target_id="target_01",
            primary_requirement_id="target_01_req_01",
            question_mode="foundation",
            reason="验证基础理解",
        )

        self.assertEqual(action.action, "ask")
        self.assertEqual(action.target_id, "target_01")
        self.assertEqual(action.primary_requirement_id, "target_01_req_01")
        self.assertEqual(action.question_mode, "foundation")

    def test_finish_action_has_finish_discriminator(self) -> None:
        finish_action = require_model(self, interview_schema, "FinishAction")

        action = finish_action(reason="所有核心要求已完成")

        self.assertEqual(action.action, "finish")
        self.assertEqual(action.reason, "所有核心要求已完成")

    def test_interview_action_uses_action_as_discriminator(self) -> None:
        action_type = require_model(self, interview_schema, "InterviewAction")
        ask_action = require_model(self, interview_schema, "AskAction")
        finish_action = require_model(self, interview_schema, "FinishAction")

        adapter = TypeAdapter(action_type)

        ask = adapter.validate_python(
            {
                "action": "ask",
                "target_id": "target_01",
                "primary_requirement_id": "target_01_req_01",
                "question_mode": "follow_up",
                "reason": "继续澄清",
            }
        )
        finish = adapter.validate_python(
            {"action": "finish", "reason": "时间到"}
        )

        self.assertIsInstance(ask, ask_action)
        self.assertIsInstance(finish, finish_action)

        with self.assertRaises(ValidationError):
            adapter.validate_python({"action": "invalid", "reason": "无效"})

    def test_generated_question_only_contains_text(self) -> None:
        generated_question = require_model(
            self,
            interview_schema,
            "GeneratedQuestion",
        )

        question = generated_question(text="请解释你如何处理并发状态更新。")

        self.assertEqual(question.text, "请解释你如何处理并发状态更新。")
        self.assertEqual(set(generated_question.model_fields), {"text"})

    def test_interview_turn_is_flat_and_requires_positive_sequence(self) -> None:
        interview_turn = require_model(self, runtime_schema, "InterviewTurn")
        asked_at = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

        turn = interview_turn(
            id="turn_01",
            sequence_number=1,
            target_id="target_01",
            primary_requirement_id="target_01_req_01",
            question_mode="scenario",
            question="请说明你的排障步骤。",
            answer=None,
            asked_at=asked_at,
        )

        self.assertEqual(turn.id, "turn_01")
        self.assertEqual(turn.sequence_number, 1)
        self.assertIsNone(turn.answer)
        self.assertEqual(turn.asked_at, asked_at)
        self.assertIsNone(turn.answered_at)
        self.assertNotIn("turn_id", interview_turn.model_fields)

        with self.assertRaises(ValidationError):
            interview_turn(
                id="turn_00",
                sequence_number=0,
                target_id="target_01",
                primary_requirement_id="target_01_req_01",
                question_mode="scenario",
                question="无效轮次",
                asked_at=asked_at,
            )

    def test_evidence_draft_and_evidence_hold_structured_facts(self) -> None:
        evidence = require_model(self, runtime_schema, "Evidence")
        evidence_draft = require_model(self, runtime_schema, "EvidenceDraft")
        common = {
            "requirement_ids": ["target_01_req_01"],
            "related_claim_ids": ["claim_01"],
            "polarity": "supporting",
            "strength": "strong",
            "observation": "候选人能说明系统化排障步骤。",
            "source_excerpt": "我先复现问题，再缩小故障范围。",
        }

        draft = evidence_draft(**common)
        persisted = evidence(
            id="evidence_01",
            turn_id="turn_01",
            **common,
        )

        self.assertEqual(draft.requirement_ids, ["target_01_req_01"])
        self.assertEqual(draft.strength, "strong")
        self.assertEqual(persisted.id, "evidence_01")
        self.assertEqual(persisted.turn_id, "turn_01")
        self.assertNotIn("turn_id", evidence_draft.model_fields)

        with self.assertRaises(ValidationError):
            evidence_draft(**{**common, "requirement_ids": []})
        with self.assertRaises(ValidationError):
            evidence_draft(**{**common, "strength": "invalid"})

    def test_evidence_defaults_are_independent(self) -> None:
        evidence_draft = require_model(self, runtime_schema, "EvidenceDraft")

        first = evidence_draft(
            requirement_ids=["req_01"],
            polarity="supporting",
            strength="weak",
            observation="事实一",
            source_excerpt="摘录一",
        )
        second = evidence_draft(
            requirement_ids=["req_02"],
            polarity="contradicting",
            strength="medium",
            observation="事实二",
            source_excerpt="摘录二",
        )

        first.related_claim_ids.append("claim_01")

        self.assertEqual(second.related_claim_ids, [])

    def test_assessments_and_answer_processing_result_have_frozen_shape(self) -> None:
        requirement_assessment = require_model(
            self,
            runtime_schema,
            "RequirementAssessment",
        )
        turn_assessment = require_model(
            self,
            runtime_schema,
            "TurnAssessment",
        )
        answer_processing_result = require_model(
            self,
            runtime_schema,
            "AnswerProcessingResult",
        )
        evidence = require_model(self, runtime_schema, "Evidence")
        runtime_state = require_model(
            self,
            runtime_schema,
            "InterviewRuntimeState",
        )

        requirement = requirement_assessment(
            requirement_id="target_01_req_01",
            recommended_status="sufficient",
            rationale="回答提供了充分证据。",
        )
        assessment = turn_assessment(
            answer_relevance="high",
            evidence_drafts=[],
            requirement_assessments=[requirement],
        )
        persisted_evidence = evidence(
            id="evidence_01",
            turn_id="turn_01",
            requirement_ids=["target_01_req_01"],
            related_claim_ids=[],
            polarity="supporting",
            strength="strong",
            observation="已验证排障能力。",
            source_excerpt="我先复现问题。",
        )
        result = answer_processing_result(
            new_evidences=[persisted_evidence],
            runtime_state=runtime_state(
                started_at=datetime.now(timezone.utc)
            ),
        )

        self.assertEqual(assessment.answer_relevance, "high")
        self.assertEqual(assessment.requirement_assessments, [requirement])
        self.assertEqual(result.new_evidences, [persisted_evidence])
        self.assertLess(
            list(runtime_schema.__dict__).index("InterviewRuntimeState"),
            list(runtime_schema.__dict__).index("AnswerProcessingResult"),
        )

        with self.assertRaises(ValidationError):
            requirement_assessment(
                requirement_id="req_01",
                recommended_status="not_started",
                rationale="不允许的评估状态",
            )

    def test_main_state_exposes_dynamic_interview_blocks(self) -> None:
        annotations = main_state.MainState.__annotations__

        self.assertIn("interview_turns", annotations)
        self.assertIn("evidences", annotations)
        self.assertIn("next_action", annotations)
        self.assertIn("current_question", annotations)
        self.assertIn("current_turn_id", annotations)


if __name__ == "__main__":
    unittest.main()
