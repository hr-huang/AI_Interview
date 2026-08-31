"""Pure hard-boundary assertions for report calibration runs.

This module deliberately depends only on calibration contracts and report
schemas.  It does not call semantic services, mutate inputs, or inspect model
output beyond the structured fields that the report pipeline already owns.
"""

from __future__ import annotations

from profile_agent.calibration.schemas import (
    CalibrationAssertion,
    ReportCalibrationCase,
)
from profile_agent.schemas.report_schema import (
    AssessmentReport,
    RubricMatchBatch,
    ScoringBlueprint,
)
from profile_agent.schemas.runtime_schema import Evidence


_LEVEL_ORDER = {
    level: index for index, level in enumerate(("L0", "L1", "L2", "L3", "L4"))
}
_RUBRIC_ID_FIELDS = (
    "matched_minimum_criteria",
    "matched_excellence_signals",
    "matched_critical_errors",
    "accepted_alternative_ids",
)


def _assertion(code: str, passed: bool, message: str) -> CalibrationAssertion:
    return CalibrationAssertion(code=code, passed=passed, message=message)


def _report_evidence_ids(report: AssessmentReport) -> set[str]:
    """Collect every Evidence ID exposed by the structured report."""

    snapshot = report.score_snapshot
    evidence_ids: set[str] = set()

    for assessment in snapshot.requirement_assessments:
        evidence_ids.update(assessment.supporting_evidence_ids)
        evidence_ids.update(assessment.limiting_evidence_ids)
        evidence_ids.update(assessment.transfer_evidence_ids)
        for reason in assessment.assessment_reasons:
            evidence_ids.update(reason.evidence_ids)

    for radar in snapshot.radar_dimensions:
        for reason in radar.score_reasons:
            evidence_ids.update(reason.evidence_ids)

    for reason in snapshot.job_match.limiting_reasons:
        evidence_ids.update(reason.evidence_ids)

    for claim in snapshot.claim_verifications:
        evidence_ids.update(claim.supporting_evidence_ids)
        evidence_ids.update(claim.contradicting_evidence_ids)

    narrative = report.narrative
    for item in (
        *narrative.strengths,
        *narrative.risks,
        *narrative.unverified_areas,
        *narrative.fit_contexts,
    ):
        evidence_ids.update(item.evidence_ids)

    for step in report.interview_path:
        evidence_ids.update(step.evidence_ids)

    return evidence_ids


def evaluate_report_invariants(
    evidences: list[Evidence],
    report: AssessmentReport,
) -> list[CalibrationAssertion]:
    """Check provenance, UNVERIFIED scores, and radar reason counts.

    These checks intentionally do not depend on fixed Requirement or Role
    Dimension IDs, so they can be reused by every calibration case.
    """

    known_evidence_ids = {evidence.id for evidence in evidences}
    referenced_evidence_ids = _report_evidence_ids(report)
    unknown_evidence_ids = sorted(referenced_evidence_ids - known_evidence_ids)
    invariants = [
        _assertion(
            "evidence_refs",
            not unknown_evidence_ids,
            "所有报告 Evidence 引用都存在。"
            if not unknown_evidence_ids
            else "报告引用了未知 Evidence ID: " + ", ".join(unknown_evidence_ids),
        )
    ]

    for radar in report.score_snapshot.radar_dimensions:
        unverified_with_score = (
            radar.level == "UNVERIFIED" and radar.score is not None
        )
        invariants.append(
            _assertion(
                f"unverified_score:{radar.dimension_id}",
                not unverified_with_score,
                f"{radar.dimension_id} 的 UNVERIFIED 结果必须保持 score=None。"
                if unverified_with_score
                else f"{radar.dimension_id} 的 UNVERIFIED score 边界正确。",
            )
        )

        requires_reasons = radar.level != "UNVERIFIED"
        enough_reasons = len(radar.score_reasons) >= 2
        invariants.append(
            _assertion(
                f"radar_reason_count:{radar.dimension_id}",
                not requires_reasons or enough_reasons,
                f"{radar.dimension_id} 的已评分结果有 {len(radar.score_reasons)} 条理由，至少需要 2 条。"
                if requires_reasons and not enough_reasons
                else f"{radar.dimension_id} 的理由数量满足边界。",
            )
        )

    return invariants


def _requirement_ids(case: ReportCalibrationCase) -> list[str]:
    return [
        requirement.id
        for target in case.plan.targets
        for requirement in target.evidence_requirements
    ]


def _matched_rubric_ids(match) -> set[str]:
    return {
        rubric_id
        for field_name in _RUBRIC_ID_FIELDS
        for rubric_id in getattr(match, field_name)
    }


def _rubric_hits_by_requirement(
    rubric_matches: RubricMatchBatch,
) -> dict[str, set[str]]:
    hits_by_requirement: dict[str, set[str]] = {}
    for match in rubric_matches.matches:
        hits_by_requirement.setdefault(match.requirement_id, set()).update(
            _matched_rubric_ids(match)
        )
    return hits_by_requirement


def _assessment_for_requirement(report: AssessmentReport, requirement_id: str):
    matches = [
        item
        for item in report.score_snapshot.requirement_assessments
        if item.requirement_id == requirement_id
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, "缺少 Requirement assessment"
    return None, "存在重复 Requirement assessment"


def _radar_for_dimension(report: AssessmentReport, dimension_id: str):
    matches = [
        item
        for item in report.score_snapshot.radar_dimensions
        if item.dimension_id == dimension_id
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, "缺少 Radar dimension"
    return None, "存在重复 Radar dimension"


def _format_level_range(level_range) -> str:
    if level_range.min_level == level_range.max_level:
        return level_range.min_level
    return f"{level_range.min_level}-{level_range.max_level}"


def evaluate_report_calibration(
    case: ReportCalibrationCase,
    blueprint: ScoringBlueprint,
    rubric_matches: RubricMatchBatch,
    report: AssessmentReport,
) -> list[CalibrationAssertion]:
    """Return all hard-boundary checks without calling an LLM or mutating data."""

    assertions = evaluate_report_invariants(case.evidences, report)
    modes_by_requirement: dict[str, set[str]] = {}
    for turn in case.turns:
        modes_by_requirement.setdefault(
            turn.primary_requirement_id,
            set(),
        ).add(turn.question_mode)
    for requirement_id, required_modes in (
        case.expectation.required_question_modes.items()
    ):
        actual_modes = modes_by_requirement.get(requirement_id, set())
        for required_mode in required_modes:
            passed = required_mode in actual_modes
            assertions.append(
                _assertion(
                    f"question_mode:{requirement_id}:{required_mode}",
                    passed,
                    f"{requirement_id} 已使用 {required_mode} 验证。"
                    if passed
                    else f"{requirement_id} 缺少 {required_mode} 验证。",
                )
            )

    for requirement_id, expected_evidence_ids in (
        case.expectation.required_limiting_evidence_ids.items()
    ):
        assessment, error = _assessment_for_requirement(
            report,
            requirement_id,
        )
        actual_ids = (
            set(assessment.limiting_evidence_ids)
            if assessment is not None
            else set()
        )
        missing_ids = sorted(set(expected_evidence_ids) - actual_ids)
        assertions.append(
            _assertion(
                f"limiting_evidence:{requirement_id}",
                error is None and not missing_ids,
                f"{requirement_id} 包含必需的限制证据。"
                if error is None and not missing_ids
                else f"{requirement_id} 缺少限制证据: "
                + ", ".join(missing_ids),
            )
        )

    expected_requirement_ids = _requirement_ids(case)
    bound_requirement_ids = [
        binding.requirement_id for binding in blueprint.bindings
    ]
    duplicate_requirement_ids = sorted(
        {
            requirement_id
            for requirement_id in bound_requirement_ids
            if bound_requirement_ids.count(requirement_id) > 1
        }
    )
    missing_requirement_ids = sorted(
        set(expected_requirement_ids) - set(bound_requirement_ids)
    )
    unexpected_requirement_ids = sorted(
        set(bound_requirement_ids) - set(expected_requirement_ids)
    )
    blueprint_covered = not (
        duplicate_requirement_ids
        or missing_requirement_ids
        or unexpected_requirement_ids
    ) and len(bound_requirement_ids) == len(expected_requirement_ids)
    blueprint_detail = "蓝图完整覆盖每个 Evidence Requirement。"
    if not blueprint_covered:
        details = []
        if missing_requirement_ids:
            details.append("missing=" + ",".join(missing_requirement_ids))
        if unexpected_requirement_ids:
            details.append("unexpected=" + ",".join(unexpected_requirement_ids))
        if duplicate_requirement_ids:
            details.append("duplicate=" + ",".join(duplicate_requirement_ids))
        blueprint_detail = "蓝图覆盖边界失败: " + "; ".join(details)
    assertions.append(_assertion("blueprint_coverage", blueprint_covered, blueprint_detail))

    hits_by_requirement = _rubric_hits_by_requirement(rubric_matches)
    expectation = case.expectation
    for requirement_id, expected_hits in expectation.required_rubric_hits.items():
        actual_hits = hits_by_requirement.get(requirement_id, set())
        missing_hits = sorted(set(expected_hits) - actual_hits)
        assertions.append(
            _assertion(
                f"required_hit:{requirement_id}",
                not missing_hits,
                f"{requirement_id} 命中全部必需 rubric。"
                if not missing_hits
                else f"{requirement_id} 缺少必需 rubric: " + ", ".join(missing_hits),
            )
        )

    for requirement_id, forbidden_hits in expectation.forbidden_rubric_hits.items():
        actual_hits = hits_by_requirement.get(requirement_id, set())
        matched_forbidden = sorted(set(forbidden_hits) & actual_hits)
        assertions.append(
            _assertion(
                f"forbidden_hit:{requirement_id}",
                not matched_forbidden,
                f"{requirement_id} 未命中禁止 rubric。"
                if not matched_forbidden
                else f"{requirement_id} 命中了禁止 rubric: "
                + ", ".join(matched_forbidden),
            )
        )

    for requirement_id, level_range in expectation.requirement_level_ranges.items():
        assessment, error = _assessment_for_requirement(report, requirement_id)
        actual_level = assessment.level if assessment is not None else None
        passed = (
            error is None
            and actual_level in _LEVEL_ORDER
            and _LEVEL_ORDER[level_range.min_level]
            <= _LEVEL_ORDER[actual_level]
            <= _LEVEL_ORDER[level_range.max_level]
        )
        expected_text = _format_level_range(level_range)
        actual_text = actual_level or error or "missing"
        assertions.append(
            _assertion(
                f"level:{requirement_id}",
                passed,
                f"{requirement_id} 等级在 {expected_text} 范围内。"
                if passed
                else f"{requirement_id} 期望 {expected_text}，实际为 {actual_text}。",
            )
        )

    for requirement_id in expectation.expected_unverified_requirements:
        assessment, error = _assessment_for_requirement(report, requirement_id)
        passed = error is None and assessment is not None and assessment.level == "UNVERIFIED"
        actual_text = assessment.level if assessment is not None else (error or "missing")
        assertions.append(
            _assertion(
                f"unverified_requirement:{requirement_id}",
                passed,
                f"{requirement_id} 保持 UNVERIFIED。"
                if passed
                else f"{requirement_id} 期望 UNVERIFIED，实际为 {actual_text}。",
            )
        )

    for dimension_id in expectation.expected_unverified_dimensions:
        radar, error = _radar_for_dimension(report, dimension_id)
        passed = error is None and radar is not None and radar.level == "UNVERIFIED"
        actual_text = radar.level if radar is not None else (error or "missing")
        assertions.append(
            _assertion(
                f"unverified_dimension:{dimension_id}",
                passed,
                f"{dimension_id} 保持 UNVERIFIED。"
                if passed
                else f"{dimension_id} 期望 UNVERIFIED，实际为 {actual_text}。",
            )
        )

    expected_publication = expectation.job_match_published
    actual_publication = report.score_snapshot.job_match.published
    publication_passed = (
        expected_publication is None
        or actual_publication == expected_publication
    )
    assertions.append(
        _assertion(
            "job_match_publication",
            publication_passed,
            "岗位匹配发布状态不受此案例约束。"
            if expected_publication is None
            else f"岗位匹配发布状态为 {actual_publication}，符合期望 {expected_publication}。"
            if publication_passed
            else f"岗位匹配发布状态期望 {expected_publication}，实际为 {actual_publication}。",
        )
    )

    claim_verifications = {
        item.claim_id: item for item in report.score_snapshot.claim_verifications
    }
    for claim_id, expected_status in expectation.required_claim_statuses.items():
        actual_claim = claim_verifications.get(claim_id)
        actual_status = actual_claim.status if actual_claim is not None else None
        passed = actual_status == expected_status
        assertions.append(
            _assertion(
                f"claim:{claim_id}",
                passed,
                f"Claim {claim_id} 状态为 {expected_status}。"
                if passed
                else f"Claim {claim_id} 期望状态 {expected_status}，实际为 {actual_status or 'missing'}。",
            )
        )

    return assertions


def require_calibration_pass(assertions: list[CalibrationAssertion]) -> None:
    """Raise one actionable error containing every failed assertion code."""

    failures = [item for item in assertions if not item.passed]
    if failures:
        detail = "; ".join(
            f"{item.code}: {item.message}" for item in failures
        )
        raise AssertionError(detail)


__all__ = [
    "evaluate_report_calibration",
    "evaluate_report_invariants",
    "require_calibration_pass",
]
