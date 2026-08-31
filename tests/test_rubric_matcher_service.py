from datetime import datetime, timezone
import unittest

from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
    InterviewPlan,
)
from profile_agent.schemas.report_schema import (
    CompetencyDimensionRubric,
    RequirementScoringBinding,
    RoleCompetencyProfile,
    RubricCriterion,
    RubricMatch,
    RubricMatchBatch,
    RubricQuality,
    ScoringBlueprint,
)
from profile_agent.schemas.runtime_schema import Evidence, InterviewTurn
from profile_agent.services.rubric_matcher_service import (
    RubricMatchValidationError,
    match_evidence_to_rubric,
)


class FakeLLM:
    def __init__(self, response: RubricMatchBatch) -> None:
        self.response = response
        self.calls: list[tuple[list[tuple[str, str]], type[object]]] = []

    def structured(self, messages, schema):
        self.calls.append((messages, schema))
        return self.response


class SequencedFakeLLM:
    def __init__(self, responses: list[RubricMatchBatch]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[list[tuple[str, str]], type[object]]] = []

    def structured(self, messages, schema):
        self.calls.append((messages, schema))
        return next(self.responses)


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=10,
        closing_buffer_minutes=2,
        targets=[
            AssessmentTarget(
                id="target_01",
                objective="验证 Agent Workflow 的设计与边界意识",
                target_type="system_design",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id="req_01",
                        description="能够拆分状态、节点和工具边界",
                    ),
                    EvidenceRequirement(
                        id="req_02",
                        description="能够说明失败恢复与人工介入",
                    ),
                ],
                related_claim_ids=[],
                priority="high",
                must_cover=True,
                time_budget_minutes=10,
                preferred_modes=["system_design", "follow_up"],
            )
        ],
    )


def make_role_profile() -> RoleCompetencyProfile:
    return RoleCompetencyProfile(
        role_family="ai_application_engineering",
        display_name="AI Agent / AI应用工程师",
        version="2026-H2",
        valid_from=datetime(2026, 7, 1, tzinfo=timezone.utc).date(),
        knowledge_as_of=datetime(2026, 8, 21, tzinfo=timezone.utc).date(),
        dimensions=[
            CompetencyDimensionRubric(
                id="role_dim_01",
                name="AI应用与Agent编排",
                weight=1.0,
                is_gating=True,
                minimum_criteria=[
                    RubricCriterion(
                        id="min_01",
                        text="拆分状态、节点与工具边界",
                    ),
                    RubricCriterion(
                        id="min_02",
                        text="说明状态流转与人工介入",
                    ),
                ],
                excellence_signals=[
                    RubricCriterion(
                        id="exc_01",
                        text="比较单 Agent、Workflow 与多 Agent",
                        score_adjustment=2,
                    )
                ],
                critical_errors=[
                    RubricCriterion(
                        id="err_01",
                        text="无差别拆成多 Agent",
                        score_adjustment=-5,
                    )
                ],
                accepted_alternatives=[
                    RubricCriterion(
                        id="alt_01",
                        text="用等价的状态机方案满足边界要求",
                    )
                ],
            )
        ],
        source_refs=["fixture"],
    )


def make_blueprint() -> ScoringBlueprint:
    return ScoringBlueprint(
        role_family="ai_application_engineering",
        role_profile_version="2026-H2",
        bindings=[
            RequirementScoringBinding(
                requirement_id="req_01",
                primary_dimension_id="role_dim_01",
                weight_within_dimension=0.5,
                rubric_id="role_dim_01",
            ),
            RequirementScoringBinding(
                requirement_id="req_02",
                primary_dimension_id="role_dim_01",
                weight_within_dimension=0.5,
                rubric_id="role_dim_01",
            ),
        ],
    )


def make_turns() -> list[InterviewTurn]:
    return [
        InterviewTurn(
            id="turn_01",
            sequence_number=1,
            target_id="target_01",
            primary_requirement_id="req_01",
            question_mode="system_design",
            question="请设计一个 Agent Workflow。",
            answer="我会先定义状态和工具边界。",
            asked_at=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
        ),
        InterviewTurn(
            id="turn_02",
            sequence_number=2,
            target_id="target_01",
            primary_requirement_id="req_02",
            question_mode="follow_up",
            question="失败时如何恢复？",
            answer="我会设计重试与人工介入。",
            asked_at=datetime(2026, 8, 21, 12, 1, tzinfo=timezone.utc),
        ),
    ]


def make_evidence(
    evidence_id: str = "ev_01",
    *,
    requirement_ids: list[str] | None = None,
    turn_id: str = "turn_01",
    polarity: str = "supporting",
    observation: str = "候选人明确说明了状态与工具边界。",
) -> Evidence:
    return Evidence(
        id=evidence_id,
        turn_id=turn_id,
        requirement_ids=requirement_ids or ["req_01"],
        polarity=polarity,
        strength="strong",
        observation=observation,
        source_excerpt="我会先定义状态，再约束工具边界。",
    )


def make_quality() -> RubricQuality:
    return RubricQuality(
        correctness="strong",
        specificity="medium",
        reasoning="strong",
        tradeoff_awareness="medium",
        transferability="unverified",
    )


def make_match(
    evidence_id: str = "ev_01",
    requirement_id: str = "req_01",
    **overrides,
) -> RubricMatch:
    values = {
        "evidence_id": evidence_id,
        "requirement_id": requirement_id,
        "matched_minimum_criteria": ["min_01"],
        "matched_excellence_signals": [],
        "matched_critical_errors": [],
        "accepted_alternative_ids": [],
        "quality": make_quality(),
    }
    values.update(overrides)
    return RubricMatch(**values)


def make_inputs() -> tuple[
    InterviewPlan,
    ScoringBlueprint,
    RoleCompetencyProfile,
    list[InterviewTurn],
    list[Evidence],
]:
    return (
        make_plan(),
        make_blueprint(),
        make_role_profile(),
        make_turns(),
        [make_evidence()],
    )


class RubricMatcherServiceTest(unittest.TestCase):
    def call_service(
        self,
        response: RubricMatchBatch,
        *,
        evidence: list[Evidence] | None = None,
    ):
        plan, blueprint, role_profile, turns, default_evidence = make_inputs()
        fake_llm = FakeLLM(response)
        result = match_evidence_to_rubric(
            plan,
            blueprint,
            role_profile,
            turns,
            evidence if evidence is not None else default_evidence,
            fake_llm,
        )
        return result, fake_llm

    def test_one_structured_call_returns_validated_matches(self) -> None:
        result, fake_llm = self.call_service(
            RubricMatchBatch(matches=[make_match()])
        )

        self.assertEqual(len(fake_llm.calls), 1)
        self.assertIs(fake_llm.calls[0][1], RubricMatchBatch)
        self.assertEqual(
            result.model_dump(),
            RubricMatchBatch(matches=[make_match()]).model_dump(),
        )

    def test_prompt_pins_aggregated_match_shape_for_mimo(self) -> None:
        _, fake_llm = self.call_service(
            RubricMatchBatch(matches=[make_match()])
        )

        messages, _ = fake_llm.calls[0]
        prompt = "\n".join(content for _, content in messages)
        self.assertIn("同一 Evidence 与 Requirement 只能输出一条聚合记录", prompt)
        self.assertIn('"matched_minimum_criteria"', prompt)
        self.assertIn('"quality"', prompt)
        self.assertIn("不要输出 rubric_element_id 或 element_type", prompt)

    def test_prompt_defines_strong_quality_without_exhaustive_answer(self) -> None:
        _, fake_llm = self.call_service(
            RubricMatchBatch(matches=[make_match()])
        )

        messages, _ = fake_llm.calls[0]
        prompt = "\n".join(content for _, content in messages)
        self.assertIn("strong 不要求穷举全部参考点", prompt)
        self.assertIn("明确说明为什么这样设计", prompt)
        self.assertIn("明确比较收益、代价或风险", prompt)

    def test_unknown_evidence_id_is_rejected(self) -> None:
        with self.assertRaises(RubricMatchValidationError):
            self.call_service(
                RubricMatchBatch(matches=[make_match(evidence_id="ev_missing")])
            )

    def test_invalid_match_ids_are_retried_once_with_exact_reason(self) -> None:
        plan, blueprint, role_profile, turns, evidence = make_inputs()
        fake_llm = SequencedFakeLLM(
            [
                RubricMatchBatch(
                    matches=[make_match(evidence_id="turn_01")]
                ),
                RubricMatchBatch(matches=[make_match()]),
            ]
        )

        result = match_evidence_to_rubric(
            plan,
            blueprint,
            role_profile,
            turns,
            evidence,
            fake_llm,
        )

        self.assertEqual(len(fake_llm.calls), 2)
        self.assertIn("不存在的 evidence_id", fake_llm.calls[1][0][-1][1])
        self.assertEqual(result.matches[0].evidence_id, "ev_01")

    def test_unknown_requirement_id_is_rejected(self) -> None:
        with self.assertRaises(RubricMatchValidationError):
            self.call_service(
                RubricMatchBatch(matches=[make_match(requirement_id="req_missing")])
            )

    def test_evidence_can_only_match_its_requirement_ids(self) -> None:
        with self.assertRaises(RubricMatchValidationError):
            self.call_service(
                RubricMatchBatch(matches=[make_match(requirement_id="req_02")])
            )

    def test_unknown_criterion_signal_error_or_alternative_is_rejected(self) -> None:
        invalid_fields = (
            ("matched_minimum_criteria", ["criterion_missing"]),
            ("matched_excellence_signals", ["signal_missing"]),
            ("matched_critical_errors", ["error_missing"]),
            ("accepted_alternative_ids", ["alternative_missing"]),
        )

        for field_name, value in invalid_fields:
            with self.subTest(field_name=field_name):
                with self.assertRaises(RubricMatchValidationError):
                    self.call_service(
                        RubricMatchBatch(
                            matches=[make_match(**{field_name: value})]
                        )
                    )

    def test_duplicate_evidence_requirement_pair_is_rejected(self) -> None:
        with self.assertRaises(RubricMatchValidationError):
            self.call_service(
                RubricMatchBatch(
                    matches=[make_match(), make_match()]
                )
            )

    def test_omission_cannot_be_returned_as_critical_error(self) -> None:
        omission = make_evidence(
            observation="回答只描述了基本流程，没有提到人工介入。"
        )

        with self.assertRaises(RubricMatchValidationError):
            self.call_service(
                RubricMatchBatch(
                    matches=[
                        make_match(matched_critical_errors=["err_01"])
                    ]
                ),
                evidence=[omission],
            )

    def test_unmatched_evidence_is_allowed_and_does_not_score(self) -> None:
        evidence = [
            make_evidence(),
            make_evidence(
                "ev_02",
                requirement_ids=["req_02"],
                turn_id="turn_02",
            ),
        ]

        result, _ = self.call_service(
            RubricMatchBatch(matches=[make_match()]),
            evidence=evidence,
        )

        self.assertEqual([match.evidence_id for match in result.matches], ["ev_01"])
        self.assertNotIn("ev_02", {match.evidence_id for match in result.matches})

    def test_inputs_are_not_mutated(self) -> None:
        plan, blueprint, role_profile, turns, evidence = make_inputs()
        snapshots = [
            plan.model_dump(),
            blueprint.model_dump(),
            role_profile.model_dump(),
            [turn.model_dump() for turn in turns],
            [item.model_dump() for item in evidence],
        ]
        fake_llm = FakeLLM(RubricMatchBatch(matches=[make_match()]))

        match_evidence_to_rubric(
            plan,
            blueprint,
            role_profile,
            turns,
            evidence,
            fake_llm,
        )

        self.assertEqual(plan.model_dump(), snapshots[0])
        self.assertEqual(blueprint.model_dump(), snapshots[1])
        self.assertEqual(role_profile.model_dump(), snapshots[2])
        self.assertEqual([turn.model_dump() for turn in turns], snapshots[3])
        self.assertEqual([item.model_dump() for item in evidence], snapshots[4])


if __name__ == "__main__":
    unittest.main()
