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
    RequirementEvidenceAssessment,
    RoleCompetencyProfile,
    RubricQuality,
    ScoreReason,
    ScoreSnapshot,
)
from profile_agent.schemas.runtime_schema import Evidence, InterviewTurn
from profile_agent.services.enterprise_report_service import (
    ReportConsistencyError,
    build_decision_signals,
    build_evidence_excerpts,
    derive_hiring_decision,
    select_reinterview_dimensions,
    validate_enterprise_assessment,
)
from profile_agent.services.role_profile_service import load_role_profile


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


def _snapshot_with_six_reinterview_gaps() -> ScoreSnapshot:
    snapshot = _snapshot(unverified_dimensions=["role_dim_06"])
    radar_by_id = {
        radar.dimension_id: radar for radar in snapshot.radar_dimensions
    }
    radar_by_id["role_dim_01"] = radar_by_id["role_dim_01"].model_copy(
        update={"score": 90.0, "confidence": "high"}
    )
    radar_by_id["role_dim_02"] = radar_by_id["role_dim_02"].model_copy(
        update={"score": 68.0, "confidence": "medium"}
    )
    radar_by_id["role_dim_03"] = radar_by_id["role_dim_03"].model_copy(
        update={
            "score": 35.0,
            "level": "L1",
            "confidence": "low",
            "score_reasons": [
                ScoreReason(
                    reason_type="critical_error",
                    text="关键限制需要复试核验。",
                    evidence_ids=["E001"],
                ),
                ScoreReason(
                    reason_type="strength",
                    text="已有局部场景证据。",
                    evidence_ids=["E001"],
                ),
            ],
        }
    )
    radar_by_id["role_dim_04"] = radar_by_id["role_dim_04"].model_copy(
        update={"score": 64.0, "confidence": "high"}
    )
    radar_by_id["role_dim_05"] = radar_by_id["role_dim_05"].model_copy(
        update={
            "score": 58.0,
            "confidence": "low",
            "score_reasons": [
                ScoreReason(
                    reason_type="risk",
                    text="门槛维度仍有关键限制。",
                    evidence_ids=["E005"],
                ),
                ScoreReason(
                    reason_type="strength",
                    text="已有局部治理证据。",
                    evidence_ids=["E005"],
                ),
            ],
        }
    )
    limiting_reason = ScoreReason(
        reason_type="critical_error",
        text="role_dim_03 存在岗位限制。",
        evidence_ids=["E001"],
    )
    return snapshot.model_copy(
        update={
            "radar_dimensions": list(radar_by_id.values()),
            "job_match": snapshot.job_match.model_copy(
                update={"limiting_reasons": [limiting_reason]}
            ),
        }
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


def _evidences() -> list[Evidence]:
    return [
        Evidence(
            id="E001",
            turn_id="turn_01",
            requirement_ids=["req_01"],
            polarity="supporting",
            strength="strong",
            observation="回答说明了失败边界和重试决策。",
            source_excerpt="我会先定义失败边界",
        )
    ]


def _profile() -> RoleCompetencyProfile:
    return load_role_profile("ai_application_engineering", "2026-H2")


def _flatten_dimensions(signals: list[DecisionSignal]) -> set[str]:
    return {
        dimension_id
        for signal in signals
        for dimension_id in signal.dimension_ids
    }


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
    def test_reinterview_selects_only_top_three_gaps(self) -> None:
        selected = select_reinterview_dimensions(
            _snapshot_with_six_reinterview_gaps(), _profile()
        )

        self.assertLessEqual(len(selected), 3)
        self.assertEqual(selected[0], "role_dim_05")

    def test_non_gating_critical_gap_does_not_beat_gating_low_confidence_gap(
        self,
    ) -> None:
        snapshot = _snapshot_with_six_reinterview_gaps()
        radar_by_id = {
            radar.dimension_id: radar for radar in snapshot.radar_dimensions
        }
        for dimension_id, radar in list(radar_by_id.items()):
            if dimension_id == "role_dim_03":
                continue
            radar_by_id[dimension_id] = radar.model_copy(
                update={
                    "confidence": (
                        "high" if dimension_id == "role_dim_05" else radar.confidence
                    ),
                    "score_reasons": [
                        ScoreReason(
                            reason_type="strength",
                            text="已有独立场景证据。",
                            evidence_ids=["E002"],
                        ),
                        ScoreReason(
                            reason_type="unverified",
                            text="更高阶表现仍需补充观察。",
                        ),
                    ]
                }
            )
        radar_by_id["role_dim_02"] = radar_by_id["role_dim_02"].model_copy(
            update={"confidence": "low"}
        )
        snapshot = snapshot.model_copy(
            update={
                "radar_dimensions": list(radar_by_id.values()),
                "job_match": snapshot.job_match.model_copy(
                    update={
                        "limiting_reasons": [
                            ScoreReason(
                                reason_type="critical_error",
                                text="role_dim_03 存在岗位限制。",
                                evidence_ids=["E99"],
                                rubric_signal_ids=["err_03"],
                            )
                        ]
                    }
                ),
            }
        )
        role_03 = next(
            radar
            for radar in snapshot.radar_dimensions
            if radar.dimension_id == "role_dim_03"
        )
        role_03.score_reasons[0] = role_03.score_reasons[0].model_copy(
            update={"evidence_ids": ["E99"], "rubric_signal_ids": ["err_03"]}
        )

        selected = select_reinterview_dimensions(snapshot, _profile())

        self.assertEqual(selected[0], "role_dim_02")

    def test_evidence_excerpt_quote_is_an_exact_answer_substring(self) -> None:
        excerpts = build_evidence_excerpts(
            _snapshot(), _evidences(), _turns()
        )
        answers = {turn.id: turn.answer or "" for turn in _turns()}

        self.assertTrue(excerpts)
        for excerpt in excerpts:
            self.assertIn(excerpt.quote, answers[excerpt.turn_id])
            self.assertNotEqual(excerpt.quote, answers[excerpt.turn_id])

    def test_unverified_dimension_becomes_unknown_not_risk(self) -> None:
        strengths, risks, unknowns = build_decision_signals(
            _snapshot(unverified_dimensions=["role_dim_06"]),
            _profile(),
        )

        self.assertIn("role_dim_06", _flatten_dimensions(unknowns))
        self.assertNotIn("role_dim_06", _flatten_dimensions(risks))

    def test_unverified_dimension_with_risk_reason_stays_unknown(self) -> None:
        snapshot = _snapshot(unverified_dimensions=["role_dim_06"])
        snapshot.radar_dimensions[5].score_reasons = [
            ScoreReason(
                reason_type="risk",
                text="该维度仍缺少可验证证据。",
                evidence_ids=["E001"],
            )
        ]

        _, risks, unknowns = build_decision_signals(snapshot, _profile())

        self.assertNotIn("role_dim_06", _flatten_dimensions(risks))
        self.assertIn("role_dim_06", _flatten_dimensions(unknowns))

    def test_unverified_rubric_only_limiting_reason_does_not_become_global_risk(
        self,
    ) -> None:
        reason = ScoreReason(
            reason_type="risk",
            text="该限制只关联未验证维度。",
            evidence_ids=["E999"],
            rubric_signal_ids=["d06_min_01"],
        )
        snapshot = _snapshot(
            unverified_dimensions=["role_dim_06"],
            limiting_reasons=[reason],
        )

        _, risks, unknowns = build_decision_signals(snapshot, _profile())

        self.assertEqual(risks, [])
        self.assertIn("role_dim_06", _flatten_dimensions(unknowns))

    def test_guard_rejects_risk_signal_for_unverified_dimension(self) -> None:
        snapshot = _snapshot(unverified_dimensions=["role_dim_06"])
        snapshot.radar_dimensions[5].score_reasons = [
            ScoreReason(
                reason_type="risk",
                text="该维度仍未验证。",
                evidence_ids=["E001"],
            )
        ]
        enterprise = _enterprise(
            snapshot=snapshot,
            unknowns=[_signal(dimension_ids=["role_dim_06"])],
            risks=[_signal(title="不应出现的未验证风险", dimension_ids=["role_dim_06"])],
        )

        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_does_not_require_risk_for_unverified_rubric_only_limitation(
        self,
    ) -> None:
        reason = ScoreReason(
            reason_type="risk",
            text="该限制只关联未验证维度。",
            evidence_ids=["E999"],
            rubric_signal_ids=["d06_min_01"],
        )
        snapshot = _snapshot(
            unverified_dimensions=["role_dim_06"],
            limiting_reasons=[reason],
        )
        enterprise = _enterprise(
            snapshot=snapshot,
            unknowns=[_signal(dimension_ids=["role_dim_06"])],
        )

        validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_invalid_source_excerpt_is_skipped_without_answer_fallback(self) -> None:
        evidence = _evidences()[0].model_copy(
            update={"source_excerpt": "回答中没有这段文字"}
        )

        excerpts = build_evidence_excerpts(_snapshot(), [evidence], _turns())

        self.assertEqual(excerpts, [])

    def test_excerpt_with_wrong_turn_id_is_skipped(self) -> None:
        evidence = _evidences()[0].model_copy(update={"turn_id": "turn_02"})

        excerpts = build_evidence_excerpts(_snapshot(), [evidence], _turns())

        self.assertEqual(excerpts, [])

    def test_empty_source_excerpt_is_skipped_without_answer_fallback(self) -> None:
        evidence = _evidences()[0].model_copy(update={"source_excerpt": ""})

        excerpts = build_evidence_excerpts(_snapshot(), [evidence], _turns())

        self.assertEqual(excerpts, [])

    def test_full_answer_source_excerpt_is_skipped(self) -> None:
        evidence = _evidences()[0].model_copy(
            update={"source_excerpt": _turns()[0].answer}
        )

        excerpts = build_evidence_excerpts(_snapshot(), [evidence], _turns())

        self.assertEqual(excerpts, [])

    def test_unreferenced_evidence_is_not_projected_to_excerpts(self) -> None:
        extra = Evidence(
            id="E002",
            turn_id="turn_01",
            requirement_ids=["req_01"],
            polarity="supporting",
            strength="strong",
            observation="未被评分原因引用。",
            source_excerpt="再决定是否重试",
        )

        excerpts = build_evidence_excerpts(
            _snapshot(), _evidences() + [extra], _turns()
        )

        self.assertEqual([excerpt.evidence_id for excerpt in excerpts], ["E001"])

    def test_excerpt_uses_readable_criteria_and_unmet_minimum_limitation(self) -> None:
        snapshot = _snapshot()
        snapshot.requirement_assessments = [
            RequirementEvidenceAssessment(
                requirement_id="req_01",
                dimension_id="role_dim_01",
                level="L1",
                coverage=0.5,
                confidence="medium",
                satisfied_minimum_criterion_ids=["d01_min_01"],
                supporting_evidence_ids=["E001"],
                quality=RubricQuality(),
                assessment_reasons=[
                    ScoreReason(
                        reason_type="strength",
                        text="回答命中了最低充分条件。",
                        evidence_ids=["E001"],
                        rubric_signal_ids=["d01_min_01"],
                    )
                ],
            )
        ]

        excerpt = build_evidence_excerpts(snapshot, _evidences(), _turns())[0]

        self.assertIn("拆分任务、状态、工具和人工介入边界", excerpt.interpretation)
        self.assertNotIn("d01_min_01", excerpt.interpretation)
        self.assertIn("解释状态流转、路由和失败恢复", excerpt.limitation)

    def test_unknowns_prioritize_gating_dimensions_and_are_bounded(self) -> None:
        snapshot = _snapshot(
            unverified_dimensions=["role_dim_02", "role_dim_05", "role_dim_06"]
        )

        _, _, unknowns = build_decision_signals(snapshot, _profile())

        self.assertEqual(len(unknowns), 2)
        self.assertEqual(unknowns[0].dimension_ids, ["role_dim_05"])
        self.assertEqual(unknowns[1].dimension_ids, ["role_dim_02"])

    def test_risk_order_puts_critical_reasons_before_low_gating_scores(self) -> None:
        snapshot = _snapshot()
        snapshot.radar_dimensions[1].score = 40.0
        snapshot.radar_dimensions[1].level = "L1"
        snapshot.radar_dimensions[2].score_reasons = [
            ScoreReason(
                reason_type="critical_error",
                text="存在未经验证的高风险操作。",
                evidence_ids=["E001"],
            ),
            ScoreReason(
                reason_type="unverified",
                text="仍需观察。",
            ),
        ]

        _, risks, _ = build_decision_signals(snapshot, _profile())

        self.assertGreaterEqual(len(risks), 2)
        self.assertEqual(risks[0].dimension_ids, ["role_dim_03"])
        self.assertEqual(risks[1].dimension_ids, ["role_dim_02"])

    def test_strengths_are_bounded_and_ties_use_stable_dimension_order(self) -> None:
        strengths, _, _ = build_decision_signals(_snapshot(), _profile())

        self.assertEqual(len(strengths), 3)
        self.assertEqual(
            [signal.dimension_ids[0] for signal in strengths],
            ["role_dim_01", "role_dim_02", "role_dim_03"],
        )

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

    def test_non_gating_critical_error_is_not_negative(self) -> None:
        snapshot = _snapshot()
        snapshot.radar_dimensions[0].score_reasons = [
            ScoreReason(
                reason_type="critical_error",
                text="非门槛维度存在待核验关键错误。",
                evidence_ids=["E001"],
            ),
            ScoreReason(
                reason_type="unverified",
                text="仍需观察该维度的迁移能力。",
            ),
        ]

        decision = derive_hiring_decision(snapshot)

        self.assertEqual(decision.code, "PROCEED")

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

    def test_guard_allows_partial_unknown_coverage_with_bounded_signal(self) -> None:
        snapshot = _snapshot(
            unverified_dimensions=["role_dim_05", "role_dim_06"]
        )
        enterprise = _enterprise(
            snapshot=snapshot,
            unknowns=[_signal(dimension_ids=["role_dim_05"])],
        )

        validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_rejects_unknown_signal_for_verified_dimension(self) -> None:
        snapshot = _snapshot(unverified_dimensions=["role_dim_06"])
        enterprise = _enterprise(
            snapshot=snapshot,
            unknowns=[
                _signal(dimension_ids=["role_dim_06", "role_dim_01"])
            ],
        )

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

    def test_guard_requires_risks_to_cover_limiting_dimension(self) -> None:
        snapshot = _snapshot()
        snapshot.radar_dimensions[1].score_reasons = [
            ScoreReason(
                reason_type="risk",
                text="该维度存在限制证据。",
                evidence_ids=["E001"],
            ),
            ScoreReason(
                reason_type="unverified",
                text="仍需补充场景观察。",
            ),
        ]
        enterprise = _enterprise(
            snapshot=snapshot,
            risks=[_signal(title="错误维度风险", dimension_ids=["role_dim_01"])],
        )

        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_allows_extra_risks_when_required_dimensions_are_covered(self) -> None:
        snapshot = _snapshot()
        snapshot.radar_dimensions[1].score_reasons = [
            ScoreReason(
                reason_type="risk",
                text="该维度存在限制证据。",
                evidence_ids=["E001"],
            ),
            ScoreReason(
                reason_type="unverified",
                text="仍需补充场景观察。",
            ),
        ]
        enterprise = _enterprise(
            snapshot=snapshot,
            risks=[
                _signal(title="门槛维度风险", dimension_ids=["role_dim_02"]),
                _signal(title="额外观察风险"),
            ],
        )

        validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_rejects_extra_risk_on_verified_dimension_without_risk(self) -> None:
        snapshot = _snapshot()
        snapshot.radar_dimensions[1].score_reasons = [
            ScoreReason(
                reason_type="risk",
                text="该维度存在限制证据。",
                evidence_ids=["E001"],
            ),
            ScoreReason(
                reason_type="unverified",
                text="仍需补充场景观察。",
            ),
        ]
        enterprise = _enterprise(
            snapshot=snapshot,
            risks=[
                _signal(title="门槛维度风险", dimension_ids=["role_dim_02"]),
                _signal(title="错误额外维度风险", dimension_ids=["role_dim_01"]),
            ],
        )

        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_allows_risks_to_cover_only_one_of_multiple_risk_dimensions(self) -> None:
        snapshot = _snapshot()
        for index in (0, 1):
            snapshot.radar_dimensions[index].score_reasons = [
                ScoreReason(
                    reason_type="risk",
                    text="该维度存在限制证据。",
                    evidence_ids=["E001"],
                ),
                ScoreReason(
                    reason_type="unverified",
                    text="仍需补充场景观察。",
                ),
            ]
        enterprise = _enterprise(
            snapshot=snapshot,
            risks=[_signal(title="一个风险重点", dimension_ids=["role_dim_01"])],
        )

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

    def test_guard_rejects_quote_with_normalized_whitespace(self) -> None:
        snapshot = _snapshot()
        excerpt = EvidenceExcerpt(
            evidence_id="E001",
            turn_id="turn_01",
            conclusion="已验证失败边界。",
            quote=" 我会先定义失败边界，再决定是否重试。",
            interpretation="回答提供了具体边界。",
            limitation="尚未验证高并发。",
        )
        enterprise = _enterprise(
            snapshot=snapshot,
            evidence_excerpts=[excerpt],
        )

        with self.assertRaises(ReportConsistencyError):
            validate_enterprise_assessment(enterprise, snapshot, _turns())

    def test_guard_allows_explicit_negative_all_verified_phrase(self) -> None:
        snapshot = _snapshot(unverified_dimensions=["role_dim_06"])
        enterprise = _enterprise(
            snapshot=snapshot,
            overall_assessment="并非全部能力已验证，role_dim_06 尚未验证。",
            unknowns=[_signal(dimension_ids=["role_dim_06"])],
        )

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
