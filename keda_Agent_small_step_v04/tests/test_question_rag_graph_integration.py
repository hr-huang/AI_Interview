from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from langgraph.types import Command

from profile_agent.graphs.interview import build_interview_graph
from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    AskAction,
    EvidenceRequirement,
    GeneratedQuestion,
    InterviewPlan,
)
from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    QuestionRetrievalResult,
    QuestionRetrievalTrace,
    RetrievedQuestion,
)
from profile_agent.schemas.runtime_schema import AnswerProcessingResult, InterviewTurn
from profile_agent.services.runtime_state_service import initialize_runtime_state
from profile_agent.web.container import WebContainer
from profile_agent.web.interview_service import InterviewService
from tests.report_test_helpers import make_test_report


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=4,
        closing_buffer_minutes=0,
        targets=[
            AssessmentTarget(
                id="target_rag",
                objective="验证 Agent 失败恢复能力",
                target_type="problem_solving",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id="requirement_rag",
                        description="能够在业务约束下分析失败恢复与取舍",
                        planned_role_dimension_id="role_dim_01",
                    )
                ],
                related_claim_ids=[],
                priority="high",
                must_cover=True,
                time_budget_minutes=10,
                preferred_modes=["scenario"],
            )
        ],
    )


def make_record() -> InterviewQuestionRecord:
    return InterviewQuestionRecord(
        question_id="q_graph_private",
        question_text="GRAPH_ORIGINAL_QUESTION",
        role="ai_agent_engineer",
        role_version="2026-H2",
        dimension_id="role_dim_01",
        skills=["GRAPH_SKILL"],
        question_mode="scenario",
        difficulty="intermediate",
        expected_signals=["GRAPH_EXPECTED_SIGNAL"],
        critical_errors=["GRAPH_CRITICAL_ERROR"],
        follow_up_seeds=["GRAPH_FOLLOW_UP_SEED"],
        company_tags=[],
        source_id="src_graph_private",
        source_url="https://example.com/graph",
        source_title="private graph source",
        source_type="GRAPH_SOURCE_TYPE",
        published_at=date(2026, 8, 1),
        verified_at=date(2026, 8, 20),
        valid_until=date(2027, 2, 20),
        trust_level="medium",
        status="active",
        version=1,
        content_hash="sha256:graph",
    )


def make_hit_result() -> QuestionRetrievalResult:
    record = make_record()
    selected = RetrievedQuestion(record=record, score=0.9, index_version="idx-graph")
    return QuestionRetrievalResult(
        status="hit",
        as_of=date(2026, 8, 26),
        selected_question=selected,
        trace=QuestionRetrievalTrace(
            status="hit",
            question_id=record.question_id,
            source_id=record.source_id,
            score=0.9,
            index_version="idx-graph",
        ),
    )


class FakeRetriever:
    def __init__(self, result: QuestionRetrievalResult) -> None:
        self.result = result
        self.calls = []

    def retrieve(self, intent):
        self.calls.append(intent)
        return self.result


class FakeQuestionGenerator:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(
        self,
        *,
        action: AskAction,
        plan: InterviewPlan,
        claim_registry=None,
        recent_turns=None,
        retrieval_result=None,
    ) -> GeneratedQuestion:
        self.calls.append(
            {
                "action": action,
                "plan": plan,
                "claim_registry": claim_registry,
                "recent_turns": recent_turns,
                "retrieval_result": retrieval_result,
            }
        )
        return GeneratedQuestion(text=f"生成问题 {len(self.calls)}")


def no_op_answer_processor(
    *,
    plan: InterviewPlan,
    runtime_state,
    turn: InterviewTurn,
    existing_evidences,
    claim_registry=None,
) -> AnswerProcessingResult:
    return AnswerProcessingResult(new_evidences=[], runtime_state=runtime_state)


class QuestionRagGraphIntegrationTests(unittest.TestCase):
    NOW = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)

    @staticmethod
    def initial_state() -> dict:
        return {
            "assessment_id": "ast-rag",
            "interview_plan": make_plan(),
            "claim_registry": ClaimRegistry(),
            "runtime_state": initialize_runtime_state(
                make_plan(), started_at=QuestionRagGraphIntegrationTests.NOW
            ),
            "interview_turns": [],
            "evidences": [],
        }

    @staticmethod
    def config() -> dict:
        return {"configurable": {"thread_id": "rag-graph"}}

    @staticmethod
    def interrupt_payload(result: dict) -> dict:
        interrupts = result.get("__interrupt__", [])
        if not interrupts:
            raise AssertionError(f"expected interrupt, got {result!r}")
        return interrupts[0].value

    def build(self, retriever, generator):
        return build_interview_graph(
            question_generator=generator,
            question_retriever=retriever,
            answer_processor=no_op_answer_processor,
            report_generator=lambda **_: make_test_report(),
            now_provider=lambda: self.NOW,
        )

    def test_graph_retrieves_once_before_generation_and_persists_private_trace(self) -> None:
        retriever = FakeRetriever(make_hit_result())
        generator = FakeQuestionGenerator()
        graph = self.build(retriever, generator)

        result = graph.invoke(self.initial_state(), self.config())
        payload = self.interrupt_payload(result)
        state = graph.get_state(self.config()).values

        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        self.assertIn(("supervisor", "retrieve_question"), edges)
        self.assertIn(("retrieve_question", "generate_question"), edges)
        self.assertIn(("generate_question", "wait_for_answer"), edges)
        self.assertEqual(len(retriever.calls), 1)
        self.assertEqual(len(generator.calls), 1)
        self.assertEqual(
            generator.calls[0]["retrieval_result"].selected_question.question_id,
            "q_graph_private",
        )
        self.assertEqual(
            state["interview_turns"][0].retrieval_trace.status,
            "hit",
        )
        self.assertIsNone(state.get("question_retrieval_result"))
        self.assertEqual(payload["question"], "生成问题 1")
        serialized_payload = repr(payload)
        for forbidden in (
            "q_graph_private",
            "src_graph_private",
            "idx-graph",
            "GRAPH_EXPECTED_SIGNAL",
            "GRAPH_CRITICAL_ERROR",
            "GRAPH_FOLLOW_UP_SEED",
        ):
            self.assertNotIn(forbidden, serialized_payload)

    def test_resume_does_not_retrieve_again_for_the_same_interrupt(self) -> None:
        retriever = FakeRetriever(make_hit_result())
        generator = FakeQuestionGenerator()
        graph = self.build(retriever, generator)
        config = self.config()

        graph.invoke(self.initial_state(), config)
        graph.invoke(Command(resume="candidate answer"), config)
        self.assertEqual(len(retriever.calls), 2)
        self.assertEqual(len(generator.calls), 2)

    def test_unavailable_retrieval_still_reaches_candidate_interrupt(self) -> None:
        retriever = FakeRetriever(QuestionRetrievalResult(status="unavailable"))
        generator = FakeQuestionGenerator()
        graph = self.build(retriever, generator)

        result = graph.invoke(self.initial_state(), self.config())
        payload = self.interrupt_payload(result)
        state = graph.get_state(self.config()).values

        self.assertEqual(payload["question"], "生成问题 1")
        self.assertEqual(generator.calls[0]["retrieval_result"].status, "unavailable")
        self.assertEqual(state["interview_turns"][0].retrieval_trace.status, "unavailable")

    def test_public_turn_projection_omits_private_retrieval_provenance(self) -> None:
        retriever = FakeRetriever(make_hit_result())
        generator = FakeQuestionGenerator()
        graph = self.build(retriever, generator)

        graph.invoke(self.initial_state(), self.config())
        state = graph.get_state(self.config()).values

        public_turns = InterviewService._public_turns(state["interview_turns"])
        self.assertEqual(
            public_turns,
            [
                {
                    "id": public_turns[0]["id"],
                    "sequence_number": 1,
                    "question": "生成问题 1",
                    "answer": None,
                }
            ],
        )
        serialized = repr(public_turns)
        for forbidden in (
            "q_graph_private",
            "src_graph_private",
            "idx-graph",
            "GRAPH_EXPECTED_SIGNAL",
            "GRAPH_CRITICAL_ERROR",
            "GRAPH_FOLLOW_UP_SEED",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_test_container_can_inject_the_retriever_without_eager_provider_setup(self) -> None:
        retriever = FakeRetriever(QuestionRetrievalResult(status="unavailable"))

        container = WebContainer.for_test(
            repository=object(),
            pre_interview_graph=object(),
            dispatcher=object(),
            question_retriever=retriever,
        )

        self.assertIs(container.question_retriever, retriever)


if __name__ == "__main__":
    unittest.main()
