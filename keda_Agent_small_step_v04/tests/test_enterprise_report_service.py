from __future__ import annotations

from datetime import datetime
import unittest

from profile_agent.schemas.report_schema import (
    DecisionSignal,
    EnterpriseAssessment,
    EvidenceExcerpt,
    JobMatchResult,
    RadarDimensionResult,
    ReinterviewFocus,
    ScoreReason,
    ScoreSnapshot,
)
from profile_agent.schemas.runtime_schema import InterviewTurn
from profile_agent.services.enterprise_report_service import (
    ReportConsistencyError,
    derive_hiring_decision,
    validate_enterprise_assessment,
)


_DIMENSION_IDS = [f"role_dim_{index:02d}" for index in range(1, 7)]


def _radar_dimension(
    dimension_id: str,
    *,
    unverified: bool = False,
    level: str = "L2",
) -> RadarDimensionResult:
    if unverified:
        return RadarDimensionResult(
            dimension_id=dimension_id,
            name=f"维度 {dimension_id}",
            level="UNVERIFIED",
            coverage=0.0,
            confidence="low",
        )
    return RadarDimensionResult(
        dimension_id=dimension_id,
        name=f"维度 {dimension_id}",
        score=72.0,
        level=level,
        coverage=1.0,
        confidence="high",
        score_reasons=[
            ScoreReason(
                reason_type="strength",
                text="回答给出了具体实现边界。",
                evidence_ids=["E001"],
            ),
            ScoreReason(
                reason_type="unverified",
                text="更高阶表现仍需补充观察。",
            ),
        ],
    )


def _snapshot(
    *,
    published: bool = True,
    confidence: str = "high",
    unverified_dimensions: list[str] | None = None,
    limiting_reasons: list[ScoreReason] | None = None,
    fit_level: str = "较高匹配",
) -> ScoreSnapshot:
    unverified = set(unverified_dimensions or [])
    radar = [
        _radar_dimension(dimension_id, unverified=dimension_id in unverified)
        for dimension_id in _DIMENSION_IDS
    ]
    return ScoreSnapshot(
        role_family="ai_application_engineering",
        role_profile_version="2026-H2",
        scoring_engine_version="v1",
        radar_dimensions=radar,
        job_match=JobMatchResult(
            raw_score=76.0 if published else None,
            published=published,
            fit_level=fit_level if published else None,
            coverage=0.86 if published else 0.62,
            confidence=confidence,
            limiting_reasons=limiting_reasons or [],
        ),
    )


def _turns() -> list[InterviewTurn]:
    return [
        InterviewTurn(
            id="turn_01",
            sequence_number=1,
            target_id="target_01",
            primary_requirement_id="req_01",
            question_mode="scenario",
            question="请说明失败恢复策略。",
            answer="我会先定义失败边界，再决定是否重试。",
            asked_at=datetime(2026, 8, 25, 12, 0),
            answered_at=datetime(2026, 8, 25, 12, 1),
        )
    ]


def _signal(
    *,
    title: str = "待核验维度",
    dimension_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> DecisionSignal:
    return DecisionSignal(
        title=title,
        text="该结论来自冻结的面试证据。",
        dimension_ids=dimension_ids or [],
        evidence_ids=evidence_ids or [],
        confidence="medium",
    )


def _enterprise(
    *,
    snapshot: ScoreSnapshot,
    decision: str = "CONDITIONAL_PROCEED",
    overall_assessment: str = "当前证据支持进入结构化复试，但仍有待核验区域。",
    provisional_score: float | None = None,
    unknowns: list[DecisionSignal] | None = None,
    risks: list[DecisionSignal] | None = None,
    reinterview_plan: list[ReinterviewFocus] | None = None,
    evidence_excerpts: list[EvidenceExcerpt] | None = None,
) -> EnterpriseAssessment:
    if provisional_score is None and snapshot.job_match.published:
        provisional_score = snapshot.job_match.raw_score
    return EnterpriseAssessment(
        decision=decision,
        decision_label="有条件进入结构化复试",
        provisional_score=provisional_score,
        confidence=snapshot.job_match.confidence,
        conditions=["补充核验待确认维度。"],
        decision_reasons=["岗位匹配证据已锁定。"],
        overall_assessment=overall_assessment,
        strengths=[_signal(title="已验证优势", evidence_ids=["E001"])],
        risks=risks or [],
        unknowns=unknowns or [],
        reinterview_plan=reinterview_plan or [],
        evidence_excerpts=evidence_excerpts or [],
    )


class EnterpriseReportServiceTest(unittest.TestCase):
    def test_low_confidence_with_unverified_dimension_is_conditional(self) -> None:
        snapshot = _snapshot(
            published=True,
            confidence="low",
            unverified_dimensions=["role_dim_06"],
        )

        decision = derive_hiring_decision(snapshot)

        self.assertEqual(decision.code, "CONDITIONAL_PROCEED")
        self.assertIn("role_dim_06", decision.unknown_dimension_ids)

    def test_unpublished_snapshot_suppresses_provisional_score(self) -> None:
        decision = derive_hiring_decision(_snapshot(published=False))

        self.assertEqual(decision.code, "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(decision.provisional_score)

    def test_published_fit_risk_is_not_recommended(self) -> None:
        decision = derive_hiring_decision(
            _snapshot(fit_level="存在明显岗位风险")
        )

        self.assertEqual(decision.code, "NOT_RECOMMENDED")

    def test_published_gating_critical_error_is_not_recommended(self) -> None:
        reason = ScoreReason(
            reason_type="critical_error",
            text="门槛维度存在未解除的关键错误。",
            evidence_ids=["E001"],
        )

        decision = derive_hiring_decision(
            _snapshot(limiting_reasons=[reason])
        )

        self.assertEqual(decision.code, "NOT_RECOMMENDED")

    def test_contradicting_limiting_evidence_requires_conditional_decision(self) -> None:
        reason = ScoreReason(
            reason_type="risk",
            text="回答与岗位门槛要求存在矛盾。",
            evidence_ids=["E001"],
        )

        decision = derive_hiring_decision(
            _snapshot(limiting_reasons=[reason])
        )

        self.assertEqual(decision.code, "CONDITIONAL_PROCEED")

    def test_guard_rejects_all_verified_claim_when_only_five_of_six_are_verified(
        self,
    ) -> None:
        snapshot = _snapshot(unverified_dimensions=["role_dim_06"])
        enterprise = _enterprise(
            snapshot=snapshot,
            overall_assessment="六项能力均已形成证据。",
        )

        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_rejects_empty_unknowns_for_unverified_dimensions(self) -> None:
        snapshot = _snapshot(unverified_dimensions=["role_dim_06"])
        enterprise = _enterprise(snapshot=snapshot)

        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_rejects_empty_risks_for_limiting_reasons(self) -> None:
        reason = ScoreReason(
            reason_type="risk",
            text="回答与门槛要求存在矛盾。",
            evidence_ids=["E001"],
        )
        snapshot = _snapshot(limiting_reasons=[reason])
        enterprise = _enterprise(snapshot=snapshot)

        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_rejects_proceed_when_confidence_is_low(self) -> None:
        snapshot = _snapshot(confidence="low")
        enterprise = _enterprise(
            snapshot=snapshot,
            decision="PROCEED",
            overall_assessment="当前证据支持直接推进。",
        )

        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_rejects_exposed_score_when_match_is_unpublished(self) -> None:
        snapshot = _snapshot(published=False)
        enterprise = _enterprise(snapshot=snapshot, provisional_score=42.0)

        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_rejects_duplicate_or_excessive_reinterview_priorities(self) -> None:
        snapshot = _snapshot(unverified_dimensions=["role_dim_06"])
        duplicate = ReinterviewFocus(
            priority=1,
            dimension_id="role_dim_06",
            dimension_name="维度 role_dim_06",
            reason="需要补充证据。",
            question="请给出一个新的场景。",
            follow_ups=["为什么？"],
            positive_signals=["有边界"],
            risk_signals=["无边界"],
            pass_criteria=["能复现验证过程"],
            suggested_minutes=8,
        )
        enterprise = _enterprise(
            snapshot=snapshot,
            unknowns=[_signal(dimension_ids=["role_dim_06"])],
            reinterview_plan=[duplicate, duplicate],
        )

        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

        enterprise = _enterprise(
            snapshot=snapshot,
            unknowns=[_signal(dimension_ids=["role_dim_06"])],
            reinterview_plan=[duplicate, duplicate, duplicate],
        )
        enterprise = enterprise.model_copy(
            update={"reinterview_plan": [duplicate, duplicate, duplicate, duplicate]}
        )
        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_rejects_excerpt_quote_not_present_in_linked_answer(self) -> None:
        snapshot = _snapshot()
        excerpt = EvidenceExcerpt(
            evidence_id="E001",
            turn_id="turn_01",
            conclusion="已验证失败边界。",
            quote="这句话不在回答里。",
            interpretation="回答提供了具体边界。",
            limitation="尚未验证高并发。",
        )
        enterprise = _enterprise(
            snapshot=snapshot,
            evidence_excerpts=[excerpt],
        )

        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_valid_conditional_assessment_passes_guard(self) -> None:
        snapshot = _snapshot(unverified_dimensions=["role_dim_06"])
        enterprise = _enterprise(
            snapshot=snapshot,
            unknowns=[_signal(dimension_ids=["role_dim_06"])],
            risks=[_signal(title="岗位限制")],
            evidence_excerpts=[
                EvidenceExcerpt(
                    evidence_id="E001",
                    turn_id="turn_01",
                    conclusion="已验证失败边界。",
                    quote="我会先定义失败边界，再决定是否重试。",
                    interpretation="回答提供了具体边界。",
                    limitation="尚未验证高并发。",
                )
            ],
        )

        validate_enterprise_assessment(enterprise, snapshot, _turns())


if __name__ == "__main__":
    unittest.main()
