from datetime import date, datetime, timezone
import unittest
from datetime import date

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
from profile_agent.schemas.scenario_rag_schema import (
    LockedScenarioContext,
    ScenarioCard,
    ScenarioConstraint,
    ScenarioModule,
)
from profile_agent.services.question_generator_service import generate_question, _retrieval_grounding_text


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


class CandidateSafeProjectionTests(unittest.TestCase):
    def test_grounding_omits_entire_candidate_when_safe_value_contains_pii_or_prompt_marker(self) -> None:
        record = InterviewQuestionRecord(
            question_id="q-unsafe", question_text="联系 candidate@example.com",
            role="ai_agent_engineer", role_version="2026-H2", dimension_id="role_dim_03",
            skills=["检索"], question_mode="scenario", difficulty="intermediate",
            business_constraint="resume: secret", expected_signals=["x"], critical_errors=[], follow_up_seeds=[], company_tags=[],
            source_id="s", source_url="https://x.invalid", source_title="t", source_type="synthetic",
            published_at=date(2026, 1, 1), verified_at=date(2026, 2, 1), valid_until=date(2027, 1, 1),
            trust_level="high", status="active", version=1, content_hash="sha256:x",
        )
        result = QuestionRetrievalResult(status="hit", as_of=date(2026, 2, 1), selected_question=RetrievedQuestion(record=record, score=.5, index_version="v2"), trace=QuestionRetrievalTrace(status="hit", question_id="q-unsafe", source_id="s", score=.5, index_version="v2"))
        self.assertEqual(_retrieval_grounding_text(result, business_constraint="ok"), "")
    def test_grounding_contains_only_candidate_safe_projection(self) -> None:
        record = InterviewQuestionRecord(
            question_id="q-safe",
            question_text="如何验证检索失败恢复？",
            role="ai_agent_engineer", role_version="2026-H2", dimension_id="role_dim_03",
            skills=["检索"], question_mode="scenario", business_constraint="延迟受限",
            difficulty="intermediate",
            expected_signals=["DO NOT LEAK"], critical_errors=["SECRET RUBRIC"],
            follow_up_seeds=["PRIVATE FOLLOWUP"], company_tags=["ACME"],
            source_id="source-private", source_url="https://private.invalid", source_title="PRIVATE TITLE",
            source_type="PRIVATE TYPE", published_at=date(2026, 1, 1), verified_at=date(2026, 2, 1),
            valid_until=date(2027, 1, 1), trust_level="high", status="active", version=1,
            content_hash="sha256:private",
        )
        result = QuestionRetrievalResult(
            status="hit", as_of=date(2026, 2, 1),
            selected_question=RetrievedQuestion(record=record, score=0.9, index_version="v2"),
            trace=QuestionRetrievalTrace(status="hit", question_id="q-safe", source_id="source-private", score=0.9, index_version="v2"),
        )
        text = _retrieval_grounding_text(result, business_constraint="业务约束")
        self.assertIn("Original question", text)
        self.assertIn("Dimension", text)
        for forbidden in ("source-private", "private.invalid", "PRIVATE TITLE", "PRIVATE TYPE", "DO NOT LEAK", "SECRET RUBRIC", "PRIVATE FOLLOWUP", "ACME", "0.9", "v2"):
            self.assertNotIn(forbidden, text)


def make_plan(candidate_focus: str | None = None) -> InterviewPlan:
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
                        candidate_focus=candidate_focus,
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
    def _make_scenario_context(
        self,
        *,
        selected_constraint: ScenarioConstraint | None = None,
        candidate_brief: str | None = None,
        candidate_focus: str | None = None,
    ) -> LockedScenarioContext:
        scenario = ScenarioCard(
            scenario_id="private_scenario_id",
            title="内部业务",
            business_goal="支持订单查询和退款",
            candidate_brief=candidate_brief,
            modules=["private_module_id"],
            valid_from=date(2026, 8, 29),
        )
        module = ScenarioModule(
            module_id="private_module_id",
            scenario_id=scenario.scenario_id,
            primary_dimension_id="role_dim_03",
            supported_requirement_types=["implementation"],
            supported_modes=["scenario", "follow_up"],
            difficulties=["intermediate"],
            opening_goal="验证状态更新与删除边界",
            semantic_text="不应发送到 Prompt 的索引文本",
            evidence_signals=["PRIVATE_SIGNAL"],
            critical_errors=["PRIVATE_ERROR"],
            constraint_ids=["selected_constraint", "unused_constraint"],
            valid_from=date(2026, 8, 29),
        )
        revealed_ids = [selected_constraint.constraint_id] if selected_constraint else []
        return LockedScenarioContext(
            scenario_id=scenario.scenario_id,
            module_id=module.module_id,
            retrieval_unit_id=module.retrieval_unit_id,
            primary_dimension_id=module.primary_dimension_id,
            business_goal=scenario.business_goal,
            opening_goal=module.opening_goal,
            candidate_brief=scenario.candidate_brief,
            candidate_focus=candidate_focus,
            selected_constraint=selected_constraint,
            revealed_constraint_ids=revealed_ids,
            retrieval_status="hit",
        )

    def test_scenario_opening_is_deterministic_and_does_not_leak_unselected_pressure_case(self) -> None:
        bad_llm = FakeLLM(
            GeneratedQuestion(
                text=(
                    "假设退款已经执行成功但接口超时、订单状态异常且用户负面情绪，"
                    "请说明何时转人工、阈值如何设置以及如何避免错误执行退款？"
                )
            )
        )

        generated = generate_question(
            action=make_action(),
            plan=make_plan(),
            scenario_context=self._make_scenario_context(),
            llm_client=bad_llm,
        )

        self.assertEqual(bad_llm.calls, [])
        self.assertIn("支持订单查询和退款", generated.text)
        self.assertIn("整体方案设计", generated.text)
        self.assertNotIn("能够说明", generated.text)
        self.assertEqual(
            sum(generated.text.count(mark) for mark in ("？", "?")),
            1,
        )
        for forbidden in (
            "接口超时",
            "状态异常",
            "负面情绪",
            "转人工",
            "阈值",
            "错误执行退款",
            "退款实际上已成功",
        ):
            self.assertNotIn(forbidden, generated.text)

    def test_scenario_opening_uses_brief_and_candidate_focus_without_llm(self) -> None:
        bad_llm = FakeLLM(
            GeneratedQuestion(text="不应调用模型：会泄露内部约束？")
        )
        context = self._make_scenario_context(
            candidate_brief="你正在为一家电商平台设计客服 Agent，帮助用户完成订单服务。",
            candidate_focus="状态一致性边界",
        )

        generated = generate_question(
            action=make_action(),
            plan=make_plan(candidate_focus="错误优化策略"),
            scenario_context=context,
            llm_client=bad_llm,
        )

        self.assertEqual(bad_llm.calls, [])
        self.assertIn(context.candidate_brief, generated.text)
        self.assertIn("状态一致性边界", generated.text)
        self.assertNotIn("错误优化策略", generated.text)
        self.assertEqual(sum(generated.text.count(mark) for mark in ("？", "?")), 1)
        for forbidden in (
            "PRIVATE_SIGNAL",
            "PRIVATE_ERROR",
            "退款实际上已成功",
            "unused_constraint",
        ):
            self.assertNotIn(forbidden, generated.text)

    def test_missing_context_focus_uses_generic_instead_of_plan_copy(self) -> None:
        bad_llm = FakeLLM(GeneratedQuestion(text="不应调用模型？"))

        for unsafe in (
            "退款实际已经执行成功，只是接口响应超时",
            "退款成功但响应超时",
        ):
            with self.subTest(unsafe=unsafe):
                generated = generate_question(
                    action=make_action(),
                    plan=make_plan(candidate_focus=unsafe),
                    scenario_context=self._make_scenario_context(),
                    llm_client=bad_llm,
                )
                self.assertIn("整体方案设计", generated.text)
                self.assertNotIn(unsafe, generated.text)
                self.assertNotIn("响应超时", generated.text)
                self.assertEqual(
                    sum(generated.text.count(mark) for mark in ("？", "?")),
                    1,
                )
        self.assertEqual(bad_llm.calls, [])

    def test_follow_up_keeps_legacy_description_even_when_candidate_focus_exists(self) -> None:
        bad_llm = FakeLLM(GeneratedQuestion(text="不应调用模型？"))

        generated = generate_question(
            action=make_action(question_mode="follow_up"),
            plan=make_plan(candidate_focus="首题优化策略"),
            scenario_context=self._make_scenario_context(),
            llm_client=bad_llm,
        )

        self.assertEqual(bad_llm.calls, [])
        self.assertIn("并发状态更新中的一致性保证", generated.text)
        self.assertNotIn("首题优化策略", generated.text)

    def test_scenario_follow_up_without_remaining_constraint_uses_safe_verification_question(self) -> None:
        bad_llm = FakeLLM(
            GeneratedQuestion(text="假设接口超时且订单状态异常，你会如何设置转人工阈值？")
        )

        generated = generate_question(
            action=make_action(question_mode="follow_up"),
            plan=make_plan(),
            scenario_context=self._make_scenario_context(),
            llm_client=bad_llm,
        )

        self.assertEqual(bad_llm.calls, [])
        self.assertIn("刚才的回答", generated.text)
        self.assertIn("如何验证", generated.text)
        self.assertEqual(sum(generated.text.count(mark) for mark in ("？", "?")), 1)
        for forbidden in ("接口超时", "状态异常", "转人工", "阈值"):
            self.assertNotIn(forbidden, generated.text)

    def test_scenario_follow_up_keeps_one_selected_constraint_for_the_llm(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text="如果接口响应超时，你会如何处理？"))
        selected = ScenarioConstraint(
            constraint_id="selected_constraint",
            scenario_id="private_scenario_id",
            module_id="private_module_id",
            fact="退款实际上已成功但响应超时",
        )

        generate_question(
            action=make_action(question_mode="follow_up"),
            plan=make_plan(),
            scenario_context=self._make_scenario_context(
                selected_constraint=selected,
            ),
            llm_client=fake_llm,
        )

        self.assertEqual(len(fake_llm.calls), 1)
        prompt = "\n".join(content for _, content in fake_llm.calls[0][0])
        self.assertIn("退款实际上已成功但响应超时", prompt)
        self.assertNotIn("PRIVATE_SIGNAL", prompt)
        self.assertNotIn("PRIVATE_ERROR", prompt)
        self.assertNotIn("unused_constraint", prompt)

    def test_scenario_context_prompt_contains_only_candidate_safe_fields(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text="请设计一个方案。"))
        scenario = ScenarioCard(
            scenario_id="private_scenario_id",
            title="内部业务",
            business_goal="支持订单查询和退款",
            modules=["private_module_id"],
            valid_from=date(2026, 8, 29),
        )
        module = ScenarioModule(
            module_id="private_module_id",
            scenario_id=scenario.scenario_id,
            primary_dimension_id="role_dim_03",
            supported_requirement_types=["implementation"],
            supported_modes=["scenario"],
            difficulties=["intermediate"],
            opening_goal="验证状态更新与删除边界",
            semantic_text="不应发送到 Prompt 的索引文本",
            evidence_signals=["PRIVATE_SIGNAL"],
            critical_errors=["PRIVATE_ERROR"],
            constraint_ids=["selected_constraint", "unused_constraint"],
            valid_from=date(2026, 8, 29),
        )
        selected = ScenarioConstraint(
            constraint_id="selected_constraint",
            scenario_id=scenario.scenario_id,
            module_id=module.module_id,
            fact="退款实际上已成功但响应超时",
        )
        context = LockedScenarioContext(
            scenario_id=scenario.scenario_id,
            module_id=module.module_id,
            retrieval_unit_id=module.retrieval_unit_id,
            primary_dimension_id=module.primary_dimension_id,
            business_goal=scenario.business_goal,
            opening_goal=module.opening_goal,
            selected_constraint=selected,
            revealed_constraint_ids=[selected.constraint_id],
            retrieval_status="hit",
        )

        generate_question(
            action=make_action(question_mode="follow_up"),
            plan=make_plan(),
            scenario_context=context,
            llm_client=fake_llm,
        )

        prompt = "\n".join(content for _, content in fake_llm.calls[0][0])
        for allowed in ("支持订单查询和退款", "退款实际上已成功但响应超时"):
            self.assertIn(allowed, prompt)
        for forbidden in (
            "PRIVATE_SIGNAL", "PRIVATE_ERROR", "unused_constraint", "不应发送到 Prompt 的索引文本",
            "private_scenario_id", "private_module_id", "0.9", "score", "验证状态更新与删除边界",
        ):
            self.assertNotIn(forbidden, prompt)

        dumped = context.model_dump(mode="json")
        for forbidden in ("scenario", "module", "opening_goal", "evidence_signals", "critical_errors", "base_constraints", "constraint_id"):
            self.assertNotIn(forbidden, dumped)
        self.assertEqual(set(dumped["selected_constraint"]), {"fact"})

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
        self.assertIn("Dimension:", prompt)
        self.assertIn("Question mode:", prompt)
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

    def test_prompt_requires_real_scenario_and_verifiable_evidence(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text="请讲一个你实际处理过的故障。"))

        generate_question(
            action=make_action(),
            plan=make_plan(),
            recent_turns=[make_turn(1, "你做过什么？", "我做过一个课程项目。")],
            llm_client=fake_llm,
        )

        prompt = "\n".join(content for _, content in fake_llm.calls[0][0])
        self.assertIn("真实业务情景", prompt)
        self.assertIn("不得考察框架定义背诵", prompt)
        self.assertIn("可量化验证", prompt)
        self.assertIn("学生项目、竞赛、开源或实习", prompt)
        self.assertIn("AI Agent应用工程师（校招/初级）", prompt)
        self.assertIn("课程项目", prompt)
        self.assertIn("可复现实验", prompt)
        self.assertIn("具体事实、取舍、失败边界、可量化验证", prompt)

    def test_prompt_requires_mode_fit_without_multiple_subquestions(self) -> None:
        fake_llm = FakeLLM(GeneratedQuestion(text="请描述一次你定位并验证修复的经历。"))

        generate_question(
            action=make_action(question_mode="follow_up"),
            plan=make_plan(),
            llm_client=fake_llm,
        )

        prompt = "\n".join(content for _, content in fake_llm.calls[0][0])
        self.assertIn("贴合 question_mode", prompt)
        self.assertIn("不要一次列出多个子问题", prompt)

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
