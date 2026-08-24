import unittest

from profile_agent.calibration.report_assertions import (
    evaluate_report_calibration,
    evaluate_report_invariants,
    require_calibration_pass,
)
from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.calibration.schemas import CalibrationAssertion
from profile_agent.schemas.report_schema import (
    AssessmentReport,
    JobMatchResult,
    RadarDimensionResult,
    RequirementEvidenceAssessment,
    RequirementScoringBinding,
    ReportNarrativeDraft,
    RubricMatch,
    RubricMatchBatch,
    RubricQuality,
    ScoreReason,
    ScoreSnapshot,
    ScoringBlueprint,
)


def _requirement_ids(case) -> list[str]:
    return [
        requirement.id
        for target in case.plan.targets
        for requirement in target.evidence_requirements
    ]


def _dimension_for_requirement(requirement_id: str) -> str:
    number = int(requirement_id.rsplit("_", 1)[1])
    return f"role_dim_{number:02d}"


def _blueprint_for(case) -> ScoringBlueprint:
    requirement_ids = _requirement_ids(case)
    return ScoringBlueprint(
        role_family="ai_application_engineering",
        role_profile_version="2026-H2",
        bindings=[
            RequirementScoringBinding(
                requirement_id=requirement_id,
                primary_dimension_id=_dimension_for_requirement(requirement_id),
                weight_within_dimension=1.0,
                rubric_id=_dimension_for_requirement(requirement_id),
            )
            for requirement_id in requirement_ids
        ],
    )


def _match_fields(criterion_ids: list[str]) -> dict[str, list[str]]:
    fields = {
        "matched_minimum_criteria": [],
        "matched_excellence_signals": [],
        "matched_critical_errors": [],
        "accepted_alternative_ids": [],
    }
    for criterion_id in criterion_ids:
        if "_min_" in criterion_id:
            fields["matched_minimum_criteria"].append(criterion_id)
        elif "_exc_" in criterion_id:
            fields["matched_excellence_signals"].append(criterion_id)
        elif "_err_" in criterion_id:
            fields["matched_critical_errors"].append(criterion_id)
        elif "_alt_" in criterion_id:
            fields["accepted_alternative_ids"].append(criterion_id)
        else:
            fields["matched_minimum_criteria"].append(criterion_id)
    return fields


def _matches_for(case, *, omit_required: bool = False) -> RubricMatchBatch:
    required_hits = case.expectation.required_rubric_hits
    matches = []
    for evidence in case.evidences:
        requirement_id = evidence.requirement_ids[0]
        criterion_ids = []
        if not omit_required:
            criterion_ids = required_hits.get(requirement_id, [])
        matches.append(
            RubricMatch(
                evidence_id=evidence.id,
                requirement_id=requirement_id,
                **_match_fields(criterion_ids),
                quality=RubricQuality(),
            )
        )
    return RubricMatchBatch(matches=matches)


def _report_for(
    case,
    *,
    level_overrides: dict[str, str] | None = None,
    numeric_unverified_dimensions: set[str] | None = None,
    unknown_evidence: bool = False,
) -> AssessmentReport:
    level_overrides = level_overrides or {}
    numeric_unverified_dimensions = numeric_unverified_dimensions or set()
    expected_levels = case.expectation.requirement_level_ranges
    expected_unverified_requirements = set(
        case.expectation.expected_unverified_requirements
    )
    evidence_by_requirement: dict[str, list[str]] = {}
    for evidence in case.evidences:
        for requirement_id in evidence.requirement_ids:
            evidence_by_requirement.setdefault(requirement_id, []).append(evidence.id)

    assessments = []
    for requirement_id in _requirement_ids(case):
        if requirement_id in expected_unverified_requirements:
            level = "UNVERIFIED"
            coverage = 0.0
            confidence = "low"
        elif requirement_id in expected_levels:
            level = level_overrides.get(
                requirement_id,
                expected_levels[requirement_id].min_level,
            )
            coverage = 1.0
            confidence = "high"
        else:
            level = "UNVERIFIED"
            coverage = 0.0
            confidence = "low"
        assessments.append(
            RequirementEvidenceAssessment(
                requirement_id=requirement_id,
                dimension_id=_dimension_for_requirement(requirement_id),
                level=level,
                coverage=coverage,
                confidence=confidence,
                supporting_evidence_ids=evidence_by_requirement.get(
                    requirement_id, []
                ),
                quality=RubricQuality(),
            )
        )

    expected_unverified_dimensions = set(
        case.expectation.expected_unverified_dimensions
    )
    radar_dimensions = []
    for index in range(1, 7):
        dimension_id = f"role_dim_{index:02d}"
        related_requirements = [
            requirement_id
            for requirement_id in _requirement_ids(case)
            if _dimension_for_requirement(requirement_id) == dimension_id
        ]
        related_evidence_ids = [
            evidence_id
            for requirement_id in related_requirements
            for evidence_id in evidence_by_requirement.get(requirement_id, [])
        ]
        should_be_unverified = dimension_id in expected_unverified_dimensions or not any(
            requirement_id in expected_levels for requirement_id in related_requirements
        )
        if should_be_unverified:
            reason = ScoreReason(
                reason_type="unverified",
                text="该维度没有足够的有效证据。",
            )
            if dimension_id in numeric_unverified_dimensions:
                radar_dimensions.append(
                    RadarDimensionResult.model_construct(
                        dimension_id=dimension_id,
                        name=dimension_id,
                        score=42.0,
                        level="UNVERIFIED",
                        coverage=0.25,
                        confidence="low",
                        score_reasons=[reason],
                        requirement_breakdown=[],
                    )
                )
            else:
                radar_dimensions.append(
                    RadarDimensionResult(
                        dimension_id=dimension_id,
                        name=dimension_id,
                        score=None,
                        level="UNVERIFIED",
                        coverage=0.0,
                        confidence="low",
                        score_reasons=[reason],
                        requirement_breakdown=[],
                    )
                )
            continue

        reason_evidence_ids = related_evidence_ids or [case.evidences[0].id]
        if unknown_evidence and dimension_id == "role_dim_01":
            reason_evidence_ids = ["ev_unknown"]
        reasons = [
            ScoreReason(
                reason_type="strength",
                text="已验证的工程边界。",
                evidence_ids=reason_evidence_ids,
            ),
            ScoreReason(
                reason_type="risk",
                text="仍需在更多场景中复核。",
                evidence_ids=reason_evidence_ids,
            ),
        ]
        dimension_levels = [
            expected_levels[requirement_id].min_level
            for requirement_id in related_requirements
            if requirement_id in expected_levels
        ]
        level = level_overrides.get(
            related_requirements[0],
            dimension_levels[0] if dimension_levels else "L2",
        )
        radar_dimensions.append(
            RadarDimensionResult(
                dimension_id=dimension_id,
                name=dimension_id,
                score=80.0,
                level=level,
                coverage=1.0,
                confidence="high",
                score_reasons=reasons,
                requirement_breakdown=[],
            )
        )

    snapshot_values = {
        "role_family": "ai_application_engineering",
        "role_profile_version": "2026-H2",
        "scoring_engine_version": "test-engine",
        "requirement_assessments": assessments,
        "radar_dimensions": radar_dimensions,
        "job_match": JobMatchResult(
            published=False,
            coverage=0.5,
            confidence="low",
        ),
    }
    if numeric_unverified_dimensions:
        snapshot = ScoreSnapshot.model_construct(**snapshot_values)
    else:
        snapshot = ScoreSnapshot(**snapshot_values)
    return AssessmentReport(
        target_role=case.target_role,
        score_snapshot=snapshot,
        narrative=ReportNarrativeDraft(executive_summary="测试报告摘要"),
    )


class ReportCalibrationAssertionsTest(unittest.TestCase):
    def test_c03_fails_without_required_transfer_question_mode(self) -> None:
        case = get_report_calibration_case("C03")
        without_transfer_turn = case.model_copy(update={"turns": [case.turns[0]]})

        assertions = evaluate_report_calibration(
            without_transfer_turn,
            _blueprint_for(case),
            _matches_for(case),
            _report_for(case),
        )

        by_code = {item.code: item for item in assertions}
        self.assertFalse(by_code["question_mode:req_01:scenario"].passed)

    def test_c03_fails_when_transfer_risk_is_not_limiting_evidence(self) -> None:
        case = get_report_calibration_case("C03")

        assertions = evaluate_report_calibration(
            case,
            _blueprint_for(case),
            _matches_for(case),
            _report_for(case),
        )

        by_code = {item.code: item for item in assertions}
        self.assertFalse(by_code["limiting_evidence:req_01"].passed)

    def test_require_pass_raises_with_failed_codes(self) -> None:
        assertions = [
            CalibrationAssertion(code="evidence_refs", passed=True, message="ok"),
            CalibrationAssertion(
                code="level:req_05",
                passed=False,
                message="expected L0-L1, got L3",
            ),
        ]
        with self.assertRaisesRegex(AssertionError, "level:req_05"):
            require_calibration_pass(assertions)

    def test_c04_accepts_l3_without_every_excellence_signal(self) -> None:
        case = get_report_calibration_case("C04")
        assertions = evaluate_report_calibration(
            case,
            _blueprint_for(case),
            _matches_for(case),
            _report_for(case),
        )

        self.assertTrue(
            all(item.passed for item in assertions),
            msg="; ".join(
                f"{item.code}: {item.message}"
                for item in assertions
                if not item.passed
            ),
        )

    def test_c05_fails_when_critical_error_is_absent(self) -> None:
        case = get_report_calibration_case("C05")
        assertions = evaluate_report_calibration(
            case,
            _blueprint_for(case),
            _matches_for(case, omit_required=True),
            _report_for(case, level_overrides={"req_05": "L0"}),
        )

        by_code = {item.code: item for item in assertions}
        self.assertFalse(by_code["required_hit:req_05"].passed)

    def test_c06_fails_when_unverified_dimension_has_numeric_score(self) -> None:
        case = get_report_calibration_case("C06")
        assertions = evaluate_report_invariants(
            case.evidences,
            _report_for(
                case,
                numeric_unverified_dimensions={"role_dim_03"},
            ),
        )

        by_code = {item.code: item for item in assertions}
        self.assertFalse(by_code["unverified_score:role_dim_03"].passed)

    def test_unknown_evidence_reference_fails(self) -> None:
        case = get_report_calibration_case("C04")
        assertions = evaluate_report_invariants(
            case.evidences,
            _report_for(case, unknown_evidence=True),
        )

        by_code = {item.code: item for item in assertions}
        self.assertFalse(by_code["evidence_refs"].passed)


if __name__ == "__main__":
    unittest.main()
