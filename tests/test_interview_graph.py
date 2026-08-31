from datetime import datetime, timezone
import unittest

from langgraph.types import Command

from profile_agent.graphs.interview import (
    _active_constraint_gap_tags,
    build_interview_graph,
)
from profile_agent.schemas.claim_schema import ClaimRegistry
from tests.report_test_helpers import make_test_report
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    AskAction,
    EvidenceRequirement,
    FinishAction,
    GeneratedQuestion,
    InterviewPlan,
)
from profile_agent.schemas.job_schema import JobProfile, JobRequirement
from profile_agent.schemas.report_schema import ScoringBlueprint
from profile_agent.schemas.resume_schema import ResumeProfile
from profile_agent.schemas.runtime_schema import (
    AnswerProcessingResult,
    Evidence,
    InterviewTurn,
)
from profile_agent.services.runtime_state_service import (
    record_requirement_evidence,
)


class InterviewGraphTest(unittest.TestCase):
    NOW = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.question_calls: list[AskAction] = []
        self.answer_calls: list[InterviewTurn] = []
        self.events: list[str] = []
        self.report_calls: list[dict] = []

    def test_missing_scenario_provenance_returns_empty_gap_allowlist(self) -> None:
        self.assertEqual(_active_constraint_gap_tags(None, None), ())
        self.assertEqual(_active_constraint_gap_tags(None, "legacy::module"), ())

        class UnusedCatalog:
            def resolve(self, retrieval_unit_id: str):
                raise AssertionError("resolve must not run for missing provenance")

        catalog = UnusedCatalog()
        self.assertEqual(_active_constraint_gap_tags(catalog, None), ())
        self.assertEqual(_active_constraint_gap_tags(catalog, ""), ())

    def test_unknown_scenario_provenance_fails_with_retrieval_unit_id(self) -> None:
        class BrokenCatalog:
            def resolve(self, retrieval_unit_id: str):
                raise KeyError(retrieval_unit_id)

        retrieval_unit_id = "unknown::module"
        with self.assertRaises(ValueError) as context:
            _active_constraint_gap_tags(BrokenCatalog(), retrieval_unit_id)

        self.assertIn(retrieval_unit_id, str(context.exception))
        self.assertIsInstance(context.exception.__cause__, KeyError)

    def make_plan(self) -> InterviewPlan:
        return InterviewPlan(
            duration_minutes=30,
            max_questions=4,
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
                            description="能够清晰分析问题并解释取舍",
                        )
                    ],
                    related_claim_ids=[],
                    priority="high",
                    must_cover=True,
                    time_budget_minutes=10,
                    preferred_modes=["scenario"],
                ),
                AssessmentTarget(
                    id="target_b",
                    objective="验证可选 B 能力",
                    target_type="knowledge",
                    competency_ids=[],
                    evidence_requirements=[
                        EvidenceRequirement(
                            id="requirement_b",
                            description="了解 B 基础知识",
                        )
                    ],
                    related_claim_ids=[],
                    priority="low",
                    must_cover=False,
                    time_budget_minutes=5,
                    preferred_modes=["foundation"],
                ),
            ],
        )

    def make_initial_state(self) -> dict:
        return {
            "assessment_id": "ast_001",
            "interview_plan": self.make_plan(),
            "claim_registry": ClaimRegistry(),
            "resume_profile": ResumeProfile(education=["本科：计算机科学与技术"]),
            "job_profile": JobProfile(
                role="AI 应用工程师",
                requirements=[
                    JobRequirement(name="Agent Workflow", description="状态与工具边界")
                ],
            ),
        }

    def question_generator(
        self,
        action: AskAction,
        plan: InterviewPlan,
        claim_registry: ClaimRegistry | None = None,
        recent_turns: list[InterviewTurn] | None = None,
    ) -> GeneratedQuestion:
        self.events.append("question_generator")
        self.question_calls.append(action)
        return GeneratedQuestion(text=f"问题 {len(self.question_calls)}")

    def answer_processor(
        self,
        plan: InterviewPlan,
        runtime_state,
        turn: InterviewTurn,
        existing_evidences: list[Evidence],
        claim_registry: ClaimRegistry | None = None,
    ) -> AnswerProcessingResult:
        self.events.append("answer_processor")
        self.answer_calls.append(turn.model_copy(deep=True))
        evidence_id = f"evidence_{len(existing_evidences) + 1:03d}"
        evidence = Evidence(
            id=evidence_id,
            turn_id=turn.id,
            requirement_ids=["requirement_a"],
            polarity="supporting",
            strength="strong" if turn.answer.startswith("strong") else "weak",
            observation="候选人给出了回答",
            source_excerpt=turn.answer,
        )
        status = "sufficient" if turn.answer.startswith("strong") else "in_progress"
        updated_runtime = record_requirement_evidence(
            runtime_state,
            requirement_id="requirement_a",
            status=status,
            supporting_evidence_ids=[evidence_id],
            contradicting_evidence_ids=[],
            known_evidence_ids={evidence_id},
        )
        return AnswerProcessingResult(
            new_evidences=[evidence],
            runtime_state=updated_runtime,
        )

    def build_graph(self):
        return build_interview_graph(
            question_generator=self.question_generator,
            answer_processor=self.answer_processor,
            report_generator=self.report_generator,
            now_provider=lambda: self.NOW,
        )

    def report_generator(self, **kwargs):
        self.report_calls.append(kwargs)
        return make_test_report(kwargs.get("target_role") or "测试岗位")

    def test_report_generator_receives_frozen_blueprint_from_initial_state(self) -> None:
        blueprint = ScoringBlueprint(
            role_family="ai_application_engineering",
            role_profile_version="2026-H2",
            bindings=[],
        )
        initial_state = self.make_initial_state()
        initial_state["scoring_blueprint"] = blueprint
        graph = self.build_graph()
        config = self.config("report-blueprint")

        graph.invoke(initial_state, config)
        graph.invoke(Command(resume="strong answer"), config)

        self.assertEqual(len(self.report_calls), 1)
        self.assertIn("scoring_blueprint", self.report_calls[0])
        self.assertEqual(
            self.report_calls[0]["scoring_blueprint"].model_dump(),
            blueprint.model_dump(),
        )

    def test_report_generator_receives_assessment_and_candidate_context(self) -> None:
        graph = self.build_graph()
        config = self.config("report-context")

        graph.invoke(self.make_initial_state(), config)
        graph.invoke(Command(resume="strong answer"), config)

        call = self.report_calls[0]
        self.assertEqual(call["candidate_id"], "ast_001")
        self.assertIn("本科", call["resume_profile"].education[0])
        self.assertTrue(call["job_profile"].requirements)

    @staticmethod
    def config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def interrupt_payload(result: dict) -> dict:
        interrupts = result.get("__interrupt__", [])
        if not interrupts:
            raise AssertionError(f"expected interrupt, got {result!r}")
        return interrupts[0].value

    def test_first_invoke_records_question_before_interrupt(self) -> None:
        graph = self.build_graph()
        config = self.config("first-invoke")

        result = graph.invoke(self.make_initial_state(), config)

        payload = self.interrupt_payload(result)
        self.assertEqual(payload["question"], "问题 1")
        self.assertEqual(payload["turn_id"], "turn_001")
        self.assertEqual(payload["action"]["action"], "ask")
        state = graph.get_state(config).values
        self.assertEqual(state["runtime_state"].question_count, 1)
        self.assertEqual(len(state["interview_turns"]), 1)
        self.assertEqual(state["interview_turns"][0].id, "turn_001")
        self.assertIsNone(state["interview_turns"][0].answer)
        self.assertEqual(len(self.question_calls), 1)
        self.assertEqual(len(self.answer_calls), 0)

    def test_default_retrieval_degrades_without_provider_and_keeps_trace_private(self) -> None:
        graph = self.build_graph()
        config = self.config("default-retrieval-unavailable")

        result = graph.invoke(self.make_initial_state(), config)
        payload = self.interrupt_payload(result)
        state = graph.get_state(config).values
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}

        self.assertIn(("supervisor", "prepare_question_context"), edges)
        self.assertIn(("prepare_question_context", "generate_question"), edges)
        self.assertEqual(
            state["interview_turns"][0].retrieval_trace.status,
            "unavailable",
        )
        self.assertNotIn("retrieval_trace", payload)

    def test_resume_answers_same_turn_and_processes_before_next_decision(self) -> None:
        graph = self.build_graph()
        config = self.config("follow-up")
        graph.invoke(self.make_initial_state(), config)

        result = graph.invoke(Command(resume="模糊回答"), config)

        payload = self.interrupt_payload(result)
        state = graph.get_state(config).values
        self.assertEqual(self.events, [
            "question_generator",
            "answer_processor",
            "question_generator",
        ])
        self.assertEqual(len(self.answer_calls), 1)
        self.assertEqual(self.answer_calls[0].id, "turn_001")
        self.assertEqual(self.answer_calls[0].answer, "模糊回答")
        self.assertEqual(state["interview_turns"][0].answer, "模糊回答")
        self.assertEqual(state["runtime_state"].question_count, 2)
        self.assertEqual(
            state["runtime_state"].requirement_progress["requirement_a"].status,
            "in_progress",
        )
        self.assertEqual(
            state["runtime_state"].requirement_progress["requirement_b"].status,
            "not_started",
        )
        self.assertEqual(payload["turn_id"], "turn_002")
        self.assertEqual(payload["action"]["target_id"], "target_a")
        self.assertEqual(payload["action"]["question_mode"], "follow_up")
        self.assertEqual(len(self.question_calls), 2)

    def test_same_thread_resumes_twice_and_finishes_without_third_question(self) -> None:
        graph = self.build_graph()
        config = self.config("two-resumes")

        first_result = graph.invoke(self.make_initial_state(), config)
        first_payload = self.interrupt_payload(first_result)
        first_state = graph.get_state(config).values
        self.assertEqual(first_payload["question"], "问题 1")
        self.assertEqual(first_payload["turn_id"], "turn_001")
        self.assertEqual(first_state["runtime_state"].question_count, 1)

        second_result = graph.invoke(Command(resume="模糊回答"), config)
        second_payload = self.interrupt_payload(second_result)
        second_state = graph.get_state(config).values
        self.assertEqual(second_payload["question"], "问题 2")
        self.assertEqual(second_payload["turn_id"], "turn_002")
        self.assertEqual(second_payload["action"]["question_mode"], "follow_up")
        self.assertEqual(second_state["runtime_state"].question_count, 2)
        self.assertEqual(len(self.question_calls), 2)

        final_result = graph.invoke(Command(resume="strong answer"), config)

        self.assertNotIn("__interrupt__", final_result)
        final_snapshot = graph.get_state(config)
        self.assertEqual(final_snapshot.next, ())
        state = final_snapshot.values
        self.assertEqual(self.events, [
            "question_generator",
            "answer_processor",
            "question_generator",
            "answer_processor",
        ])
        self.assertEqual(len(self.question_calls), 2)
        self.assertEqual(len(self.answer_calls), 2)
        self.assertEqual(
            [turn.id for turn in self.answer_calls],
            ["turn_001", "turn_002"],
        )
        self.assertEqual(
            [turn.answer for turn in self.answer_calls],
            ["模糊回答", "strong answer"],
        )
        self.assertEqual(len(state["interview_turns"]), 2)
        self.assertEqual(
            [turn.answer for turn in state["interview_turns"]],
            ["模糊回答", "strong answer"],
        )
        self.assertEqual(state["runtime_state"].question_count, 2)
        self.assertEqual(
            state["runtime_state"].requirement_progress["requirement_a"].status,
            "sufficient",
        )
        self.assertEqual(
            state["runtime_state"].requirement_progress["requirement_b"].status,
            "not_started",
        )
        self.assertTrue(state["runtime_state"].stop_requested)
        self.assertIsInstance(state["next_action"], FinishAction)
        self.assertEqual(state["next_action"].action, "finish")

    def test_strong_answer_finishes_and_leaves_optional_target_unstarted(self) -> None:
        graph = self.build_graph()
        config = self.config("strong-answer")
        graph.invoke(self.make_initial_state(), config)

        result = graph.invoke(Command(resume="strong answer"), config)

        self.assertNotIn("__interrupt__", result)
        state = graph.get_state(config).values
        self.assertIsInstance(state["next_action"], FinishAction)
        self.assertEqual(state["next_action"].action, "finish")
        self.assertTrue(state["runtime_state"].stop_requested)
        self.assertTrue(state["runtime_state"].stop_reason)
        self.assertEqual(
            state["runtime_state"].requirement_progress["requirement_a"].status,
            "sufficient",
        )
        self.assertEqual(
            state["runtime_state"].requirement_progress["requirement_b"].status,
            "not_started",
        )
        self.assertEqual(len(self.question_calls), 1)
        self.assertEqual(len(self.answer_calls), 1)

    def test_thread_ids_isolate_runtime_and_turns(self) -> None:
        graph = self.build_graph()
        config_a = self.config("thread-a")
        config_b = self.config("thread-b")

        graph.invoke(self.make_initial_state(), config_a)
        graph.invoke(self.make_initial_state(), config_b)
        graph.invoke(Command(resume="模糊回答"), config_a)

        state_a = graph.get_state(config_a).values
        state_b = graph.get_state(config_b).values
        self.assertEqual(state_a["runtime_state"].question_count, 2)
        self.assertEqual(state_a["interview_turns"][0].answer, "模糊回答")
        self.assertEqual(state_b["runtime_state"].question_count, 1)
        self.assertEqual(state_b["interview_turns"][0].id, "turn_001")
        self.assertIsNone(state_b["interview_turns"][0].answer)

    def test_blank_resume_answer_is_rejected_without_processing(self) -> None:
        graph = self.build_graph()
        config = self.config("blank-answer")
        graph.invoke(self.make_initial_state(), config)

        with self.assertRaisesRegex(ValueError, "回答不能为空"):
            graph.invoke(Command(resume="   "), config)

        state = graph.get_state(config).values
        self.assertEqual(state["runtime_state"].question_count, 1)
        self.assertIsNone(state["interview_turns"][0].answer)
        self.assertEqual(len(self.question_calls), 1)
        self.assertEqual(len(self.answer_calls), 0)


if __name__ == "__main__":
    unittest.main()
