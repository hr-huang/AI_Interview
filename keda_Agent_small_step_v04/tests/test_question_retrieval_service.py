from __future__ import annotations

from datetime import date, datetime, timezone
import math
import unittest

from profile_agent.knowledge.qdrant_question_store import QuestionStoreSearchResult
from profile_agent.schemas.interview_schema import (
    AskAction,
    AssessmentTarget,
    EvidenceRequirement,
    InterviewPlan,
)
from profile_agent.schemas.job_schema import JobProfile, JobRequirement
from profile_agent.schemas.resume_schema import (
    ProjectExperience,
    ResumeProfile,
)
from profile_agent.schemas.runtime_schema import InterviewTurn
from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    QuestionModePolicy,
    QuestionRetrievalIntent,
    QuestionRetrievalResult,
    RetrievedQuestion,
)
from profile_agent.services.question_retrieval_service import (
    ModeMatchTier,
    QuestionRetriever,
    build_query_embedding_text,
    build_question_retrieval_intent,
    route_mode_candidates,
)


STARTED_AT = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 8, 26)


def make_plan(
    *,
    target_id: str = "target_01",
    requirement_id: str = "target_01_req_01",
    dimension_id: str | None = "role_dim_03",
    mode: str = "scenario",
    objective: str = "验证候选人设计可验证的 Agent 检索与工具流程",
    requirement: str = "能够说明上下文、检索、状态和工具副作用的边界",
) -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=8,
        closing_buffer_minutes=2,
        targets=[
            AssessmentTarget(
                id=target_id,
                objective=objective,
                target_type="problem_solving",
                competency_ids=["competency_01"],
                evidence_requirements=[
                    EvidenceRequirement(
                        id=requirement_id,
                        description=requirement,
                        planned_role_dimension_id=dimension_id,
                    )
                ],
                related_claim_ids=["claim_01"],
                priority="high",
                must_cover=True,
                time_budget_minutes=8,
                preferred_modes=[mode],
            )
        ],
    )


def make_action(
    *,
    target_id: str = "target_01",
    requirement_id: str = "target_01_req_01",
    mode: str = "scenario",
) -> AskAction:
    return AskAction(
        target_id=target_id,
        primary_requirement_id=requirement_id,
        question_mode=mode,
        reason="cover requirement",
    )


def make_resume() -> ResumeProfile:
    return ResumeProfile(
        summary="候选人邮箱 candidate@example.com，负责 Agent 项目",
        skills=["Python", "RAG", "工具调用"],
        projects=[
            ProjectExperience(
                name="知识库助手",
                description="实现文档检索和工具编排",
                responsibilities=["设计检索链路"],
                achievements=["提升回答质量"],
                technologies=["Python", "Qdrant"],
            ),
            ProjectExperience(
                name="第二个不应完整展开的项目",
                description="隐私项目内容",
                responsibilities=["未验证工作"],
                achievements=["内部指标"],
                technologies=["secret-token"],
            ),
        ],
        education=["不应进入完整查询"],
        claims_to_verify=[],
        uncertainties=[],
    )


def make_job() -> JobProfile:
    return JobProfile(
        role="AI Agent 应用工程师",
        responsibilities=["建设检索和工具调用流程", "保障可观测性"],
        requirements=[
            JobRequirement(name="RAG", description="理解检索、引用和上下文生命周期"),
            JobRequirement(name="安全", description="校验工具参数和副作用"),
        ],
        uncertainties=[],
    )


def make_turn(sequence_number: int, *, answer: str | None) -> InterviewTurn:
    return InterviewTurn(
        id=f"turn_{sequence_number:02d}",
        sequence_number=sequence_number,
        target_id="target_01",
        primary_requirement_id="target_01_req_01",
        question_mode="scenario",
        question=f"问题 {sequence_number}",
        answer=answer,
        asked_at=STARTED_AT,
        answered_at=STARTED_AT if answer is not None else None,
    )


def make_record(
    question_id: str,
    *,
    trust_level: str = "medium",
    valid_until: date = date(2027, 2, 26),
    question_text: str | None = None,
    skills: list[str] | None = None,
) -> InterviewQuestionRecord:
    return InterviewQuestionRecord(
        question_id=question_id,
        question_text=question_text or f"如何设计 {question_id} 的检索流程？",
        role="ai_agent_engineer",
        role_version="2026-H2",
        dimension_id="role_dim_03",
        skills=skills or ["检索", "工具调用"],
        question_mode="scenario",
        difficulty="intermediate",
        expected_signals=["边界"],
        critical_errors=["未经校验执行工具"],
        follow_up_seeds=["如何验证结果？"],
        company_tags=[],
        source_id=f"source-{question_id}",
        source_url="https://example.com/source",
        source_title="Synthetic source",
        source_type="test_only_synthetic",
        published_at=date(2026, 7, 1),
        verified_at=date(2026, 8, 1),
        valid_until=valid_until,
        trust_level=trust_level,
        status="active",
        version=1,
        content_hash="sha256:test",
    )


class FakeEmbedding:
    def __init__(self, vectors: list[list[float]] | None = None, error: Exception | None = None):
        self.vectors = vectors or [[1.0, 0.0, 0.0]]
        self.error = error
        self.inputs: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.inputs.extend(texts)
        if self.error is not None:
            raise self.error
        return self.vectors


class FakeStore:
    def __init__(self, result: QuestionStoreSearchResult | None = None, error: Exception | None = None):
        self.result = result or QuestionStoreSearchResult(status="no_match")
        self.error = error
        self.calls: list[dict[str, object]] = []

    def search(self, **kwargs: object) -> QuestionStoreSearchResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class IntentBuilderTests(unittest.TestCase):
    def test_query_embedding_projection_rejects_unknown_or_pii_dimension_ids(self) -> None:
        intent = QuestionRetrievalIntent(
            query_text="Agent 失败恢复",
            role="ai_agent_engineer",
            dimension_id="role_dim_01",
            question_mode="scenario",
            difficulty="intermediate",
        )
        for dimension_id in ("role_dim_untrusted", "candidate@example.com"):
            with self.subTest(dimension_id=dimension_id):
                invalid_intent = intent.model_copy(update={"dimension_id": dimension_id})
                with self.assertRaisesRegex(ValueError, "dimension_id"):
                    build_query_embedding_text(invalid_intent, [])

    def test_query_embedding_projection_uses_intent_and_role_terms(self) -> None:
        intent = QuestionRetrievalIntent(
            query_text="  如何设计 Agent 的失败恢复边界？\u00a0",
            role="ai_agent_engineer",
            dimension_id="role_dim_01",
            question_mode="scenario",
            difficulty="advanced",
            excluded_question_ids=["q-secret"],
        )

        text = build_query_embedding_text(intent, ["  失败恢复\n", "Agent"])

        self.assertEqual(
            text.splitlines(),
            [
                "question=Agent 失败恢复 失败恢复边界 恢复",
                "business_constraint=",
                "skills=Agent,失败恢复",
                "dimension_terms=role_dim_01",
                "primary_mode=scenario",
                "compatible_modes=",
            ],
        )
        self.assertNotIn("q-secret", text)
        self.assertNotIn("advanced", text)

    def test_query_embedding_projection_drops_untrusted_role_terms(self) -> None:
        intent = QuestionRetrievalIntent(
            query_text="如何设计 Agent 的失败恢复边界？",
            role="ai_agent_engineer",
            dimension_id="role_dim_01",
            question_mode="scenario",
            difficulty="advanced",
        )

        text = build_query_embedding_text(
            intent,
            [
                "Agent",
                "任务编排",
                "candidate@example.com",
                "https://evil.example/Agent",
                "UNRECOGNIZED_ROLE_TEXT",
            ],
        )

        self.assertIn("skills=Agent,任务编排", text)
        for forbidden in (
            "candidate@example.com",
            "https://evil.example/Agent",
            "UNRECOGNIZED_ROLE_TEXT",
        ):
            self.assertNotIn(forbidden, text)

        pii_only_text = build_query_embedding_text(
            intent,
            ["Agent@example.com", "https://evil.example/RAG", "unknown-token"],
        )
        self.assertIn("skills=", pii_only_text)
        self.assertNotIn("skills=Agent", pii_only_text)
        self.assertNotIn("skills=RAG", pii_only_text)

    def test_query_embedding_projection_uses_only_approved_intent_anchors(self) -> None:
        intent = QuestionRetrievalIntent(
            query_text=(
                "dimension=role_dim_01 | mode=scenario | depth=advanced | "
                "objective=JD resume candidate@example.com https://evil.example/Agent "
                "Agent 失败恢复边界 | requirement=secret-token"
            ),
            role="ai_agent_engineer",
            dimension_id="role_dim_01",
            question_mode="scenario",
            difficulty="advanced",
        )

        text = build_query_embedding_text(intent, [])

        self.assertIn("question=Agent 失败恢复 失败恢复边界 恢复", text)
        for forbidden in (
            "depth=",
            "advanced",
            "JD",
            "resume",
            "candidate@example.com",
            "https://evil.example/Agent",
            "secret-token",
        ):
            self.assertNotIn(forbidden, text)

    def test_resolves_target_requirement_and_uses_bounded_safe_anchors(self) -> None:
        intent = build_question_retrieval_intent(
            action=make_action(),
            plan=make_plan(),
            resume_profile=make_resume(),
            job_profile=make_job(),
            recent_turns=[
                make_turn(1, answer="旧回答"),
                make_turn(2, answer=None),
                make_turn(3, answer="较新回答"),
                make_turn(4, answer="最新回答 candidate@example.com"),
            ],
            evidence_summaries=["已覆盖输入输出；仍缺少失败恢复边界"],
            excluded_question_ids=["q_02", "q_01", "q_02", "  ", "q_01"],
        )

        self.assertEqual(intent.role, "ai_agent_engineer")
        self.assertEqual(intent.dimension_id, "role_dim_03")
        self.assertEqual(intent.question_mode, "scenario")
        self.assertEqual(intent.difficulty, "intermediate")
        self.assertEqual(intent.excluded_question_ids, ["q_01", "q_02"])
        self.assertLessEqual(len(intent.query_text), 512)
        self.assertIn("role_dim_03", intent.query_text)
        self.assertIn("失败恢复边界", intent.query_text)
        self.assertNotIn("candidate@example.com", intent.query_text)
        self.assertNotIn("第二个不应完整展开的项目", intent.query_text)
        self.assertNotIn("不应进入完整查询", intent.query_text)
        self.assertNotIn("secret-token", intent.query_text)
        self.assertNotIn("旧回答", intent.query_text)

    def test_builder_is_deterministic_and_rejects_invalid_plan_references(self) -> None:
        kwargs = {
            "action": make_action(),
            "plan": make_plan(),
            "resume_profile": make_resume(),
            "job_profile": make_job(),
            "recent_turns": [make_turn(1, answer="回答")],
            "evidence_summaries": ["缺口"],
            "excluded_question_ids": ["q_02", "q_01"],
        }
        self.assertEqual(
            build_question_retrieval_intent(**kwargs).model_dump(),
            build_question_retrieval_intent(**kwargs).model_dump(),
        )

        with self.assertRaisesRegex(ValueError, "target"):
            build_question_retrieval_intent(
                action=make_action(target_id="missing_target"),
                plan=make_plan(),
            )
        with self.assertRaisesRegex(ValueError, "requirement"):
            build_question_retrieval_intent(
                action=make_action(requirement_id="missing_requirement"),
                plan=make_plan(),
            )

    def test_builder_requires_a_planned_dimension_instead_of_guessing(self) -> None:
        with self.assertRaisesRegex(ValueError, "dimension"):
            build_question_retrieval_intent(
                action=make_action(),
                plan=make_plan(dimension_id=None),
            )

    def test_builder_rejects_dimension_outside_role_pack_allowlist(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported role dimension_id"):
            build_question_retrieval_intent(
                action=make_action(),
                plan=make_plan(dimension_id="role_dim_untrusted"),
            )

    def test_builder_rejects_duplicate_requirement_ids_in_selected_target(self) -> None:
        plan = make_plan()
        plan.targets[0].evidence_requirements.append(
            EvidenceRequirement(
                id="target_01_req_01",
                description="ambiguous duplicate requirement",
                planned_role_dimension_id="role_dim_03",
            )
        )

        with self.assertRaisesRegex(ValueError, "duplicate requirement_id"):
            build_question_retrieval_intent(action=make_action(), plan=plan)

    def test_builder_maps_every_question_mode_to_a_bounded_difficulty(self) -> None:
        expected = {
            "foundation": "foundation",
            "project_deep_dive": "intermediate",
            "scenario": "intermediate",
            "system_design": "advanced",
            "coding": "intermediate",
            "follow_up": "intermediate",
        }
        for mode, difficulty in expected.items():
            with self.subTest(mode=mode):
                intent = build_question_retrieval_intent(
                    action=make_action(mode=mode),
                    plan=make_plan(mode=mode),
                )
                self.assertEqual(intent.difficulty, difficulty)

    def test_builder_redacts_common_secret_and_authorization_variants(self) -> None:
        attacks = [
            ("SILICONFLOW_API_KEY=sf-secret-123", "sf-secret-123"),
            ("OPENAI_API_KEY openai-secret", "openai-secret"),
            ("OPENAI_API_KEY='quoted openai secret'", "quoted openai secret"),
            ("API key: split-api-secret", "split-api-secret"),
            ("private key = 'split-private-secret'", "split-private-secret"),
            ("access key split-access-secret", "split-access-secret"),
            ("client secret: split-client-secret", "split-client-secret"),
            ("API KEY=split-uppercase-secret", "split-uppercase-secret"),
            ("API key is split-natural-api-secret", "split-natural-api-secret"),
            ("private key is 'split-natural-private-secret'", "split-natural-private-secret"),
            ("access key is split-natural-access-secret", "split-natural-access-secret"),
            ("client secret is split-natural-client-secret", "split-natural-client-secret"),
            ("auth token is split-natural-auth-secret", "split-natural-auth-secret"),
            ("token is split-natural-token-secret", "split-natural-token-secret"),
            ("password split-password-secret", "split-password-secret"),
            ("password is 'split-password-is-secret'", "split-password-is-secret"),
            ("secret split-secret-value", "split-secret-value"),
            ("credential split-credential-value", "split-credential-value"),
            ("authorization split-authorization-value", "split-authorization-value"),
            ("API key abcdef", "abcdef"),
            ("secret openai", "openai"),
            ("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
            ("AWS_SECRET_ACCESS_KEY=aws-secret", "aws-secret"),
            (
                "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.signature",
                "eyJhbGciOiJIUzI1NiJ9.abc.signature",
            ),
            ("Authorization Bearer header-secret", "header-secret"),
            ("Bearer 'quoted bearer secret'", "quoted bearer secret"),
            ("bearer bearer-secret", "bearer-secret"),
            ("Bearer x", "Bearer x"),
            ("api-key api-secret", "api-secret"),
            ("accessToken camel-access-secret", "camel-access-secret"),
            ("authToken camel-auth-secret", "camel-auth-secret"),
            ("apiSecret camel-api-secret", "camel-api-secret"),
            ("client_secret client-secret", "client-secret"),
            ("secret-token", "secret-token"),
            ("sk-proj-1234567890abcdef", "sk-proj-1234567890abcdef"),
            ("sk_live_1234567890abcdef", "sk_live_1234567890abcdef"),
            ("sk_live_short", "sk_live_short"),
            ("ghp_1234567890abcdef1234567890abcdef", "ghp_1234567890abcdef1234567890abcdef"),
            ("token: plain-secret-value", "plain-secret-value"),
            ("token token123", "token123"),
        ]
        for attack, secret_value in attacks:
            with self.subTest(attack=attack):
                intent = build_question_retrieval_intent(
                    action=make_action(),
                    plan=make_plan(),
                    evidence_summaries=[attack],
                )
                self.assertNotIn(attack, intent.query_text)
                self.assertNotIn(secret_value, intent.query_text)
                if attack.startswith("accessToken"):
                    self.assertNotIn("accessToken", intent.query_text)
                if attack.startswith("authToken"):
                    self.assertNotIn("authToken", intent.query_text)
                if attack.startswith("apiSecret"):
                    self.assertNotIn("apiSecret", intent.query_text)
                if attack.startswith("OPENAI_API_KEY='"):
                    self.assertNotIn("openai", intent.query_text.lower())
                if "quoted" in attack:
                    self.assertNotIn("quoted", intent.query_text)
                if "split-" in attack:
                    self.assertNotIn("split-", intent.query_text)

                embedding = FakeEmbedding()
                store = FakeStore(QuestionStoreSearchResult(status="no_match"))
                QuestionRetriever(embedding, store, today=TODAY).retrieve(intent)
                self.assertEqual(
                    embedding.inputs,
                    [build_query_embedding_text(intent, ())],
                )
                self.assertNotIn(secret_value, embedding.inputs[0])

    def test_builder_redacts_pii_adjacent_to_chinese_text(self) -> None:
        attacks = [
            ("candidate@example.com候选人", "candidate@example.com"),
            ("访问https://example.com候选项目", "https://example.com"),
            ("电话13812345678，负责项目", "13812345678"),
            ("联系candidate@example.com；负责 RAG", "candidate@example.com"),
        ]

        for attack, secret_value in attacks:
            with self.subTest(attack=attack):
                intent = build_question_retrieval_intent(
                    action=make_action(),
                    plan=make_plan(),
                    evidence_summaries=[attack],
                )
                self.assertNotIn(secret_value, intent.query_text)

                embedding = FakeEmbedding()
                QuestionRetriever(
                    embedding,
                    FakeStore(QuestionStoreSearchResult(status="no_match")),
                    today=TODAY,
                ).retrieve(intent)
                self.assertNotIn(secret_value, embedding.inputs[0])

    def test_builder_preserves_ordinary_token_and_authorization_technical_terms(self) -> None:
        technical_terms = [
            "tokenization strategy",
            "token 生命周期",
            "工具 token 调用和上下文",
            "token budget",
            "OAuth authorization code flow",
            "authorization policy",
            "password rotation policy",
            "credential store",
            "JWT validation",
            "Bearer authentication",
            "Basic auth flow",
            "Bearer token flow",
        ]

        for term in technical_terms:
            with self.subTest(term=term):
                intent = build_question_retrieval_intent(
                    action=make_action(),
                    plan=make_plan(),
                    evidence_summaries=[term],
                )
                self.assertIn(term, intent.query_text)

    def test_builder_preserves_all_sections_under_independent_budgets(self) -> None:
        long_value = "锚点-" * 400
        plan = make_plan(objective=long_value, requirement=long_value)
        resume = ResumeProfile(
            skills=[long_value],
            projects=[
                ProjectExperience(
                    name=long_value,
                    description=long_value,
                    technologies=[long_value],
                )
            ],
        )
        job = JobProfile(
            role="AI Agent",
            responsibilities=[long_value],
            requirements=[JobRequirement(name=long_value, description=long_value)],
        )
        intent = build_question_retrieval_intent(
            action=make_action(),
            plan=plan,
            resume_profile=resume,
            job_profile=job,
            recent_turns=[make_turn(1, answer=long_value), make_turn(2, answer=long_value)],
            evidence_summaries=[long_value],
        )

        self.assertLessEqual(len(intent.query_text), 512)
        for section in (
            "dimension=",
            "mode=",
            "depth=",
            "objective=",
            "requirement=",
            "coverage_gap=",
            "jd=",
            "resume=",
            "recent=",
        ):
            with self.subTest(section=section):
                self.assertIn(section, intent.query_text)

    def test_builder_keeps_allowlisted_terms_and_drops_unrecognized_tail_markers(self) -> None:
        long_tail = "前缀 " + ("未识别原文 " * 80)
        plan = make_plan(
            objective=long_tail + " Agent OBJECTIVE_TAIL",
            requirement=long_tail + " RAG REQUIREMENT_TAIL",
        )
        job = JobProfile(
            role="AI Agent",
            responsibilities=["未识别 JD_RESPONSIBILITY"],
            requirements=[
                JobRequirement(name="JD_NAME", description=long_tail + " 检索 JD_TAIL")
            ],
        )
        resume = ResumeProfile(
            skills=["RESUME_SKILL", "工具调用"],
            projects=[
                ProjectExperience(
                    name="RESUME_PROJECT",
                    description=long_tail + " 上下文 RESUME_TAIL",
                    technologies=["Python"],
                )
            ],
        )
        recent = [make_turn(1, answer=long_tail + " JWT validation RECENT_TAIL")]

        intent = build_question_retrieval_intent(
            action=make_action(),
            plan=plan,
            job_profile=job,
            resume_profile=resume,
            recent_turns=recent,
            evidence_summaries=[long_tail + " 失败恢复边界 GAP_TAIL"],
        )

        for marker in (
            "OBJECTIVE_TAIL",
            "REQUIREMENT_TAIL",
            "GAP_TAIL",
            "JD_NAME",
            "RESUME_SKILL",
            "RECENT_TAIL",
        ):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, intent.query_text)

        for safe_term in ("Agent", "RAG", "检索", "工具调用", "上下文", "JWT validation", "失败恢复边界"):
            with self.subTest(safe_term=safe_term):
                self.assertIn(safe_term, intent.query_text)

    def test_builder_uses_controlled_placeholder_for_untrusted_secret_only_input(self) -> None:
        intent = build_question_retrieval_intent(
            action=make_action(),
            plan=make_plan(),
            evidence_summaries=["AUTHORIZATION=Bearer marker-secret"],
        )

        self.assertIn("coverage_gap=none", intent.query_text)
        self.assertNotIn("marker-secret", intent.query_text)

    def test_builder_query_contains_only_controlled_fields_and_allowlisted_terms(self) -> None:
        unknown_markers = (
            "UNRECOGNIZED_OBJECTIVE_SPAN",
            "UNRECOGNIZED_REQUIREMENT_SPAN",
            "UNRECOGNIZED_GAP_SPAN",
            "UNRECOGNIZED_JD_SPAN",
            "UNRECOGNIZED_RESUME_SPAN",
            "UNRECOGNIZED_RECENT_SPAN",
        )
        plan = make_plan(
            objective="UNRECOGNIZED_OBJECTIVE_SPAN Agent",
            requirement="UNRECOGNIZED_REQUIREMENT_SPAN RAG",
        )
        job = JobProfile(
            role="UNRECOGNIZED_JD_ROLE",
            responsibilities=["UNRECOGNIZED_JD_RESPONSIBILITY"],
            requirements=[
                JobRequirement(
                    name="UNRECOGNIZED_JD_SPAN",
                    description="JWT validation UNRECOGNIZED_JD_DESCRIPTION",
                )
            ],
        )
        resume = ResumeProfile(
            summary="UNRECOGNIZED_RESUME_SUMMARY",
            skills=["UNRECOGNIZED_RESUME_SPAN", "工具调用"],
            projects=[
                ProjectExperience(
                    name="UNRECOGNIZED_PROJECT_NAME",
                    description="上下文 UNRECOGNIZED_PROJECT_DESCRIPTION",
                    technologies=["UNRECOGNIZED_TECHNOLOGY"],
                )
            ],
        )
        recent = [make_turn(1, answer="Bearer authentication UNRECOGNIZED_RECENT_SPAN")]

        intent = build_question_retrieval_intent(
            action=make_action(),
            plan=plan,
            resume_profile=resume,
            job_profile=job,
            recent_turns=recent,
            evidence_summaries=["UNRECOGNIZED_GAP_SPAN 检索"],
        )

        for marker in unknown_markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, intent.query_text)
        for safe_term in ("Agent", "RAG", "检索", "JWT validation", "工具调用", "上下文", "Bearer authentication"):
            with self.subTest(safe_term=safe_term):
                self.assertIn(safe_term, intent.query_text)

        embedding = FakeEmbedding()
        QuestionRetriever(
            embedding,
            FakeStore(QuestionStoreSearchResult(status="no_match")),
            today=TODAY,
        ).retrieve(intent)
        for marker in unknown_markers:
            self.assertNotIn(marker, embedding.inputs[0])

    def test_builder_is_stable_for_long_and_redacted_anchor_inputs(self) -> None:
        kwargs = {
            "action": make_action(),
            "plan": make_plan(
                objective=("objective " * 100) + " OBJECTIVE_TAIL",
                requirement=("requirement " * 100) + " REQUIREMENT_TAIL",
            ),
            "evidence_summaries": ["OPENAI_API_KEY=stable-secret " + ("gap " * 100)],
            "recent_turns": [make_turn(1, answer=("answer " * 100) + " RECENT_TAIL")],
        }

        outputs = [
            build_question_retrieval_intent(**kwargs).model_dump()
            for _ in range(5)
        ]

        self.assertTrue(all(output == outputs[0] for output in outputs))
        self.assertLessEqual(len(outputs[0]["query_text"]), 512)


class RetrieverTests(unittest.TestCase):
    def test_route_prefers_exact_primary_over_compatible_candidates(self) -> None:
        exact = RetrievedQuestion(record=make_record("q-exact"), score=0.1, index_version="v2")
        compatible_record = make_record("q-compatible").model_copy(
            update={"question_mode": "system_design", "primary_mode": "system_design"}
        )
        compatible = RetrievedQuestion(record=compatible_record, score=0.99, index_version="v2")
        routed = route_mode_candidates(
            QuestionRetrievalIntent(
                query_text="query", role="ai_agent_engineer", dimension_id="role_dim_03",
                question_mode="scenario", difficulty="intermediate",
            ),
            [compatible, exact],
            QuestionModePolicy.default(),
        )
        self.assertEqual([item.question_id for item in routed], ["q-exact"])
        self.assertEqual(getattr(routed, "match_tier", ModeMatchTier.EXACT), ModeMatchTier.EXACT)

    def test_route_returns_compatible_in_frozen_order_only_when_primary_empty(self) -> None:
        records = []
        for mode in ("coding", "system_design"):
            record = make_record(f"q-{mode}").model_copy(
                update={"question_mode": mode, "primary_mode": mode, "compatible_modes": ["scenario"]}
            )
            records.append(RetrievedQuestion(record=record, score=0.5, index_version="v2"))
        intent = QuestionRetrievalIntent(
            query_text="query", role="ai_agent_engineer", dimension_id="role_dim_03",
            question_mode="scenario", difficulty="intermediate",
        )
        routed = route_mode_candidates(intent, records, QuestionModePolicy.default())
        self.assertEqual([item.question_id for item in routed], ["q-system_design", "q-coding"])
        self.assertEqual(getattr(routed, "match_tier", ModeMatchTier.EXACT), ModeMatchTier.COMPATIBLE)

    def test_route_requires_requested_mode_in_record_compatible_modes(self) -> None:
        record = make_record("q-not-compatible").model_copy(
            update={"question_mode": "system_design", "primary_mode": "system_design", "compatible_modes": []}
        )
        intent = QuestionRetrievalIntent(query_text="query", role="ai_agent_engineer", dimension_id="role_dim_03", question_mode="scenario", difficulty="intermediate")
        routed = route_mode_candidates(intent, [RetrievedQuestion(record=record, score=0.5, index_version="v2")], QuestionModePolicy.default())
        self.assertEqual(routed, [])

    def test_retriever_queries_compatible_only_after_empty_primary(self) -> None:
        compatible_record = make_record("q-compatible").model_copy(
            update={"question_mode": "system_design", "primary_mode": "system_design", "compatible_modes": ["scenario"]}
        )
        store = FakeStore()
        def search(**kwargs: object) -> QuestionStoreSearchResult:
            store.calls.append(kwargs)
            requested = kwargs["intent"].question_mode  # type: ignore[union-attr]
            if requested == "system_design":
                return QuestionStoreSearchResult(status="hit", hits=[RetrievedQuestion(record=compatible_record, score=0.4, index_version="v2")], index_version="v2")
            return QuestionStoreSearchResult(status="no_match", index_version="v2")
        store.search = search  # type: ignore[method-assign]
        retriever = QuestionRetriever(FakeEmbedding(), store, today=TODAY)
        intent = QuestionRetrievalIntent(query_text="query", role="ai_agent_engineer", dimension_id="role_dim_03", question_mode="scenario", difficulty="intermediate")
        result = retriever.retrieve(intent)
        self.assertEqual(result.status, "hit")
        self.assertEqual(result.trace.mode_match_tier, "compatible")
        self.assertGreaterEqual(len(store.calls), 2)

    def test_retriever_rejects_low_trust_even_when_store_returns_hit(self) -> None:
        low = make_record("q-low", trust_level="low")
        store = FakeStore(QuestionStoreSearchResult(status="hit", hits=[RetrievedQuestion(record=low, score=.9, index_version="v2")], index_version="v2"))
        retriever = QuestionRetriever(FakeEmbedding(), store, today=TODAY)
        intent = QuestionRetrievalIntent(query_text="query", role="ai_agent_engineer", dimension_id="role_dim_03", question_mode="scenario", difficulty="intermediate")
        self.assertEqual(retriever.retrieve(intent).status, "no_match")
    def test_retrieve_path_uses_safe_projection_instead_of_raw_intent(self) -> None:
        embedding = FakeEmbedding()
        store = FakeStore(QuestionStoreSearchResult(status="no_match"))
        retriever = QuestionRetriever(embedding, store, today=TODAY)
        intent = QuestionRetrievalIntent(
            query_text=(
                "dimension=role_dim_01 | mode=scenario | depth=advanced | "
                "objective=JD marker resume marker candidate@example.com "
                "https://evil.example/Agent Agent | requirement=answer marker"
            ),
            role="ai_agent_engineer",
            dimension_id="role_dim_01",
            question_mode="scenario",
            difficulty="advanced",
        )

        result = retriever.retrieve(intent)

        self.assertEqual(result.status, "no_match")
        self.assertEqual(len(embedding.inputs), 1)
        query_text = embedding.inputs[0]
        self.assertEqual(
            query_text.splitlines(),
            [
                "question=Agent",
                "business_constraint=",
                "skills=",
                "dimension_terms=role_dim_01",
                "primary_mode=scenario",
                "compatible_modes=",
            ],
        )
        for forbidden in (
            "depth=",
            "advanced",
            "JD marker",
            "resume marker",
            "candidate@example.com",
            "https://evil.example/Agent",
            "answer marker",
        ):
            self.assertNotIn(forbidden, query_text)

    def test_retriever_explicitly_projects_v1_hits_before_v2_selection(self) -> None:
        selected = RetrievedQuestion(
            record=make_record("q_v1_candidate"),
            score=0.91,
            index_version="idx-v1",
        )
        store = FakeStore(
            QuestionStoreSearchResult(
                status="hit",
                hits=[selected],
                index_version="idx-v1",
            )
        )
        retriever = QuestionRetriever(
            FakeEmbedding(),
            store,
            today=TODAY,
            question_mode_policy=QuestionModePolicy.default(),
        )

        result = retriever.retrieve(
            build_question_retrieval_intent(action=make_action(), plan=make_plan())
        )

        self.assertEqual(result.status, "hit")
        projected = result.selected_question.record
        self.assertEqual(projected.primary_mode, "scenario")
        self.assertEqual(projected.compatible_modes, [])
        self.assertIn("primary_mode", projected.model_fields_set)
        self.assertIn("source_ids", projected.model_fields_set)
        self.assertEqual(len(store.calls), 1)

    def test_retriever_rejects_v2_record_outside_fixed_mode_policy(self) -> None:
        record = make_record("q_invalid_policy").model_copy(
            update={
                "dimension_id": "role_dim_01",
                "primary_mode": "coding",
                "question_mode": "coding",
            }
        )
        hit = RetrievedQuestion(record=record, score=0.9, index_version="idx")
        retriever = QuestionRetriever(
            FakeEmbedding(),
            FakeStore(
                QuestionStoreSearchResult(
                    status="hit",
                    hits=[hit],
                    index_version="idx",
                )
            ),
            today=TODAY,
            question_mode_policy=QuestionModePolicy.default(),
        )

        result = retriever.retrieve(
            build_question_retrieval_intent(
                action=make_action(),
                plan=make_plan(dimension_id="role_dim_01"),
            )
        )

        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.selected_question)

    def test_retriever_rejects_an_incompatible_mode_assignment(self) -> None:
        record = make_record("q_invalid_compatible").model_copy(
            update={
                "primary_mode": "scenario",
                "question_mode": "scenario",
                "compatible_modes": ["foundation", "coding"],
                "business_constraint": "约束",
                "dimension_terms": ["检索"],
                "source_ids": ["source-q_invalid_compatible"],
            }
        )
        hit = RetrievedQuestion(record=record, score=0.9, index_version="idx")
        retriever = QuestionRetriever(
            FakeEmbedding(),
            FakeStore(
                QuestionStoreSearchResult(
                    status="hit",
                    hits=[hit],
                    index_version="idx",
                )
            ),
            today=TODAY,
        )

        result = retriever.retrieve(
            build_question_retrieval_intent(action=make_action(), plan=make_plan())
        )

        self.assertEqual(result.status, "unavailable")

    def test_retriever_revalidates_mutable_typed_hits_and_falls_back_safely(self) -> None:
        updates = {
            "hash": {"content_hash": "not-a-hash"},
            "date": {"valid_until": "not-a-date"},
            "role": {"role": "not-a-role"},
            "mode": {"question_mode": "not-a-mode"},
        }
        intent = build_question_retrieval_intent(action=make_action(), plan=make_plan())
        for label, update in updates.items():
            with self.subTest(label=label):
                valid_hit = RetrievedQuestion(
                    record=make_record("q_malformed"),
                    score=0.9,
                    index_version="idx",
                )
                malformed_record = valid_hit.record.model_copy(update=update)
                malformed_hit = valid_hit.model_copy(update={"record": malformed_record})
                retriever = QuestionRetriever(
                    FakeEmbedding(),
                    FakeStore(
                        QuestionStoreSearchResult(
                            status="hit",
                            hits=[malformed_hit],
                            index_version="idx",
                        )
                    ),
                    today=TODAY,
                )

                result = retriever.retrieve(intent)

                self.assertEqual(result.status, "unavailable")
                self.assertIsNone(result.selected_question)

    def test_retriever_selects_explainable_deterministic_best_hit(self) -> None:
        first = RetrievedQuestion(
            record=make_record("q_low_trust", trust_level="low"),
            score=0.95,
            index_version="idx-v1",
        )
        second = RetrievedQuestion(
            record=make_record("q_high_trust", trust_level="high"),
            score=0.94,
            index_version="idx-v1",
        )
        store = FakeStore(
            QuestionStoreSearchResult(status="hit", hits=[first, second], index_version="idx-v1")
        )
        embedding = FakeEmbedding()
        retriever = QuestionRetriever(embedding_client=embedding, question_store=store, today=TODAY)

        result = retriever.retrieve(
            build_question_retrieval_intent(action=make_action(), plan=make_plan())
        )

        self.assertIsInstance(result, QuestionRetrievalResult)
        self.assertEqual(result.status, "hit")
        self.assertEqual(result.selected_question.question_id, "q_high_trust")
        self.assertEqual(store.calls[0]["limit"], 3)
        self.assertEqual(len(embedding.inputs), 1)
        self.assertTrue(retriever.last_rank_trace)
        self.assertEqual(
            {item["question_id"] for item in retriever.last_rank_trace},
            {"q_high_trust"},
        )
        self.assertIn("vector_similarity", retriever.last_rank_trace[0]["components"])
        self.assertIn("trust", retriever.last_rank_trace[0]["components"])
        self.assertIn("freshness", retriever.last_rank_trace[0]["components"])
        self.assertIn("coverage", retriever.last_rank_trace[0]["components"])
        self.assertIn("mode", retriever.last_rank_trace[0]["components"])
        self.assertIn("duplicate_penalty", retriever.last_rank_trace[0]["components"])
        self.assertIn("asked_penalty", retriever.last_rank_trace[0]["components"])
        expected_sources = {
            second.question_id: (second.source_id, second.index_version),
        }
        for trace in retriever.last_rank_trace:
            source_id, index_version = expected_sources[trace["question_id"]]
            self.assertEqual(trace["source_id"], source_id)
            self.assertEqual(trace["index_version"], index_version)
            self.assertTrue(math.isfinite(trace["score"]))

    def test_retriever_uses_stable_question_id_tiebreak_and_at_most_three_hits(self) -> None:
        hits = [
            RetrievedQuestion(record=make_record("q_03"), score=0.8, index_version="idx"),
            RetrievedQuestion(record=make_record("q_01"), score=0.8, index_version="idx"),
            RetrievedQuestion(record=make_record("q_02"), score=0.8, index_version="idx"),
            RetrievedQuestion(record=make_record("q_04"), score=0.8, index_version="idx"),
        ]
        store = FakeStore(QuestionStoreSearchResult(status="hit", hits=hits, index_version="idx"))
        retriever = QuestionRetriever(FakeEmbedding(), store, today=TODAY, max_candidates=99)
        result = retriever.retrieve(build_question_retrieval_intent(action=make_action(), plan=make_plan()))

        self.assertEqual(store.calls[0]["limit"], 3)
        self.assertEqual(result.selected_question.question_id, "q_01")
        self.assertLessEqual(len(retriever.last_rank_trace), 3)

    def test_retriever_honors_a_smaller_candidate_limit_after_filtering(self) -> None:
        hits = [
            RetrievedQuestion(record=make_record("q_01"), score=0.8, index_version="idx"),
            RetrievedQuestion(record=make_record("q_02"), score=0.7, index_version="idx"),
        ]
        store = FakeStore(QuestionStoreSearchResult(status="hit", hits=hits, index_version="idx"))
        retriever = QuestionRetriever(FakeEmbedding(), store, today=TODAY, max_candidates=1)

        result = retriever.retrieve(
            build_question_retrieval_intent(action=make_action(), plan=make_plan())
        )

        self.assertEqual(result.status, "hit")
        self.assertEqual(len(retriever.last_rank_trace), 1)

    def test_retriever_filters_all_raw_hits_before_selecting_top_three(self) -> None:
        wrong_dimension = make_record("q_wrong_dimension").model_copy(
            update={"dimension_id": "role_dim_99"}
        )
        wrong_role = make_record("q_wrong_role").model_copy(
            update={"role": "ai_agent_engineer_other"}
        )
        wrong_mode = make_record("q_wrong_mode").model_copy(
            update={"question_mode": "coding"}
        )
        retired = make_record("q_retired").model_copy(update={"status": "retired"})
        expired = make_record("q_expired", valid_until=date(2026, 8, 25))
        valid = make_record("q_valid")
        hits = [
            RetrievedQuestion(record=wrong_dimension, score=0.99, index_version="idx"),
            RetrievedQuestion(record=wrong_role, score=0.98, index_version="idx"),
            RetrievedQuestion(record=wrong_mode, score=0.97, index_version="idx"),
            RetrievedQuestion(record=retired, score=0.96, index_version="idx"),
            RetrievedQuestion(record=expired, score=0.95, index_version="idx"),
            RetrievedQuestion(record=valid, score=0.80, index_version="idx"),
        ]
        store = FakeStore(QuestionStoreSearchResult(status="hit", hits=hits, index_version="idx"))
        retriever = QuestionRetriever(FakeEmbedding(), store, today=TODAY)

        result = retriever.retrieve(
            build_question_retrieval_intent(action=make_action(), plan=make_plan())
        )

        self.assertEqual(result.status, "hit")
        self.assertEqual(result.selected_question.question_id, "q_valid")

    def test_retriever_rejects_missing_nonfinite_score_bare_record_and_provenance(self) -> None:
        record = make_record("q_malformed")
        malformed_hits = [
            RetrievedQuestion(record=record, score=None, index_version="idx"),
            RetrievedQuestion(
                record=record,
                score=0.9,
                index_version="idx",
            ).model_copy(update={"score": math.nan}),
            RetrievedQuestion(
                record=record,
                score=0.9,
                index_version="idx",
            ).model_copy(update={"score": math.inf}),
            RetrievedQuestion(
                record=record,
                score=0.9,
                index_version="idx",
            ).model_copy(update={"score": "0.9"}),
            record,
            RetrievedQuestion(record=record, score=0.9, index_version=None),
            RetrievedQuestion(record=record, score=0.9, index_version="idx").model_copy(
                update={"index_version": 123}
            ),
            RetrievedQuestion(
                record=record.model_copy(update={"source_id": 123}),
                score=0.9,
                index_version="idx",
            ),
        ]
        store = FakeStore(
            QuestionStoreSearchResult(status="hit", hits=malformed_hits, index_version="idx")
        )
        retriever = QuestionRetriever(FakeEmbedding(), store, today=TODAY)

        result = retriever.retrieve(
            build_question_retrieval_intent(action=make_action(), plan=make_plan())
        )

        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.selected_question)

    def test_retriever_skips_a_malformed_hit_when_a_valid_hit_exists(self) -> None:
        malformed = RetrievedQuestion(
            record=make_record("q_malformed"),
            score=0.9,
            index_version="idx",
        ).model_copy(update={"score": None})
        valid = RetrievedQuestion(record=make_record("q_valid"), score=0.8, index_version="idx")
        store = FakeStore(
            QuestionStoreSearchResult(
                status="hit",
                hits=[malformed, valid],
                index_version="idx",
            )
        )
        retriever = QuestionRetriever(FakeEmbedding(), store, today=TODAY)

        result = retriever.retrieve(
            build_question_retrieval_intent(action=make_action(), plan=make_plan())
        )

        self.assertEqual(result.status, "hit")
        self.assertEqual(result.selected_question.question_id, "q_valid")

    def test_retriever_requires_real_store_hit_and_keeps_trace_score_provenance_equal(self) -> None:
        selected = RetrievedQuestion(record=make_record("q_valid"), score=0.91, index_version="idx")
        embedding = FakeEmbedding()
        store = FakeStore(
            QuestionStoreSearchResult(status="hit", hits=[selected], index_version="idx")
        )
        retriever = QuestionRetriever(embedding, store, today=TODAY)

        result = retriever.retrieve(
            build_question_retrieval_intent(action=make_action(), plan=make_plan())
        )

        self.assertEqual(result.status, "hit")
        self.assertEqual(result.trace.question_id, result.selected_question.question_id)
        self.assertEqual(result.trace.source_id, result.selected_question.source_id)
        self.assertEqual(result.trace.score, result.selected_question.score)
        self.assertEqual(result.trace.index_version, result.selected_question.index_version)

    def test_retriever_does_not_treat_a_bare_hit_list_as_a_store_result(self) -> None:
        selected = RetrievedQuestion(record=make_record("q_valid"), score=0.91, index_version="idx")
        # A search adapter must return the Task 4/5 store envelope.  A raw list
        # has no status or index provenance and must not be promoted to a hit.
        store = FakeStore(result=[selected])  # type: ignore[arg-type]
        retriever = QuestionRetriever(FakeEmbedding(), store, today=TODAY)

        result = retriever.retrieve(
            build_question_retrieval_intent(action=make_action(), plan=make_plan())
        )

        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.selected_question)

    def test_retriever_rejects_datetime_as_of_instead_of_leaking_type_error(self) -> None:
        retriever = QuestionRetriever(FakeEmbedding(), FakeStore(), today=TODAY)
        intent = build_question_retrieval_intent(action=make_action(), plan=make_plan())

        with self.assertRaises(TypeError):
            retriever.retrieve(intent, today=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc))

    def test_retrieval_failure_statuses_are_honest_safe_fallbacks(self) -> None:
        intent = build_question_retrieval_intent(action=make_action(), plan=make_plan())
        for status in ("no_match", "unavailable", "index_mismatch"):
            with self.subTest(status=status):
                retriever = QuestionRetriever(
                    FakeEmbedding(),
                    FakeStore(QuestionStoreSearchResult(status=status)),
                    today=TODAY,
                )
                result = retriever.retrieve(intent)
                self.assertEqual(result.status, status)
                self.assertIsNone(result.selected_question)

        for status in ("empty", "provider_error"):
            with self.subTest(status=status):
                retriever = QuestionRetriever(
                    FakeEmbedding(),
                    FakeStore(QuestionStoreSearchResult(status=status)),  # type: ignore[arg-type]
                    today=TODAY,
                )
                result = retriever.retrieve(intent)
                self.assertEqual(result.status, "unavailable")
                self.assertIsNone(result.selected_question)

        for dependency in (FakeEmbedding(error=RuntimeError("private secret")), FakeEmbedding()):
            store_error = RuntimeError("candidate@example.com") if dependency.error is None else None
            retriever = QuestionRetriever(
                dependency,
                FakeStore(error=store_error),
                today=TODAY,
            )
            result = retriever.retrieve(intent)
            self.assertEqual(result.status, "unavailable")
            self.assertIsNone(result.selected_question)

    def test_retriever_does_not_select_excluded_hit_from_an_untrusted_store(self) -> None:
        excluded = RetrievedQuestion(record=make_record("q_asked"), score=0.99, index_version="idx")
        allowed = RetrievedQuestion(record=make_record("q_allowed"), score=0.8, index_version="idx")
        store = FakeStore(QuestionStoreSearchResult(status="hit", hits=[excluded, allowed], index_version="idx"))
        retriever = QuestionRetriever(FakeEmbedding(), store, today=TODAY)
        intent = build_question_retrieval_intent(
            action=make_action(), plan=make_plan(), excluded_question_ids=["q_asked"]
        )

        result = retriever.retrieve(intent)

        self.assertEqual(result.status, "hit")
        self.assertEqual(result.selected_question.question_id, "q_allowed")


if __name__ == "__main__":
    unittest.main()
