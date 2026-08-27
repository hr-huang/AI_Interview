from datetime import date, datetime
import unittest

from pydantic import ValidationError

from profile_agent.schemas.report_schema import (
    AssessmentReport,
    CandidateOverview,
    CompetencyDimensionRubric,
    DecisionSignal,
    EnterpriseAssessment,
    EvidenceExcerpt,
    JobMatchResult,
    RadarDimensionResult,
    ReinterviewFocus,
    RequirementEvidenceAssessment,
    RequirementScore,
    ReportNarrativeDraft,
    RubricCriterion,
    RubricQuality,
    RoleCompetencyProfile,
    ScoreSnapshot,
    ScoreReason,
)


def _quality() -> RubricQuality:
    return RubricQuality(
        correctness="unverified",
        specificity="unverified",
        reasoning="unverified",
        tradeoff_awareness="unverified",
        transferability="unverified",
    )


def _reason(reason_type: str, evidence_ids: list[str] | None = None) -> ScoreReason:
    return ScoreReason(
        reason_type=reason_type,
        text="reason",
        evidence_ids=evidence_ids or [],
        rubric_signal_ids=[],
    )


def _criterion(criterion_id: str = "criterion_01") -> RubricCriterion:
    return RubricCriterion(id=criterion_id, text="criterion")


def _dimension(
    dimension_id: str,
    weight: float,
) -> CompetencyDimensionRubric:
    return CompetencyDimensionRubric(
        id=dimension_id,
        name="dimension",
        weight=weight,
        is_gating=False,
        minimum_criteria=[_criterion(f"{dimension_id}_criterion")],
        excellence_signals=[],
        critical_errors=[],
        accepted_alternatives=[],
    )


def _candidate_overview_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "candidate_id": "candidate_01",
        "candidate_name": "候选人",
        "target_role": "AI 应用工程师",
        "education_summary": "计算机科学学士",
        "experience_summary": "三年相关经验",
        "jd_focus": ["Agent 编排", "RAG"],
        "interview_rounds": 1,
        "generated_at": datetime(2026, 8, 25, 12, 0),
    }
    fields.update(overrides)
    return fields


def _decision_signal_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "title": "关键能力",
        "text": "能够解释方案边界和验证方式。",
        "dimension_ids": ["role_dim_01"],
        "evidence_ids": ["E001"],
        "confidence": "high",
    }
    fields.update(overrides)
    return fields


def _evidence_excerpt_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "evidence_id": "E001",
        "turn_id": "turn_01",
        "conclusion": "能复述关键取舍。",
        "quote": "我会先定义失败边界，再决定是否重试。",
        "interpretation": "体现对可靠性边界的理解。",
        "limitation": "尚未验证高并发场景。",
    }
    fields.update(overrides)
    return fields


def _reinterview_focus_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "priority": 1,
        "dimension_id": "role_dim_03",
        "dimension_name": "Context、RAG、Memory与工具工程",
        "reason": "当前只验证了过期文档过滤。",
        "question": "实时状态与历史记忆冲突时如何处理？",
        "follow_ups": ["如何验证冲突策略有效？"],
        "positive_signals": ["说明生命周期和冲突优先级"],
        "risk_signals": ["只描述向量检索"],
        "pass_criteria": ["给出可复现实验和回滚方式"],
        "suggested_minutes": 8,
        "related_evidence_ids": ["E003"],
    }
    fields.update(overrides)
    return fields


def _enterprise_assessment_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "decision": "PROCEED",
        "decision_label": "建议推进",
        "confidence": "high",
        "decision_reasons": ["关键能力有直接证据"],
        "overall_assessment": "候选人具备目标岗位所需能力。",
    }
    fields.update(overrides)
    return fields


def _assessment_report() -> AssessmentReport:
    return AssessmentReport(
        target_role="AI 应用工程师",
        score_snapshot=ScoreSnapshot(
            role_family="ai_application_engineering",
            role_profile_version="2026-H2",
            scoring_engine_version="v1",
            job_match=JobMatchResult(
                published=False,
                coverage=0.0,
                confidence="low",
            ),
        ),
        narrative=ReportNarrativeDraft(executive_summary="总结"),
        candidate_overview=CandidateOverview(**_candidate_overview_fields()),
        enterprise_assessment=EnterpriseAssessment(
            **_enterprise_assessment_fields()
        ),
    )


class ReportSchemaTest(unittest.TestCase):
    def test_candidate_overview_rejects_unknown_fields(self) -> None:
        fields = _candidate_overview_fields()
        fields["unexpected"] = "must be rejected"

        with self.assertRaises(ValidationError):
            CandidateOverview(**fields)

    def test_candidate_overview_requires_identity_and_timestamp_fields(self) -> None:
        for required_field in (
            "candidate_id",
            "target_role",
            "interview_rounds",
            "generated_at",
        ):
            with self.subTest(required_field=required_field):
                incomplete = _candidate_overview_fields()
                incomplete.pop(required_field)
                with self.assertRaises(ValidationError):
                    CandidateOverview(**incomplete)

        for interview_rounds in (0, 1):
            with self.subTest(interview_rounds=interview_rounds):
                overview = CandidateOverview(
                    **_candidate_overview_fields(
                        interview_rounds=interview_rounds
                    )
                )
                self.assertEqual(overview.interview_rounds, interview_rounds)

        with self.assertRaises(ValidationError):
            CandidateOverview(
                **_candidate_overview_fields(interview_rounds=-1)
            )

        overview = CandidateOverview(
            **_candidate_overview_fields(
                jd_focus=[f"focus_{index}" for index in range(5)]
            )
        )
        self.assertEqual(len(overview.jd_focus), 5)

        with self.assertRaises(ValidationError):
            CandidateOverview(
                **_candidate_overview_fields(
                    jd_focus=[f"focus_{index}" for index in range(6)]
                )
            )

    def test_decision_signal_has_required_fields_and_forbids_extras(self) -> None:
        signal = DecisionSignal(**_decision_signal_fields())
        self.assertEqual(signal.title, "关键能力")

        for required_field in ("title", "text", "confidence"):
            with self.subTest(required_field=required_field):
                incomplete = _decision_signal_fields()
                incomplete.pop(required_field)
                with self.assertRaises(ValidationError):
                    DecisionSignal(**incomplete)

        with self.assertRaises(ValidationError):
            DecisionSignal(
                **_decision_signal_fields(unexpected="must be rejected")
            )

    def test_evidence_excerpt_has_required_fields_and_forbids_extras(self) -> None:
        excerpt = EvidenceExcerpt(**_evidence_excerpt_fields())
        self.assertEqual(excerpt.evidence_id, "E001")

        for required_field in (
            "evidence_id",
            "turn_id",
            "conclusion",
            "quote",
            "interpretation",
            "limitation",
        ):
            with self.subTest(required_field=required_field):
                incomplete = _evidence_excerpt_fields()
                incomplete.pop(required_field)
                with self.assertRaises(ValidationError):
                    EvidenceExcerpt(**incomplete)

        with self.assertRaises(ValidationError):
            EvidenceExcerpt(
                **_evidence_excerpt_fields(unexpected="must be rejected")
            )

    def test_enterprise_assessment_requires_decision_and_overall_assessment(
        self,
    ) -> None:
        for required_field in ("decision", "overall_assessment"):
            with self.subTest(required_field=required_field):
                incomplete = _enterprise_assessment_fields()
                incomplete.pop(required_field)
                with self.assertRaises(ValidationError):
                    EnterpriseAssessment.model_validate(incomplete)

    def test_enterprise_assessment_enforces_decision_and_score_boundaries(
        self,
    ) -> None:
        for decision in (
            "PROCEED",
            "CONDITIONAL_PROCEED",
            "INSUFFICIENT_EVIDENCE",
            "NOT_RECOMMENDED",
        ):
            with self.subTest(decision=decision):
                assessment = EnterpriseAssessment(
                    **_enterprise_assessment_fields(decision=decision)
                )
                self.assertEqual(assessment.decision, decision)

        with self.assertRaises(ValidationError):
            EnterpriseAssessment(
                **_enterprise_assessment_fields(decision="UNKNOWN")
            )

        for score in (0, 100):
            with self.subTest(score=score):
                assessment = EnterpriseAssessment(
                    **_enterprise_assessment_fields(provisional_score=score)
                )
                self.assertEqual(assessment.provisional_score, score)

        for score in (-1, 101):
            with self.subTest(score=score):
                with self.assertRaises(ValidationError):
                    EnterpriseAssessment(
                        **_enterprise_assessment_fields(provisional_score=score)
                    )

    def test_enterprise_assessment_enforces_collection_boundaries(self) -> None:
        factories = {
            "conditions": lambda length: [
                f"condition_{index}" for index in range(length)
            ],
            "decision_reasons": lambda length: [
                f"reason_{index}" for index in range(length)
            ],
            "strengths": lambda length: [
                DecisionSignal(
                    **_decision_signal_fields(title=f"strength_{index}")
                )
                for index in range(length)
            ],
            "risks": lambda length: [
                DecisionSignal(
                    **_decision_signal_fields(title=f"risk_{index}")
                )
                for index in range(length)
            ],
            "unknowns": lambda length: [
                DecisionSignal(
                    **_decision_signal_fields(title=f"unknown_{index}")
                )
                for index in range(length)
            ],
            "reinterview_plan": lambda length: [
                ReinterviewFocus(
                    **_reinterview_focus_fields(
                        dimension_id=f"role_dim_{index + 1:02d}"
                    )
                )
                for index in range(length)
            ],
        }
        bounds = {
            "conditions": (3, (4,)),
            "decision_reasons": (3, (0, 4)),
            "strengths": (3, (4,)),
            "risks": (3, (4,)),
            "unknowns": (2, (3,)),
            "reinterview_plan": (3, (4,)),
        }

        for field, (maximum, invalid_lengths) in bounds.items():
            valid_lengths = (maximum,)
            if field == "decision_reasons":
                valid_lengths = (1, maximum)
            for length in valid_lengths:
                with self.subTest(field=field, length=length):
                    assessment = EnterpriseAssessment(
                        **_enterprise_assessment_fields(
                            **{field: factories[field](length)}
                        )
                    )
                    self.assertEqual(len(getattr(assessment, field)), length)

            for length in invalid_lengths:
                with self.subTest(field=field, length=length):
                    with self.assertRaises(ValidationError):
                        EnterpriseAssessment(
                            **_enterprise_assessment_fields(
                                **{field: factories[field](length)}
                            )
                        )

    def test_development_actions_is_marked_as_legacy(self) -> None:
        field = ReportNarrativeDraft.model_fields["development_actions"]
        self.assertTrue(field.deprecated)
        self.assertIn("legacy", (field.description or "").lower())

    def test_reinterview_focus_requires_observable_signals_and_minutes(self) -> None:
        focus = ReinterviewFocus(
            priority=1,
            dimension_id="role_dim_03",
            dimension_name="Context、RAG、Memory与工具工程",
            reason="当前只验证了过期文档过滤。",
            question="实时状态与历史记忆冲突时如何处理？",
            follow_ups=["如何验证冲突策略有效？"],
            positive_signals=["说明生命周期和冲突优先级"],
            risk_signals=["只描述向量检索"],
            pass_criteria=["给出可复现实验和回滚方式"],
            suggested_minutes=8,
            related_evidence_ids=["E003"],
        )
        self.assertEqual(focus.priority, 1)

    def test_reinterview_focus_enforces_priority_and_minutes_boundaries(self) -> None:
        for field, valid_values, invalid_values in (
            ("priority", (1, 3), (0, 4)),
            ("suggested_minutes", (3, 15), (2, 16)),
        ):
            for value in valid_values:
                with self.subTest(field=field, value=value):
                    focus = ReinterviewFocus(
                        **_reinterview_focus_fields(**{field: value})
                    )
                    self.assertEqual(getattr(focus, field), value)

            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        ReinterviewFocus(
                            **_reinterview_focus_fields(**{field: value})
                        )

    def test_reinterview_focus_enforces_signal_list_boundaries(self) -> None:
        bounds = {
            "follow_ups": (1, 2),
            "positive_signals": (1, 3),
            "risk_signals": (1, 3),
            "pass_criteria": (1, 3),
        }

        for field, (minimum, maximum) in bounds.items():
            for length in (minimum, maximum):
                with self.subTest(field=field, length=length):
                    values = [f"{field}_{index}" for index in range(length)]
                    focus = ReinterviewFocus(
                        **_reinterview_focus_fields(**{field: values})
                    )
                    self.assertEqual(len(getattr(focus, field)), length)

            for length in (minimum - 1, maximum + 1):
                with self.subTest(field=field, length=length):
                    values = [f"{field}_{index}" for index in range(length)]
                    with self.assertRaises(ValidationError):
                        ReinterviewFocus(
                            **_reinterview_focus_fields(**{field: values})
                        )

    def test_assessment_report_requires_enterprise_contract_sections(self) -> None:
        fields = _assessment_report().model_dump()

        for missing_field in ("candidate_overview", "enterprise_assessment"):
            with self.subTest(missing_field=missing_field):
                incomplete = {
                    key: value
                    for key, value in fields.items()
                    if key != missing_field
                }
                with self.assertRaises(ValidationError):
                    AssessmentReport.model_validate(incomplete)

    def test_unverified_assessment_is_valid_without_numeric_score(self) -> None:
        assessment = RequirementEvidenceAssessment(
            requirement_id="req_01",
            dimension_id="role_dim_01",
            level="UNVERIFIED",
            coverage=0.0,
            confidence="low",
            satisfied_minimum_criterion_ids=[],
            matched_excellence_signal_ids=[],
            unresolved_critical_error_ids=[],
            accepted_alternative_ids=[],
            supporting_evidence_ids=[],
            limiting_evidence_ids=[],
            transfer_evidence_ids=[],
            quality=_quality(),
            assessment_reasons=[_reason("unverified")],
        )

        self.assertEqual(assessment.level, "UNVERIFIED")
        properties = RequirementEvidenceAssessment.model_json_schema()[
            "properties"
        ]
        self.assertNotIn("base_score", properties)
        self.assertNotIn("adjustment", properties)
        self.assertNotIn("display_score", properties)

    def test_requirement_score_requires_all_numeric_fields(self) -> None:
        fields = {
            "requirement_id": "req_01",
            "dimension_id": "role_dim_01",
            "base_score": 65,
            "adjustment": 3,
            "display_score": 68,
        }

        for missing_field in ("base_score", "adjustment", "display_score"):
            with self.subTest(missing_field=missing_field):
                incomplete = {
                    key: value
                    for key, value in fields.items()
                    if key != missing_field
                }
                with self.assertRaises(ValidationError):
                    RequirementScore(**incomplete)

    def test_requirement_score_rejects_level_and_evidence_fields(self) -> None:
        fields = {
            "requirement_id": "req_01",
            "dimension_id": "role_dim_01",
            "base_score": 65,
            "adjustment": 3,
            "display_score": 68,
        }

        for extra_field, extra_value in (
            ("level", "L2"),
            ("evidence_ids", ["evidence_01"]),
        ):
            with self.subTest(extra_field=extra_field):
                with self.assertRaises(ValidationError):
                    RequirementScore(**fields, **{extra_field: extra_value})

    def test_strength_risk_and_critical_reason_require_evidence(self) -> None:
        for reason_type in ("strength", "risk", "critical_error"):
            with self.subTest(reason_type=reason_type):
                with self.assertRaises(ValidationError):
                    _reason(reason_type)

    def test_unverified_reason_can_have_no_evidence(self) -> None:
        reason = _reason("unverified")

        self.assertEqual(reason.evidence_ids, [])

    def test_scored_radar_dimension_requires_two_reasons(self) -> None:
        fields = {
            "dimension_id": "role_dim_01",
            "name": "dimension",
            "score": 80,
            "level": "L3",
            "coverage": 1.0,
            "confidence": "high",
            "requirement_breakdown": [],
        }

        with self.assertRaises(ValidationError):
            RadarDimensionResult(
                **fields,
                score_reasons=[_reason("strength", ["evidence_01"])],
            )

        result = RadarDimensionResult(
            **fields,
            score_reasons=[
                _reason("strength", ["evidence_01"]),
                _reason("unverified"),
            ],
        )
        self.assertEqual(len(result.score_reasons), 2)

    def test_role_dimension_weights_must_sum_to_one(self) -> None:
        common = {
            "role_family": "ai_application_engineering",
            "display_name": "AI Agent / AI应用工程师",
            "version": "2026-H2",
            "valid_from": date(2026, 7, 1),
            "knowledge_as_of": date(2026, 8, 21),
            "source_refs": ["source_01"],
        }

        with self.assertRaises(ValidationError):
            RoleCompetencyProfile(
                **common,
                dimensions=[
                    _dimension("role_dim_01", 0.50),
                    _dimension("role_dim_02", 0.40),
                ],
            )

        profile = RoleCompetencyProfile(
            **common,
            dimensions=[
                _dimension("role_dim_01", 0.50),
                _dimension("role_dim_02", 0.50),
            ],
        )
        self.assertEqual(sum(item.weight for item in profile.dimensions), 1.0)

    def test_role_dimension_ids_are_unique(self) -> None:
        with self.assertRaises(ValidationError):
            RoleCompetencyProfile(
                role_family="ai_application_engineering",
                display_name="AI Agent / AI应用工程师",
                version="2026-H2",
                valid_from=date(2026, 7, 1),
                knowledge_as_of=date(2026, 8, 21),
                source_refs=["source_01"],
                dimensions=[
                    _dimension("role_dim_01", 0.50),
                    _dimension("role_dim_01", 0.50),
                ],
            )

    def test_unpublished_job_match_has_no_score_or_fit_level(self) -> None:
        base = {
            "published": False,
            "coverage": 0.60,
            "confidence": "low",
            "limiting_reasons": [],
        }

        with self.assertRaises(ValidationError):
            JobMatchResult(**base, raw_score=70)

        with self.assertRaises(ValidationError):
            JobMatchResult(**base, fit_level="较高匹配")

    def test_requirement_evidence_assessment_has_no_numeric_score_fields(
        self,
    ) -> None:
        properties = RequirementEvidenceAssessment.model_json_schema()[
            "properties"
        ]

        self.assertNotIn("base_score", properties)
        self.assertNotIn("adjustment", properties)
        self.assertNotIn("display_score", properties)

    def test_requirement_score_is_numeric_only(self) -> None:
        score = RequirementScore(
            requirement_id="req_01",
            dimension_id="role_dim_01",
            base_score=65,
            adjustment=3,
            display_score=68,
        )

        self.assertEqual(
            set(score.model_dump()),
            {
                "requirement_id",
                "dimension_id",
                "base_score",
                "adjustment",
                "display_score",
            },
        )


if __name__ == "__main__":
    unittest.main()
