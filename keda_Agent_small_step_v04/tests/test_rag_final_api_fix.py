import inspect
from datetime import datetime, timezone
from threading import RLock
import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from profile_agent.graphs.interview import build_interview_graph
from profile_agent.llm import LLM
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
    GeneratedQuestion,
    InterviewPlan,
)
from profile_agent.schemas.runtime_schema import (
    AnswerProcessingResult,
    InterviewTurn,
)
from profile_agent.web.container import WebContainer


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=2,
        closing_buffer_minutes=0,
        targets=[
            AssessmentTarget(
                id="target_a",
                objective="验证问题分析能力",
                target_type="problem_solving",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id="requirement_a",
                        description="能够分析问题并解释取舍",
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


def make_initial_state() -> dict:
    return {"interview_plan": make_plan()}


def question_generator(**_kwargs) -> GeneratedQuestion:
    return GeneratedQuestion(text="请描述一次真实的问题排查经历。")


def answer_processor(**kwargs) -> AnswerProcessingResult:
    return AnswerProcessingResult(
        new_evidences=[],
        runtime_state=kwargs["runtime_state"],
    )


def report_generator(**_kwargs):
    raise AssertionError("this regression test should not reach report generation")


class RagFinalApiFixTest(unittest.TestCase):
    def test_interview_graph_keeps_legacy_positional_parameter_order(self) -> None:
        graph = build_interview_graph(
            question_generator,
            answer_processor,
            InMemorySaver(),
            lambda: NOW,
            report_generator,
        )

        result = graph.invoke(
            make_initial_state(),
            {"configurable": {"thread_id": "legacy-positional"}},
        )

        self.assertTrue(result["__interrupt__"])

    def test_question_retriever_is_keyword_only_after_legacy_parameters(self) -> None:
        parameters = inspect.signature(build_interview_graph).parameters

        self.assertEqual(
            list(parameters)[:5],
            [
                "question_generator",
                "answer_processor",
                "checkpointer",
                "now_provider",
                "report_generator",
            ],
        )
        self.assertEqual(
            parameters["question_retriever"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )

    def test_interview_graph_preserves_disabled_checkpointer_option(self) -> None:
        graph = build_interview_graph(
            question_generator,
            answer_processor,
            False,
            lambda: NOW,
            report_generator,
        )

        self.assertIsNotNone(graph)

    def test_web_container_keeps_legacy_positional_field_order(self) -> None:
        repository = object()
        pre_interview_graph = object()
        document_extractor = object()
        dispatcher = object()
        role_profile = object()
        interview_graph = object()
        checkpoint_connection = object()
        interview_lock = RLock()

        container = WebContainer(
            repository,
            pre_interview_graph,
            document_extractor,
            dispatcher,
            role_profile,
            interview_graph,
            checkpoint_connection,
            interview_lock,
        )

        self.assertIs(container.interview_graph, interview_graph)
        self.assertIs(container.checkpoint_connection, checkpoint_connection)
        self.assertIs(container.interview_lock, interview_lock)
        self.assertIsNone(container.question_retriever)

    def test_retrieval_trace_is_publicly_hidden_but_checkpoint_resume_preserves_it(
        self,
    ) -> None:
        graph = build_interview_graph(
            question_generator=question_generator,
            answer_processor=answer_processor,
            now_provider=lambda: NOW,
            report_generator=report_generator,
        )
        config = {"configurable": {"thread_id": "private-trace"}}

        graph.invoke(make_initial_state(), config)
        first_state = graph.get_state(config).values
        first_turn = first_state["interview_turns"][0]

        self.assertIsInstance(first_turn, InterviewTurn)
        self.assertEqual(first_turn.retrieval_trace.status, "unavailable")
        self.assertNotIn("retrieval_trace", first_turn.model_dump())
        self.assertNotIn("retrieval_trace", LLM._jsonable(first_turn))

        graph.invoke(Command(resume="候选人的回答"), config)
        resumed_state = graph.get_state(config).values

        self.assertEqual(
            resumed_state["interview_turns"][0].retrieval_trace.status,
            "unavailable",
        )


if __name__ == "__main__":
    unittest.main()
