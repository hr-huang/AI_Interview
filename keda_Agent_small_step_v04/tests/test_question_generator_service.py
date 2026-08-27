from datetime import date, datetime, timezone
import unittest

from profile_agent.schemas.claim_schema import ClaimItem, ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AskAction,
    AssessmentTarget,
    EvidenceRequirement,
    GeneratedQuestion,
    InterviewPlan,
)
from profile_agent.schemas.runtime_schema import InterviewTurn
from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    QuestionRetrievalResult,
    QuestionRetrievalTrace,
    RetrievedQuestion,
)
from profile_agent.services.question_generator_service import generate_question


class FakeLLM:
    """不会访问真实 API 的结构化输出替身。"""

    def __init__(self, response: GeneratedQuestion) -> None:
        self.response = response
        self.calls: list[tuple[list[tuple[str, str]], type[GeneratedQuestion]]] = []

    def structured(
        self,
        messages: list[tuple[str, str]],
        schema: type[GeneratedQuestion],
    ) -> GeneratedQuestion:
        self.calls.append((messages, schema))
        return self.response


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=10,
        closing_buffer_minutes=2,
        targets=[
            AssessmentTarget(
                id="target_01",
                objective="验证候选人解释并发状态更新的能力",
                target_type="implementation",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id="target_01_req_01",
                        description="能够说明并发状态更新中的一致性保证",
                    )
                ],
                related_claim_ids=["claim_01"],
                priority="high",
                must_cover=True,
                time_budget_minutes=10,
                preferred_modes=["scenario", "follow_up"],
            ),
            AssessmentTarget(
                id="target_02",
                objective="验证候选人的问题定位能力",
                target_type="debugging",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id="target_02_req_01",
                        description="能够提出可复现、定位并验证修复的步骤",
                    )
                ],
                related_claim_ids=["claim_02"],
                priority="medium",
                must_cover=False,
                time_budget_minutes=8,
                preferred_modes=["scenario"],
            ),
        ],
    )


def make_action(
    target_id: str = "target_01",
    requirement_id: str = "target_01_req_01",
    question_mode: str = "scenario",
) -> AskAction:
    return AskAction(
        target_id=target_id,
        primary_requirement_id=requirement_id,
        question_mode=question_mode,
        reason="获取该要求所需的面试证据",
    )


def make_claim_registry() -> ClaimRegistry:
    return ClaimRegistry(
        claims=[
            ClaimItem(
                id="claim_01",
                text="曾设计可恢复的 Agent 工作流",
                source_section="project",
                claim_type="experience",
            ),
            ClaimItem(
                id="claim_02",
                text="曾负责高并发服务的故障排查",
                source_section="work_experience",
                claim_type="responsibility",
            ),
        ]
    )


def make_turn(
    sequence_number: int,
    question: str,
    answer: str | None,
) -> InterviewTurn:
    return InterviewTurn(
        id=f"turn_{sequence_number:02d}",
        sequence_number=sequence_number,
        target_id="target_01",
        primary_requirement_id="target_01_req_01",
        question_mode="follow_up",
        question=question,
        answer=answer,
        asked_at=datetime(2026, 8, 21, 12, sequence_number, tzinfo=timezone.utc),
    )


class QuestionGeneratorServiceTest(unittest.TestCase):
    def test_selected_retrieval_record_adds_only_safe_grounding_fields(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text="请说明你的方案。"))
        record = InterviewQuestionRecord(
            question_id="q_private_001",
            question_text="ORIGINAL_RAG_QUESTION",
            role="ai_agent_engineer",
            role_version="2026-H2",
            dimension_id="role_dim_01",
            skills=["SKILL_ONE", "SKILL_TWO"],
            question_mode="scenario",
            difficulty="intermediate",
            expected_signals=["NEVER_LEAK_EXPECTED_SIGNAL"],
            critical_errors=["NEVER_LEAK_CRITICAL_ERROR"],
            follow_up_seeds=["NEVER_LEAK_FOLLOW_UP"],
            company_tags=[],
            source_id="src_private_001",
            source_url="https://example.com/private",
            source_title="Private title must not be copied",
            source_type="SOURCE_TYPE",
            published_at=date(2026, 7, 1),
            verified_at=date(2026, 8, 20),
            valid_until=date(2027, 2, 20),
            trust_level="medium",
            status="active",
            version=1,
            content_hash="sha256:private",
        )
        retrieval_result = QuestionRetrievalResult(
            status="hit",
            as_of=date(2026, 8, 26),
            selected_question=RetrievedQuestion(
                record=record,
                score=0.91,
                index_version="index-private",
            ),
            trace=QuestionRetrievalTrace(
                status="hit",
                question_id=record.question_id,
                source_id=record.source_id,
                score=0.91,
                index_version="index-private",
            ),
        )

        generate_question(
            action=make_action(),
            plan=make_plan(),
            retrieval_result=retrieval_result,
            llm_client=fake_llm,
        )

        prompt = "\n".join(content for _, content in fake_llm.calls[0][0])
        self.assertIn("ORIGINAL_RAG_QUESTION", prompt)
        self.assertIn("能够说明并发状态更新中的一致性保证", prompt)
        self.assertIn("SKILL_ONE", prompt)
        self.assertIn("SKILL_TWO", prompt)
        self.assertIn("SOURCE_TYPE", prompt)
        self.assertIn("2026-07-01", prompt)
        for forbidden in (
            "NEVER_LEAK_EXPECTED_SIGNAL",
            "NEVER_LEAK_CRITICAL_ERROR",
            "NEVER_LEAK_FOLLOW_UP",
            "q_private_001",
            "src_private_001",
            "index-private",
            "https://example.com/private",
            "Private title must not be copied",
        ):
            self.assertNotIn(forbidden, prompt)

    def test_no_match_or_unavailable_retrieval_keeps_the_legacy_prompt(self) -> None:
        legacy_llm = FakeLLM(GeneratedQuestion(text="请说明你的方案。"))
        degraded_llm = FakeLLM(GeneratedQuestion(text="请说明你的方案。"))
        legacy_kwargs = {
            "action": make_action(),
            "plan": make_plan(),
        }

        generate_question(**legacy_kwargs, llm_client=legacy_llm)
        generate_question(
            **legacy_kwargs,
            retrieval_result=QuestionRetrievalResult(status="unavailable"),
            llm_client=degraded_llm,
        )

        legacy_prompt = "\n".join(content for _, content in legacy_llm.calls[0][0])
        degraded_prompt = "\n".join(content for _, content in degraded_llm.calls[0][0])
        self.assertEqual(legacy_prompt, degraded_prompt)

    def test_generates_one_trimmed_question_with_target_requirement_and_claim_context(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text="  请说明你如何保证并发更新的一致性？  "))

        generated = generate_question(
            action=make_action(),
            plan=make_plan(),
            claim_registry=make_claim_registry(),
            llm_client=fake_llm,
        )

        self.assertEqual(generated.text, "请说明你如何保证并发更新的一致性？")
        self.assertEqual(len(fake_llm.calls), 1)
        messages, schema = fake_llm.calls[0]
        prompt = "\n".join(content for _, content in messages)
        self.assertIs(schema, GeneratedQuestion)
        self.assertIn("验证候选人解释并发状态更新的能力", prompt)
        self.assertIn("能够说明并发状态更新中的一致性保证", prompt)
        self.assertIn("scenario", prompt)
        self.assertIn("曾设计可恢复的 Agent 工作流", prompt)
        self.assertNotIn("曾负责高并发服务的故障排查", prompt)
        self.assertIn("只问一个清晰的问题", prompt)
        self.assertIn("不要泄露答案", prompt)
        self.assertIn("不要评分", prompt)
        self.assertIn("不要列出多个问题", prompt)

    def test_prompt_pins_generated_question_to_the_exact_root_json_shape(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text="请描述你的实现方案。"))

        generate_question(
            action=make_action(),
            plan=make_plan(),
            llm_client=fake_llm,
        )

        prompt = "\n".join(content for _, content in fake_llm.calls[0][0])
        self.assertIn('JSON 根对象必须严格是 {"text": "问题文本"}', prompt)
        self.assertIn('不要返回 {"GeneratedQuestion": ...}', prompt)
        self.assertIn("根对象只能包含 text", prompt)

    def test_rejects_unknown_target_before_calling_llm(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text="不应生成"))

        with self.assertRaisesRegex(ValueError, "target_id"):
            generate_question(
                action=make_action(
                    target_id="target_99",
                    requirement_id="target_99_req_01",
                ),
                plan=make_plan(),
                llm_client=fake_llm,
            )

        self.assertEqual(fake_llm.calls, [])

    def test_rejects_unknown_requirement_before_calling_llm(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text="不应生成"))

        with self.assertRaisesRegex(ValueError, "requirement_id"):
            generate_question(
                action=make_action(requirement_id="target_01_req_99"),
                plan=make_plan(),
                llm_client=fake_llm,
            )

        self.assertEqual(fake_llm.calls, [])

    def test_rejects_requirement_owned_by_another_target_before_calling_llm(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text="不应生成"))

        with self.assertRaisesRegex(ValueError, "不属于"):
            generate_question(
                action=make_action(requirement_id="target_02_req_01"),
                plan=make_plan(),
                llm_client=fake_llm,
            )

        self.assertEqual(fake_llm.calls, [])

    def test_follow_up_prompt_contains_only_the_last_two_answered_turns(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text="请进一步说明。"))
        recent_turns = [
            make_turn(1, "OLD_QUESTION", "OLD_ANSWER"),
            make_turn(2, "MIDDLE_QUESTION", "MIDDLE_ANSWER"),
            make_turn(3, "LATEST_QUESTION", "LATEST_ANSWER"),
            make_turn(4, "UNANSWERED_QUESTION", None),
        ]

        generate_question(
            action=make_action(question_mode="follow_up"),
            plan=make_plan(),
            recent_turns=recent_turns,
            llm_client=fake_llm,
        )

        prompt = "\n".join(content for _, content in fake_llm.calls[0][0])
        self.assertIn("MIDDLE_QUESTION", prompt)
        self.assertIn("MIDDLE_ANSWER", prompt)
        self.assertIn("LATEST_QUESTION", prompt)
        self.assertIn("LATEST_ANSWER", prompt)
        self.assertNotIn("OLD_QUESTION", prompt)
        self.assertNotIn("OLD_ANSWER", prompt)
        self.assertNotIn("UNANSWERED_QUESTION", prompt)

    def test_rejects_empty_question_text_after_structured_call(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text=" \n\t "))

        with self.assertRaisesRegex(ValueError, "不能为空"):
            generate_question(
                action=make_action(),
                plan=make_plan(),
                llm_client=fake_llm,
            )

        self.assertEqual(len(fake_llm.calls), 1)


if __name__ == "__main__":
    unittest.main()
