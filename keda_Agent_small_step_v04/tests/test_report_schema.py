from datetime import date
import unittest

from pydantic import ValidationError

from profile_agent.schemas.report_schema import (
    EnterpriseAssessment,
    CompetencyDimensionRubric,
    JobMatchResult,
    RadarDimensionResult,
    ReinterviewFocus,
    RequirementEvidenceAssessment,
    RequirementScore,
    RubricCriterion,
    RubricQuality,
    RoleCompetencyProfile,
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


class ReportSchemaTest(unittest.TestCase):
    def test_enterprise_assessment_requires_decision_and_overall_assessment(
        self,
    ) -> None:
        with self.assertRaises(ValidationError):
            EnterpriseAssessment.model_validate({"strengths": []})

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
