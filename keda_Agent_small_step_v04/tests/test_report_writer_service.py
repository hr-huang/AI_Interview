from datetime import date
import unittest

from profile_agent.schemas.report_schema import (
    CompetencyDimensionRubric,
    DevelopmentAction,
    JobMatchResult,
    NarrativeItem,
    RadarDimensionResult,
    ReportNarrativeDraft,
    RequirementEvidenceAssessment,
    RequirementScore,
    ReinterviewFocus,
    RoleCompetencyProfile,
    RubricCriterion,
    RubricQuality,
    ScoreReason,
    ScoreSnapshot,
)
from profile_agent.schemas.runtime_schema import Evidence
from profile_agent.services.report_writer_service import (
    EnterpriseCopyDraft,
    GroundingValidationError,
    fallback_report_narrative,
    fallback_enterprise_copy,
    write_enterprise_copy,
    write_report_narrative,
)
from profile_agent.services.role_profile_service import load_role_profile


class FakeLLM:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[tuple[list[tuple[str, str]], type]] = []

    def structured(self, messages: list[tuple[str, str]], schema: type):
        self.calls.append((messages, schema))
        return self.response


def make_role_profile() -> RoleCompetencyProfile:
    def criterion(criterion_id: str, text: str) -> RubricCriterion:
        return RubricCriterion(id=criterion_id, text=text)

    return RoleCompetencyProfile(
        role_family="ai_application_engineering",
        display_name="AI Agent / AI应用工程师",
        version="2026-H2",
        valid_from=date(2026, 7, 1),
        knowledge_as_of=date(2026, 8, 21),
        dimensions=[
            CompetencyDimensionRubric(
                id="role_dim_01",
                name="AI应用与Agent编排",
                weight=0.6,
                is_gating=True,
                minimum_criteria=[criterion("min_01", "拆分状态和工具边界")],
                excellence_signals=[criterion("exc_01", "比较编排方案")],
                critical_errors=[criterion("err_01", "无差别拆成多 Agent")],
            ),
            CompetencyDimensionRubric(
                id="role_dim_02",
                name="业务理解与任务建模",
                weight=0.4,
                is_gating=False,
                minimum_criteria=[criterion("min_02", "定义成功标准")],
                excellence_signals=[criterion("exc_02", "转成可交付规格")],
                critical_errors=[criterion("err_02", "无验收标准选模型")],
            ),
        ],
        source_refs=["test-source"],
    )


def make_evidence() -> list[Evidence]:
    return [
        Evidence(
            id="ev_support",
            turn_id="turn_01",
            requirement_ids=["req_01"],
            polarity="supporting",
            strength="strong",
            observation="候选人明确说明了状态和工具边界。",
            source_excerpt="我会先定义状态，再约束工具边界。",
        ),
        Evidence(
            id="ev_limit",
            turn_id="turn_02",
            requirement_ids=["req_01"],
            polarity="contradicting",
            strength="medium",
            observation="候选人在恢复场景中没有说明人工接管。",
            source_excerpt="失败时先继续重试。",
        ),
    ]


def make_snapshot() -> ScoreSnapshot:
    quality = RubricQuality(
        correctness="strong",
        specificity="strong",
        reasoning="strong",
        tradeoff_awareness="medium",
        transferability="unverified",
    )
    return ScoreSnapshot(
        role_family="ai_application_engineering",
        role_profile_version="2026-H2",
        scoring_engine_version="v1",
        requirement_assessments=[
            RequirementEvidenceAssessment(
                requirement_id="req_01",
                dimension_id="role_dim_01",
                level="L3",
                coverage=1.0,
                confidence="medium",
                satisfied_minimum_criterion_ids=["min_01"],
                supporting_evidence_ids=["ev_support"],
                limiting_evidence_ids=["ev_limit"],
                quality=quality,
                assessment_reasons=[
                    ScoreReason(
                        reason_type="strength",
                        text="已证明状态和工具边界意识。",
                        evidence_ids=["ev_support"],
                        rubric_signal_ids=["min_01"],
                    ),
                    ScoreReason(
                        reason_type="risk",
                        text="恢复策略仍缺少人工接管边界。",
                        evidence_ids=["ev_limit"],
                        rubric_signal_ids=["err_01"],
                    ),
                ],
            ),
            RequirementEvidenceAssessment(
                requirement_id="req_02",
                dimension_id="role_dim_02",
                level="UNVERIFIED",
                coverage=0.0,
                confidence="low",
                quality=RubricQuality(),
                assessment_reasons=[
                    ScoreReason(
                        reason_type="unverified",
                        text="当前面试尚未充分观察该能力。",
                    )
                ],
            ),
        ],
        requirement_scores=[
            RequirementScore(
                requirement_id="req_01",
                dimension_id="role_dim_01",
                base_score=82,
                adjustment=0,
                display_score=82,
            )
        ],
        radar_dimensions=[
            RadarDimensionResult(
                dimension_id="role_dim_01",
                name="AI应用与Agent编排",
                score=82,
                level="L3",
                coverage=1.0,
                confidence="medium",
                score_reasons=[
                    ScoreReason(
                        reason_type="strength",
                        text="状态与工具边界清晰。",
                        evidence_ids=["ev_support"],
                        rubric_signal_ids=["min_01"],
                    ),
                    ScoreReason(
                        reason_type="risk",
                        text="恢复与接管边界仍需核验。",
                        evidence_ids=["ev_limit"],
                        rubric_signal_ids=["err_01"],
                    ),
                ],
                requirement_breakdown=[
                    RequirementScore(
                        requirement_id="req_01",
                        dimension_id="role_dim_01",
                        base_score=82,
                        adjustment=0,
                        display_score=82,
                    )
                ],
            ),
            RadarDimensionResult(
                dimension_id="role_dim_02",
                name="业务理解与任务建模",
                score=None,
                level="UNVERIFIED",
                coverage=0.0,
                confidence="low",
                score_reasons=[
                    ScoreReason(
                        reason_type="unverified",
                        text="当前面试尚未充分观察该能力。",
                    )
                ],
            ),
        ],
        job_match=JobMatchResult(
            published=False,
            coverage=0.6,
            confidence="medium",
            limiting_reasons=[],
        ),
    )


def make_draft(
    *,
    strength_evidence_ids: list[str] | None = None,
    risk_evidence_ids: list[str] | None = None,
    unverified_text: str = "当前面试尚未充分观察该能力。",
    dimension_id: str = "role_dim_01",
    evidence_id: str = "ev_support",
) -> ReportNarrativeDraft:
    return ReportNarrativeDraft(
        executive_summary="当前报告基于已验证证据，仍有部分能力需要补充观察。",
        strengths=[
            NarrativeItem(
                text="能够清晰描述状态和工具边界。",
                dimension_ids=[dimension_id],
                evidence_ids=(
                    strength_evidence_ids
                    if strength_evidence_ids is not None
                    else [evidence_id]
                ),
            )
        ],
        risks=[
            NarrativeItem(
                text="恢复与人工接管边界需要继续核验。",
                dimension_ids=[dimension_id],
                evidence_ids=(
                    risk_evidence_ids
                    if risk_evidence_ids is not None
                    else ["ev_limit"]
                ),
            )
        ],
        unverified_areas=[
            NarrativeItem(
                text=unverified_text,
                dimension_ids=["role_dim_02"],
            )
        ],
        fit_contexts=[
            NarrativeItem(
                text="适合在已有边界约束的 Agent 工作流中继续验证。",
                dimension_ids=[dimension_id],
                evidence_ids=[evidence_id],
            )
        ],
        development_actions=[
            DevelopmentAction(
                dimension_id="role_dim_02",
                current_gap="尚未有足够观察",
                actions=["补充一个业务任务建模场景"],
                acceptance_criteria=["明确输入、输出与失败边界"],
            )
        ],
    )


def make_enterprise_copy_draft() -> EnterpriseCopyDraft:
    return EnterpriseCopyDraft(
        overall_assessment="当前已有可核验片段，复试需要补充两个独立场景。",
        reinterview_plan=[
            ReinterviewFocus(
                priority=1,
                dimension_id="role_dim_01",
                dimension_name="AI应用与Agent编排",
                reason="需要核验边界设计。",
                question="请说明一次状态与工具边界的设计取舍。",
                follow_ups=["当工具失败时如何处理？"],
                positive_signals=["能明确状态边界。"],
                risk_signals=["只描述流程而没有边界。"],
                pass_criteria=["能说明输入、边界和验证方式。"],
                suggested_minutes=8,
                related_evidence_ids=["ev_support"],
            ),
            ReinterviewFocus(
                priority=2,
                dimension_id="role_dim_02",
                dimension_name="业务理解与任务建模",
                reason="需要核验任务建模。",
                question="请把一个业务目标拆成可验收任务。",
                follow_ups=["如何定义成功？"],
                positive_signals=["能给出验收标准。"],
                risk_signals=["目标与输出无法对应。"],
                pass_criteria=["能说清输入、输出和边界。"],
                suggested_minutes=7,
                related_evidence_ids=["ev_support"],
            ),
        ],
    )


class ReportWriterServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = make_snapshot()
        self.evidence = make_evidence()
        self.role_profile = make_role_profile()

    def test_writer_calls_structured_llm_once(self) -> None:
        fake_llm = FakeLLM(make_draft())

        result = write_report_narrative(
            self.snapshot, self.evidence, self.role_profile, fake_llm
        )

        self.assertEqual(len(fake_llm.calls), 1)
        self.assertIs(fake_llm.calls[0][1], ReportNarrativeDraft)
        self.assertEqual(result.model_dump(), make_draft().model_dump())

    def test_enterprise_writer_calls_structured_llm_once_with_copy_contract(self) -> None:
        fake_llm = FakeLLM(make_enterprise_copy_draft())

        result = write_enterprise_copy(
            self.snapshot,
            self.role_profile,
            self.evidence,
            ["role_dim_01", "role_dim_02"],
            llm_client=fake_llm,
        )

        self.assertEqual(len(fake_llm.calls), 1)
        self.assertIs(fake_llm.calls[0][1], EnterpriseCopyDraft)
        self.assertEqual(result.model_dump(), make_enterprise_copy_draft().model_dump())

        prompt = "\n".join(text for _, text in fake_llm.calls[0][0])
        self.assertIn("req_", prompt)
        self.assertIn("d003_", prompt)
        self.assertIn("ev_", prompt)
        self.assertIn("Requirement", prompt)
        self.assertIn("RubricMatch", prompt)
        self.assertIn("成长", prompt)

    def test_enterprise_writer_requires_one_focus_per_selected_dimension(self) -> None:
        draft = make_enterprise_copy_draft()
        draft.reinterview_plan = draft.reinterview_plan[:1]

        with self.assertRaises(GroundingValidationError):
            write_enterprise_copy(
                self.snapshot,
                self.role_profile,
                self.evidence,
                ["role_dim_01", "role_dim_02"],
                llm_client=FakeLLM(draft),
            )

    def test_enterprise_writer_rejects_internal_ids_in_public_copy(self) -> None:
        draft = make_enterprise_copy_draft()
        draft.overall_assessment = "请继续核验 req_01 的边界。"

        with self.assertRaises(GroundingValidationError):
            write_enterprise_copy(
                self.snapshot,
                self.role_profile,
                self.evidence,
                ["role_dim_01", "role_dim_02"],
                llm_client=FakeLLM(draft),
            )

    def test_fallback_questions_are_dimension_specific(self) -> None:
        profile = load_role_profile("ai_application_engineering", "2026-H2")
        draft = fallback_enterprise_copy(
            self.snapshot,
            profile,
            ["role_dim_03", "role_dim_06"],
        )
        questions = [item.question for item in draft.reinterview_plan]

        self.assertEqual(len(set(questions)), 2)
        self.assertIn("记忆", questions[0])
        self.assertIn("成本", questions[1])
        self.assertIn("工具", questions[0])
        self.assertIn("优化", questions[1])

    def test_strength_requires_supporting_evidence(self) -> None:
        with self.assertRaises(GroundingValidationError):
            write_report_narrative(
                self.snapshot,
                self.evidence,
                self.role_profile,
                FakeLLM(make_draft(strength_evidence_ids=["ev_limit"])),
            )

    def test_risk_requires_contradicting_evidence(self) -> None:
        with self.assertRaises(GroundingValidationError):
            write_report_narrative(
                self.snapshot,
                self.evidence,
                self.role_profile,
                FakeLLM(make_draft(risk_evidence_ids=["ev_support"])),
            )

    def test_unverified_area_maps_to_unverified_reason(self) -> None:
        draft = write_report_narrative(
            self.snapshot,
            self.evidence,
            self.role_profile,
            FakeLLM(make_draft()),
        )

        self.assertEqual(draft.unverified_areas[0].dimension_ids, ["role_dim_02"])
        reasons = self.snapshot.radar_dimensions[1].score_reasons
        self.assertTrue(any(reason.kind == "unverified" for reason in reasons))
        self.assertEqual(draft.unverified_areas[0].evidence_ids, [])

    def test_unknown_dimension_or_evidence_is_rejected(self) -> None:
        with self.subTest(kind="dimension"):
            with self.assertRaises(GroundingValidationError):
                write_report_narrative(
                    self.snapshot,
                    self.evidence,
                    self.role_profile,
                    FakeLLM(make_draft(dimension_id="role_dim_missing")),
                )

        with self.subTest(kind="evidence"):
            with self.assertRaises(GroundingValidationError):
                write_report_narrative(
                    self.snapshot,
                    self.evidence,
                    self.role_profile,
                    FakeLLM(make_draft(evidence_id="ev_missing")),
                )

    def test_narrative_schema_has_no_score_fields(self) -> None:
        properties = ReportNarrativeDraft.model_json_schema()["properties"]
        self.assertFalse(
            {"score", "level", "fit_level"}.intersection(properties)
        )

    def test_llm_score_fields_are_rejected(self) -> None:
        invalid_response = make_draft().model_dump()
        invalid_response["score"] = 99

        with self.assertRaises(GroundingValidationError):
            write_report_narrative(
                self.snapshot,
                self.evidence,
                self.role_profile,
                FakeLLM(invalid_response),
            )

    def test_hire_and_reject_language_is_rejected(self) -> None:
        for phrase in ("建议录用", "建议淘汰", "必须录用", "不予录用"):
            with self.subTest(phrase=phrase):
                draft = make_draft()
                draft.executive_summary = phrase
                with self.assertRaises(GroundingValidationError):
                    write_report_narrative(
                        self.snapshot,
                        self.evidence,
                        self.role_profile,
                        FakeLLM(draft),
                    )

    def test_development_action_requires_known_dimension(self) -> None:
        draft = make_draft()
        draft.development_actions[0].dimension_id = "role_dim_missing"

        with self.assertRaises(GroundingValidationError):
            write_report_narrative(
                self.snapshot,
                self.evidence,
                self.role_profile,
                FakeLLM(draft),
            )

    def test_fallback_uses_score_reasons_without_llm(self) -> None:
        result = fallback_report_narrative(
            self.snapshot, self.evidence, self.role_profile
        )

        self.assertTrue(result.strengths)
        self.assertEqual(result.strengths[0].text, "状态与工具边界清晰。")
        self.assertEqual(result.strengths[0].evidence_ids, ["ev_support"])
        self.assertTrue(result.risks)
        self.assertEqual(result.risks[0].evidence_ids, ["ev_limit"])
        self.assertTrue(result.unverified_areas)
        self.assertEqual(result.unverified_areas[0].evidence_ids, [])


if __name__ == "__main__":
    unittest.main()
