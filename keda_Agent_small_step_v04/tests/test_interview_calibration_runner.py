from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from profile_agent.calibration.interview_runner import (
    InterviewCalibrationRunnerError,
    run_interview_calibration_case,
)
from profile_agent.calibration.schemas import (
    InterviewCalibrationCase,
    InterviewPathExpectation,
    ScriptedAnswerRule,
)
from profile_agent.graphs.interview import build_interview_graph
from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
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


NOW = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)


def _plan() -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=1,
        closing_buffer_minutes=0,
        targets=[
            AssessmentTarget(
                id="target_01",
                objective="验证迁移能力",
                target_type="problem_solving",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id="target_01_req_01",
                        description="能够把 Workflow 迁移到新场景",
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


def _case() -> InterviewCalibrationCase:
    return InterviewCalibrationCase(
        id="T01",
        title="runner integration",
        resume_text="候选人有 Workflow 项目。",
        jd_text="岗位要求迁移到新场景。",
        target_role="AI Agent / AI 应用工程师",
        answer_rules=[
            ScriptedAnswerRule(
                id="T01_transfer",
                match_any=["迁移", "新场景"],
                answer="我会重新验证约束后再迁移。",
            )
        ],
        path_expectation=InterviewPathExpectation(
            required_topics={"transfer": ["迁移", "新场景"]},
            job_match_published=False,
            max_questions=1,
        ),
    )


def _answer_processor(
    plan: InterviewPlan,
    runtime_state,
    turn: InterviewTurn,
    existing_evidences: list[Evidence],
    claim_registry: ClaimRegistry | None = None,
) -> AnswerProcessingResult:
    evidence = Evidence(
        id="evidence_001",
        turn_id=turn.id,
        requirement_ids=[turn.primary_requirement_id],
        polarity="supporting",
        strength="strong",
        observation="候选人回答了迁移问题。",
        source_excerpt=turn.answer or "",
    )
    runtime = record_requirement_evidence(
        runtime_state,
        requirement_id=turn.primary_requirement_id,
        status="sufficient",
        supporting_evidence_ids=[evidence.id],
        contradicting_evidence_ids=[],
        known_evidence_ids={evidence.id},
    )
    return AnswerProcessingResult(new_evidences=[evidence], runtime_state=runtime)


class InterviewCalibrationRunnerTest(unittest.TestCase):
    def test_drives_real_graph_to_report_with_frozen_answer(self) -> None:
        pre_inputs: list[dict[str, object]] = []

        def pre_interview_runner(state):
            pre_inputs.append(dict(state))
            return {
                **state,
                "interview_plan": _plan(),
                "claim_registry": ClaimRegistry(),
            }

        def graph_builder():
            return build_interview_graph(
                question_generator=lambda **_kwargs: GeneratedQuestion(
                    text="请说明如何迁移到新场景。"
                ),
                answer_processor=_answer_processor,
                report_generator=lambda **kwargs: make_test_report(
                    kwargs["target_role"]
                ),
                now_provider=lambda: NOW,
            )

        run = run_interview_calibration_case(
            _case(),
            run_number=1,
            pre_interview_runner=pre_interview_runner,
            graph_builder=graph_builder,
        )

        self.assertTrue(run.passed)
        self.assertEqual(run.selected_rule_ids, ["T01_transfer"])
        self.assertIn("assessment_report", run.final_state)
        self.assertEqual(run.final_state["interview_turns"][0].answer, "我会重新验证约束后再迁移。")
        self.assertEqual(
            pre_inputs,
            [
                {
                    "resume_text": _case().resume_text,
                    "jd_text": _case().jd_text,
                    "target_role": _case().target_role,
                }
            ],
        )
        assertion_codes = {assertion.code for assertion in run.assertions}
        self.assertIn("required_topic:transfer", assertion_codes)
        self.assertIn("evidence_refs", assertion_codes)

    def test_defensive_resume_ceiling_stops_non_terminating_graph(self) -> None:
        payload = {
            "question": "请继续。",
            "action": {
                "action": "ask",
                "target_id": "target_01",
                "primary_requirement_id": "target_01_req_01",
                "question_mode": "follow_up",
                "reason": "继续验证迁移",
            },
        }

        class LoopGraph:
            def invoke(self, _state, _config):
                return {"__interrupt__": [SimpleNamespace(value=payload)]}

        with self.assertRaisesRegex(InterviewCalibrationRunnerError, "上限"):
            run_interview_calibration_case(
                _case(),
                pre_interview_runner=lambda state: {
                    **state,
                    "interview_plan": _plan(),
                    "claim_registry": ClaimRegistry(),
                },
                graph_builder=LoopGraph,
                answer_selector=lambda **_kwargs: ("固定回答", "T01_transfer"),
            )


if __name__ == "__main__":
    unittest.main()
