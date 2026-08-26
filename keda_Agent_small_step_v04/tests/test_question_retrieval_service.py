from __future__ import annotations

from datetime import date, datetime, timezone
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
    QuestionRetrievalResult,
    RetrievedQuestion,
)
from profile_agent.services.question_retrieval_service import (
    QuestionRetriever,
    build_question_retrieval_intent,
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


class RetrieverTests(unittest.TestCase):
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
            {"q_low_trust", "q_high_trust"},
        )
        self.assertIn("vector_similarity", retriever.last_rank_trace[0]["components"])
        self.assertIn("trust", retriever.last_rank_trace[0]["components"])
        self.assertIn("freshness", retriever.last_rank_trace[0]["components"])
        self.assertIn("coverage", retriever.last_rank_trace[0]["components"])
        self.assertIn("mode", retriever.last_rank_trace[0]["components"])
        self.assertIn("duplicate_penalty", retriever.last_rank_trace[0]["components"])
        self.assertIn("asked_penalty", retriever.last_rank_trace[0]["components"])

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
