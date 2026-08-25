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
from profile_agent.schemas.job_schema import JobProfile, JobRequirement
from profile_agent.schemas.report_schema import (
    JobMatchResult,
    RequirementScoringBinding,
    RubricMatchBatch,
    ScoreSnapshot,
    ScoringBlueprint,
)
from profile_agent.schemas.resume_schema import ResumeProfile
from profile_agent.schemas.runtime_schema import (
    AnswerProcessingResult,
    Evidence,
    InterviewTurn,
)
from profile_agent.services.runtime_state_service import record_requirement_evidence
from profile_agent.services.assessment_report_service import (
    generate_assessment_report,
)
from profile_agent.services.report_writer_service import fallback_enterprise_copy
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
        candidate_id="未提供",
        resume_profile=None,
        job_profile=None,
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
                "candidate_id": candidate_id,
                "resume_profile": resume_profile,
                "job_profile": job_profile,
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
            "assessment_id": "ast_001",
            "interview_plan": self.make_plan(),
            "claim_registry": ClaimRegistry(),
            "target_role": "目标岗位",
            "resume_profile": ResumeProfile(education=["本科：计算机科学与技术"]),
            "job_profile": JobProfile(
                role="目标岗位",
                requirements=[
                    JobRequirement(name="Agent Workflow", description="状态与工具边界")
                ],
            ),
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
        self.assertEqual(call["candidate_id"], "ast_001")
        self.assertIn("本科", call["resume_profile"].education[0])
        self.assertTrue(call["job_profile"].requirements)

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

    def test_terminal_graph_assembles_real_enterprise_report_without_llm(self) -> None:
        writer_calls: list[tuple[object, object, object, object]] = []

        def deterministic_rubric_matcher(
            plan,
            blueprint,
            role_profile,
            turns,
            evidences,
        ) -> RubricMatchBatch:
            return RubricMatchBatch(matches=[])

        def deterministic_score_engine(
            profile,
            blueprint,
            assessments,
            claim_verifications,
        ) -> ScoreSnapshot:
            return ScoreSnapshot(
                role_family=profile.role_family,
                role_profile_version=profile.version,
                scoring_engine_version="integration-test",
                job_match=JobMatchResult(
                    published=False,
                    coverage=0.0,
                    confidence="low",
                ),
            )

        def deterministic_writer(
            snapshot,
            profile,
            evidences,
            selected_dimension_ids,
        ):
            writer_calls.append(
                (snapshot, profile, evidences, selected_dimension_ids)
            )
            return fallback_enterprise_copy(
                snapshot,
                profile,
                selected_dimension_ids,
                evidence=evidences,
            )

        def real_report_generator(**kwargs):
            return generate_assessment_report(
                **kwargs,
                semantic_services={
                    "rubric_matcher": deterministic_rubric_matcher,
                    "score_engine": deterministic_score_engine,
                    "narrative_writer": deterministic_writer,
                },
            )

        initial_state = self.make_initial_state()
        initial_state["scoring_blueprint"] = ScoringBlueprint(
            role_family="ai_application_engineering",
            role_profile_version="2026-H2",
            bindings=[
                RequirementScoringBinding(
                    requirement_id="requirement_a",
                    primary_dimension_id="role_dim_01",
                    weight_within_dimension=1.0,
                    rubric_id="role_dim_01",
                )
            ],
        )
        graph = self.build_graph(real_report_generator)
        config = self.config("real-enterprise-report")

        graph.invoke(initial_state, config)
        result = graph.invoke(Command(resume="我的回答"), config)

        self.assertNotIn("__interrupt__", result)
        report = graph.get_state(config).values["assessment_report"]
        self.assertEqual(report.candidate_overview.candidate_id, "ast_001")
        self.assertIn("本科", report.candidate_overview.education_summary)
        self.assertTrue(report.candidate_overview.jd_focus)
        self.assertEqual(report.candidate_overview.interview_rounds, 1)
        self.assertEqual(
            report.enterprise_assessment.decision,
            "INSUFFICIENT_EVIDENCE",
        )
        self.assertEqual(len(writer_calls), 1)
        self.assertEqual(writer_calls[0][2][0].id, "evidence_a")


if __name__ == "__main__":
    unittest.main()
