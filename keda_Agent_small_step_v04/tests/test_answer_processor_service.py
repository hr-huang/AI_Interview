from datetime import datetime, timezone
import unittest

from profile_agent.schemas.claim_schema import ClaimItem, ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
    InterviewPlan,
)
from profile_agent.schemas.runtime_schema import (
    Evidence,
    EvidenceDraft,
    InterviewRuntimeState,
    InterviewTurn,
    RequirementAssessment,
    TurnAssessment,
)
from profile_agent.services.answer_processor_service import process_answer
from profile_agent.services.runtime_state_service import initialize_runtime_state


REQ_01 = "target_01_req_01"
REQ_02 = "target_01_req_02"


class FakeLLM:
    def __init__(self, assessment: TurnAssessment) -> None:
        self.assessment = assessment
        self.calls: list[tuple[list[tuple[str, str]], type[object]]] = []

    def structured(self, messages, schema):
        self.calls.append((messages, schema))
        return self.assessment


class AnswerProcessorServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = InterviewPlan(
            duration_minutes=30,
            max_questions=10,
            closing_buffer_minutes=2,
            targets=[
                AssessmentTarget(
                    id="target_01",
                    objective="验证 Agent Workflow 设计能力",
                    target_type="implementation",
                    competency_ids=["competency_01"],
                    evidence_requirements=[
                        EvidenceRequirement(
                            id=REQ_01,
                            description="能够解释 Workflow 数据流",
                        ),
                        EvidenceRequirement(
                            id=REQ_02,
                            description="能够解释 State 设计",
                        ),
                    ],
                    related_claim_ids=["claim_01"],
                    priority="high",
                    must_cover=True,
                    time_budget_minutes=10,
                    preferred_modes=["project_deep_dive", "scenario"],
                )
            ],
        )
        self.runtime = initialize_runtime_state(self.plan)
        self.turn = InterviewTurn(
            id="turn_01",
            sequence_number=1,
            target_id="target_01",
            primary_requirement_id=REQ_01,
            question_mode="scenario",
            question="请说明你如何设计 Workflow 的状态流转。",
            answer="我会先定义状态，再分别处理并行节点的输入和输出。",
            asked_at=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
            answered_at=datetime(2026, 8, 21, 12, 1, tzinfo=timezone.utc),
        )
        self.claim_registry = ClaimRegistry(
            claims=[
                ClaimItem(
                    id="claim_01",
                    text="实现过 Agent Workflow",
                    source_section="project",
                    claim_type="experience",
                )
            ]
        )

    def assessment(
        self,
        *,
        drafts: list[EvidenceDraft],
        requirements: list[RequirementAssessment],
        relevance: str = "high",
    ) -> TurnAssessment:
        return TurnAssessment(
            answer_relevance=relevance,
            evidence_drafts=drafts,
            requirement_assessments=requirements,
        )

    def draft(
        self,
        *,
        requirements: list[str],
        polarity: str = "supporting",
        strength: str = "strong",
        claims: list[str] | None = None,
    ) -> EvidenceDraft:
        return EvidenceDraft(
            requirement_ids=requirements,
            related_claim_ids=claims or [],
            polarity=polarity,
            strength=strength,
            observation="回答给出了可核验的设计事实。",
            source_excerpt="我会先定义状态，再处理并行节点。",
        )

    def requirement(
        self,
        requirement_id: str,
        status: str = "sufficient",
    ) -> RequirementAssessment:
        return RequirementAssessment(
            requirement_id=requirement_id,
            recommended_status=status,
            rationale="回答提供了充分证据。",
        )

    def test_success_calls_structured_once_and_updates_runtime(self) -> None:
        fake_llm = FakeLLM(
            self.assessment(
                drafts=[self.draft(requirements=[REQ_01], claims=["claim_01"])],
                requirements=[self.requirement(REQ_01)],
            )
        )

        result = process_answer(
            self.plan,
            self.runtime,
            self.turn,
            [],
            claim_registry=self.claim_registry,
            llm_client=fake_llm,
        )

        self.assertEqual(len(fake_llm.calls), 1)
        self.assertIs(fake_llm.calls[0][1], TurnAssessment)
        self.assertEqual([item.id for item in result.new_evidences], ["evidence_001"])
        self.assertEqual(result.new_evidences[0].turn_id, self.turn.id)
        progress = result.runtime_state.requirement_progress[REQ_01]
        self.assertEqual(progress.status, "sufficient")
        self.assertEqual(progress.supporting_evidence_ids, ["evidence_001"])

    def test_prompt_pins_turn_assessment_field_names_and_enums(self) -> None:
        fake_llm = FakeLLM(
            self.assessment(
                drafts=[self.draft(requirements=[REQ_01])],
                requirements=[self.requirement(REQ_01)],
            )
        )

        process_answer(
            self.plan,
            self.runtime,
            self.turn,
            [],
            llm_client=fake_llm,
        )

        prompt = "\n".join(content for _, content in fake_llm.calls[0][0])
        self.assertIn('根对象必须严格包含 answer_relevance、evidence_drafts、requirement_assessments', prompt)
        self.assertIn('EvidenceDraft 字段只能是 requirement_ids、related_claim_ids、polarity、strength、observation、source_excerpt', prompt)
        self.assertIn('RequirementAssessment 字段只能是 requirement_id、recommended_status、rationale', prompt)
        self.assertIn('strength 只能是 weak、medium、strong', prompt)
        self.assertIn('不要生成 evidence_id、content、status、coverage_notes、overall_notes', prompt)
        self.assertIn('无法归属到任何 requirement 时，evidence_drafts 和 requirement_assessments 都返回空数组', prompt)
        self.assertIn('recommended_status 必须使用 contradictory，绝不能使用 contradicting', prompt)
        self.assertIn('source_excerpt 必须逐字复制回答中的一段连续原文', prompt)
        self.assertIn('禁止使用省略号、改写或拼接', prompt)

    def test_one_evidence_can_update_multiple_requirements(self) -> None:
        fake_llm = FakeLLM(
            self.assessment(
                drafts=[self.draft(requirements=[REQ_01, REQ_02])],
                requirements=[
                    self.requirement(REQ_01),
                    self.requirement(REQ_02, status="in_progress"),
                ],
            )
        )

        result = process_answer(
            self.plan,
            self.runtime,
            self.turn,
            [],
            llm_client=fake_llm,
        )

        self.assertEqual(len(result.new_evidences), 1)
        self.assertEqual(result.new_evidences[0].requirement_ids, [REQ_01, REQ_02])
        self.assertEqual(
            result.runtime_state.requirement_progress[REQ_01].supporting_evidence_ids,
            ["evidence_001"],
        )
        self.assertEqual(
            result.runtime_state.requirement_progress[REQ_02].supporting_evidence_ids,
            ["evidence_001"],
        )

    def test_strong_negative_evidence_can_have_sufficient_assessment(self) -> None:
        fake_llm = FakeLLM(
            self.assessment(
                drafts=[
                    self.draft(
                        requirements=[REQ_01],
                        polarity="contradicting",
                        strength="strong",
                    )
                ],
                requirements=[self.requirement(REQ_01, status="sufficient")],
            )
        )

        result = process_answer(
            self.plan,
            self.runtime,
            self.turn,
            [],
            llm_client=fake_llm,
        )

        progress = result.runtime_state.requirement_progress[REQ_01]
        self.assertEqual(progress.status, "sufficient")
        self.assertEqual(progress.contradicting_evidence_ids, ["evidence_001"])

    def test_invalid_requirement_reference_is_rejected(self) -> None:
        fake_llm = FakeLLM(
            self.assessment(
                drafts=[self.draft(requirements=["target_99_req_01"])],
                requirements=[],
            )
        )

        with self.assertRaisesRegex(ValueError, "requirement"):
            process_answer(
                self.plan,
                self.runtime,
                self.turn,
                [],
                llm_client=fake_llm,
            )

        self.assertEqual(len(fake_llm.calls), 1)

    def test_unknown_claim_reference_is_rejected(self) -> None:
        fake_llm = FakeLLM(
            self.assessment(
                drafts=[self.draft(requirements=[REQ_01], claims=["claim_99"])],
                requirements=[self.requirement(REQ_01)],
            )
        )

        with self.assertRaisesRegex(ValueError, "claim"):
            process_answer(
                self.plan,
                self.runtime,
                self.turn,
                [],
                claim_registry=self.claim_registry,
                llm_client=fake_llm,
            )

    def test_duplicate_requirement_assessment_is_rejected(self) -> None:
        fake_llm = FakeLLM(
            self.assessment(
                drafts=[self.draft(requirements=[REQ_01])],
                requirements=[self.requirement(REQ_01), self.requirement(REQ_01)],
            )
        )

        with self.assertRaisesRegex(ValueError, "重复"):
            process_answer(
                self.plan,
                self.runtime,
                self.turn,
                [],
                llm_client=fake_llm,
            )

    def test_assessment_without_current_turn_evidence_is_rejected(self) -> None:
        fake_llm = FakeLLM(
            self.assessment(
                drafts=[self.draft(requirements=[REQ_02])],
                requirements=[self.requirement(REQ_01)],
            )
        )

        with self.assertRaisesRegex(ValueError, "evidence"):
            process_answer(
                self.plan,
                self.runtime,
                self.turn,
                [],
                llm_client=fake_llm,
            )

    def test_empty_or_unanswered_turn_is_rejected_before_model_call(self) -> None:
        for answer in (None, "   "):
            with self.subTest(answer=answer):
                turn = self.turn.model_copy(update={"answer": answer})
                fake_llm = FakeLLM(
                    self.assessment(drafts=[], requirements=[])
                )

                with self.assertRaisesRegex(ValueError, "回答"):
                    process_answer(
                        self.plan,
                        self.runtime,
                        turn,
                        [],
                        llm_client=fake_llm,
                    )

                self.assertEqual(fake_llm.calls, [])

    def test_evidence_ids_continue_after_existing_maximum(self) -> None:
        existing = [
            Evidence(
                id="evidence_002",
                turn_id="turn_00",
                requirement_ids=[REQ_01],
                polarity="supporting",
                strength="medium",
                observation="已有事实。",
                source_excerpt="此前回答。",
            ),
            Evidence(
                id="evidence_009",
                turn_id="turn_00",
                requirement_ids=[REQ_02],
                polarity="supporting",
                strength="weak",
                observation="已有另一事实。",
                source_excerpt="此前另一回答。",
            ),
        ]
        fake_llm = FakeLLM(
            self.assessment(
                drafts=[
                    self.draft(requirements=[REQ_01]),
                    self.draft(requirements=[REQ_02]),
                ],
                requirements=[
                    self.requirement(REQ_01),
                    self.requirement(REQ_02),
                ],
            )
        )

        result = process_answer(
            self.plan,
            self.runtime,
            self.turn,
            existing,
            llm_client=fake_llm,
        )

        self.assertEqual(
            [evidence.id for evidence in result.new_evidences],
            ["evidence_010", "evidence_011"],
        )

    def test_runtime_and_existing_evidence_list_are_not_mutated(self) -> None:
        existing = [
            Evidence(
                id="evidence_004",
                turn_id="turn_00",
                requirement_ids=[REQ_01],
                polarity="supporting",
                strength="medium",
                observation="已有事实。",
                source_excerpt="此前回答。",
            )
        ]
        runtime_before = self.runtime.model_dump()
        existing_before = [item.model_dump() for item in existing]
        fake_llm = FakeLLM(
            self.assessment(
                drafts=[self.draft(requirements=[REQ_01])],
                requirements=[self.requirement(REQ_01)],
            )
        )

        result = process_answer(
            self.plan,
            self.runtime,
            self.turn,
            existing,
            llm_client=fake_llm,
        )

        self.assertEqual(self.runtime.model_dump(), runtime_before)
        self.assertEqual([item.model_dump() for item in existing], existing_before)
        self.assertIsNot(result.runtime_state, self.runtime)
        self.assertIsNot(
            result.runtime_state.requirement_progress[REQ_01],
            self.runtime.requirement_progress[REQ_01],
        )
        self.assertEqual(len(existing), 1)


if __name__ == "__main__":
    unittest.main()
