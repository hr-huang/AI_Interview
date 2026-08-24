from datetime import datetime, timezone
import unittest

from langgraph.types import Command
from pydantic import ValidationError

from profile_agent.graphs.interview import build_interview_graph
from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
    FinishAction,
    GeneratedQuestion,
    InterviewPlan,
)
from profile_agent.schemas.runtime_schema import (
    AnswerProcessingResult,
    Evidence,
    InterviewTurn,
)
from profile_agent.services.runtime_state_service import record_requirement_evidence
from tests.report_test_helpers import make_test_report


class ReportGeneratorRecorder:
    def __init__(self, result=None) -> None:
        self.calls: list[dict[str, object]] = []
        self.result = make_test_report() if result is None else result

    def __call__(
        self,
        *,
        plan,
        runtime_state,
        turns,
        evidences,
        claim_registry,
        target_role,
        scoring_blueprint=None,
    ):
        self.calls.append(
            {
                "plan": plan,
                "runtime_state": runtime_state,
                "turns": turns,
                "evidences": evidences,
                "claim_registry": claim_registry,
                "target_role": target_role,
                "scoring_blueprint": scoring_blueprint,
            }
        )
        return self.result


class InterviewReportIntegrationTest(unittest.TestCase):
    NOW = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)

    def make_plan(self) -> InterviewPlan:
        return InterviewPlan(
            duration_minutes=30,
            max_questions=1,
            closing_buffer_minutes=0,
            targets=[
                AssessmentTarget(
                    id="target_a",
                    objective="验证 A 能力",
                    target_type="problem_solving",
                    competency_ids=[],
                    evidence_requirements=[
                        EvidenceRequirement(
                            id="requirement_a",
                            description="能够清晰分析问题",
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

    def make_initial_state(self) -> dict[str, object]:
        return {
            "interview_plan": self.make_plan(),
            "claim_registry": ClaimRegistry(),
            "target_role": "目标岗位",
        }

    @staticmethod
    def question_generator(**_kwargs) -> GeneratedQuestion:
        return GeneratedQuestion(text="请说明你的分析过程")

    @staticmethod
    def answer_processor(
        plan: InterviewPlan,
        runtime_state,
        turn: InterviewTurn,
        existing_evidences: list[Evidence],
        claim_registry: ClaimRegistry | None = None,
    ) -> AnswerProcessingResult:
        evidence = Evidence(
            id="evidence_a",
            turn_id=turn.id,
            requirement_ids=["requirement_a"],
            polarity="supporting",
            strength="strong",
            observation="候选人给出了清晰回答",
            source_excerpt=turn.answer or "",
        )
        updated_runtime = record_requirement_evidence(
            runtime_state,
            requirement_id="requirement_a",
            status="sufficient",
            supporting_evidence_ids=[evidence.id],
            contradicting_evidence_ids=[],
            known_evidence_ids={evidence.id},
        )
        return AnswerProcessingResult(
            new_evidences=[evidence],
            runtime_state=updated_runtime,
        )

    def build_graph(self, report_generator):
        return build_interview_graph(
            question_generator=self.question_generator,
            answer_processor=self.answer_processor,
            report_generator=report_generator,
            now_provider=lambda: self.NOW,
        )

    @staticmethod
    def config(thread_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": thread_id}}

    def test_report_is_generated_once_at_finish_with_terminal_inputs(self) -> None:
        recorder = ReportGeneratorRecorder()
        graph = self.build_graph(recorder)
        config = self.config("report-once")

        first_result = graph.invoke(self.make_initial_state(), config)
        self.assertIn("__interrupt__", first_result)
        self.assertEqual(recorder.calls, [])

        final_result = graph.invoke(Command(resume="我的回答"), config)

        self.assertNotIn("__interrupt__", final_result)
        self.assertEqual(len(recorder.calls), 1)
        call = recorder.calls[0]
        self.assertEqual(call["plan"], self.make_plan())
        self.assertTrue(call["runtime_state"].stop_requested)
        self.assertTrue(call["runtime_state"].stop_reason)
        self.assertEqual(len(call["turns"]), 1)
        self.assertEqual(call["turns"][0].answer, "我的回答")
        self.assertEqual(len(call["evidences"]), 1)
        self.assertEqual(call["evidences"][0].id, "evidence_a")
        self.assertEqual(call["claim_registry"], ClaimRegistry())
        self.assertEqual(call["target_role"], "目标岗位")

        state = graph.get_state(config).values
        self.assertIsInstance(state["next_action"], FinishAction)
        self.assertTrue(state["runtime_state"].stop_requested)
        self.assertEqual(state["assessment_report"], recorder.result)

    def test_invalid_generated_report_is_rejected_by_report_schema(self) -> None:
        recorder = ReportGeneratorRecorder(result={"target_role": "不完整"})
        graph = self.build_graph(recorder)
        config = self.config("invalid-report")

        graph.invoke(self.make_initial_state(), config)
        with self.assertRaises(ValidationError):
            graph.invoke(Command(resume="我的回答"), config)

        self.assertEqual(len(recorder.calls), 1)


if __name__ == "__main__":
    unittest.main()
