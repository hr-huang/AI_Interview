"""Deterministic numeric scoring over frozen assessment snapshots."""

from __future__ import annotations

from collections.abc import Iterable
import math

from profile_agent.schemas.report_schema import (
    ClaimVerification,
    CompetencyDimensionRubric,
    JobMatchResult,
    RadarDimensionResult,
    RequirementEvidenceAssessment,
    RequirementScore,
    RoleCompetencyProfile,
    ScoreLevel,
    ScoreReason,
    ScoreSnapshot,
    ScoringBlueprint,
)


class ScoreEngineError(ValueError):
    """Raised when the frozen scoring inputs cannot be reconciled."""


_SCORING_ENGINE_VERSION = "v1"
_LEVEL_BASE = {
    "L0": 20,
    "L1": 40,
    "L2": 65,
    "L3": 82,
    "L4": 95,
}
_LEVEL_RANK = {
    "L0": 0,
    "L1": 1,
    "L2": 2,
    "L3": 3,
    "L4": 4,
}
_LEVEL_BY_RANK: tuple[ScoreLevel, ...] = ("L0", "L1", "L2", "L3", "L4")
_REASON_ORDER = {
    "strength": 0,
    "risk": 1,
    "critical_error": 2,
    "unverified": 3,
}


def _round_score(value: float) -> float:
    return round(value, 6)


def _round_fraction(value: float) -> float:
    return round(value, 10)


def _index_dimensions(
    role_profile: RoleCompetencyProfile,
) -> dict[str, CompetencyDimensionRubric]:
    dimensions: dict[str, CompetencyDimensionRubric] = {}
    for dimension in role_profile.dimensions:
        if dimension.id in dimensions:
            raise ScoreEngineError(f"Role Dimension ID 重复: {dimension.id}")
        dimensions[dimension.id] = dimension
    return dimensions


def _index_bindings(
    role_profile: RoleCompetencyProfile,
    blueprint: ScoringBlueprint,
    dimensions: dict[str, CompetencyDimensionRubric],
) -> tuple[list[object], dict[str, object], dict[str, list[object]]]:
    if blueprint.role_family != role_profile.role_family:
        raise ScoreEngineError(
            "ScoringBlueprint 的 role_family 与 Role Profile 不一致: "
            + blueprint.role_family
        )
    if blueprint.role_profile_version != role_profile.version:
        raise ScoreEngineError(
            "ScoringBlueprint 的 Role Profile version 不一致: "
            + blueprint.role_profile_version
        )

    bindings_by_requirement: dict[str, object] = {}
    bindings_by_dimension: dict[str, list[object]] = {
        dimension_id: [] for dimension_id in dimensions
    }
    ordered_bindings: list[object] = []

    for binding in blueprint.bindings:
        if binding.requirement_id in bindings_by_requirement:
            raise ScoreEngineError(
                f"ScoringBlueprint binding 重复: {binding.requirement_id}"
            )
        dimension = dimensions.get(binding.primary_dimension_id)
        if dimension is None:
            raise ScoreEngineError(
                "ScoringBlueprint 引用了不存在的 Role Dimension: "
                + binding.primary_dimension_id
            )
        if binding.rubric_id != dimension.id:
            raise ScoreEngineError(
                "ScoringBlueprint 的 rubric_id 不属于绑定的 Role Dimension: "
                + binding.rubric_id
            )
        if not 0 < binding.weight_within_dimension <= 1:
            raise ScoreEngineError(
                f"ScoringBlueprint 的 Requirement 权重无效: {binding.requirement_id}"
            )

        ordered_bindings.append(binding)
        bindings_by_requirement[binding.requirement_id] = binding
        bindings_by_dimension[binding.primary_dimension_id].append(binding)

    return ordered_bindings, bindings_by_requirement, bindings_by_dimension


def _ordered_assessments(
    assessments: Iterable[RequirementEvidenceAssessment],
    ordered_bindings: list[object],
    bindings_by_requirement: dict[str, object],
) -> tuple[list[RequirementEvidenceAssessment], dict[str, RequirementEvidenceAssessment]]:
    by_requirement: dict[str, RequirementEvidenceAssessment] = {}
    for assessment in assessments:
        if assessment.requirement_id not in bindings_by_requirement:
            raise ScoreEngineError(
                "RequirementEvidenceAssessment 引用了不存在的 Requirement: "
                + assessment.requirement_id
            )
        if assessment.requirement_id in by_requirement:
            raise ScoreEngineError(
                "RequirementEvidenceAssessment 重复: " + assessment.requirement_id
            )
        binding = bindings_by_requirement[assessment.requirement_id]
        if assessment.dimension_id != binding.primary_dimension_id:
            raise ScoreEngineError(
                "RequirementEvidenceAssessment 的 dimension_id 与 Blueprint 不一致: "
                + assessment.requirement_id
            )
        by_requirement[assessment.requirement_id] = assessment

    ordered = [
        by_requirement[binding.requirement_id]
        for binding in ordered_bindings
        if binding.requirement_id in by_requirement
    ]
    return ordered, by_requirement


def score_requirement(
    assessment: RequirementEvidenceAssessment,
    dimension: CompetencyDimensionRubric,
) -> RequirementScore | None:
    """Map one frozen level to its bounded numeric Requirement result."""

    if assessment.dimension_id != dimension.id:
        raise ScoreEngineError(
            "RequirementEvidenceAssessment 的 dimension_id 与 Role Dimension 不一致: "
            + assessment.requirement_id
        )
    if assessment.level == "UNVERIFIED":
        return None

    adjustment_ids = sorted(
        set(
            assessment.matched_excellence_signal_ids
            + assessment.unresolved_critical_error_ids
        )
    )
    adjustment_by_id = {
        item.id: item.score_adjustment
        for item in dimension.excellence_signals + dimension.critical_errors
    }
    unknown_adjustment_ids = sorted(set(adjustment_ids) - set(adjustment_by_id))
    if unknown_adjustment_ids:
        raise ScoreEngineError(
            "RequirementEvidenceAssessment 引用了未知调整信号: "
            + ", ".join(unknown_adjustment_ids)
        )

    base_score = _LEVEL_BASE[assessment.level]
    adjustment = max(
        -5,
        min(5, sum(adjustment_by_id[item_id] for item_id in adjustment_ids)),
    )
    display_score = max(0, min(100, base_score + adjustment))
    return RequirementScore(
        requirement_id=assessment.requirement_id,
        dimension_id=assessment.dimension_id,
        base_score=base_score,
        adjustment=adjustment,
        display_score=display_score,
    )


def _canonical_reason(reason: ScoreReason) -> ScoreReason:
    return ScoreReason(
        reason_type=reason.reason_type,
        text=reason.text,
        evidence_ids=sorted(set(reason.evidence_ids)),
        rubric_signal_ids=sorted(set(reason.rubric_signal_ids)),
    )


def _reason_key(reason: ScoreReason) -> tuple[object, ...]:
    return (
        _REASON_ORDER[reason.reason_type],
        reason.text,
        tuple(reason.evidence_ids),
        tuple(reason.rubric_signal_ids),
    )


def _unverified_reason(text: str) -> ScoreReason:
    return ScoreReason(reason_type="unverified", text=text)


def _dimension_reasons(
    dimension: CompetencyDimensionRubric,
    assessments: list[RequirementEvidenceAssessment],
    *,
    scored: bool,
    coverage: float,
) -> list[ScoreReason]:
    unique: dict[tuple[object, ...], ScoreReason] = {}
    for assessment in assessments:
        for reason in assessment.assessment_reasons:
            normalized = _canonical_reason(reason)
            unique[_reason_key(normalized)] = normalized

    reasons = sorted(unique.values(), key=_reason_key)
    if not scored:
        if not reasons:
            reasons.append(
                _unverified_reason(
                    f"维度“{dimension.name}”当前尚未得到充分验证，暂不生成数值分数。"
                )
            )
        return reasons

    if len(reasons) < 2:
        if coverage < 1.0:
            reasons.append(
                _unverified_reason(
                    f"维度“{dimension.name}”的加权覆盖率为 {coverage:.0%}，"
                    "未验证项不按零分计入。"
                )
            )
        else:
            reasons.append(
                _unverified_reason(
                    f"维度“{dimension.name}”的数值分数由已验证 Requirement 聚合，"
                    "更高阶表现仍需独立观察。"
                )
            )

    if len(reasons) < 2:
        reasons.append(
            _unverified_reason(
                f"维度“{dimension.name}”仍有可观察细节未形成独立评分原因。"
            )
        )
    return reasons


def _dimension_level(
    members: list[tuple[object, RequirementEvidenceAssessment, RequirementScore]],
    dimension: CompetencyDimensionRubric,
) -> ScoreLevel:
    if not members:
        return "UNVERIFIED"

    assessments = [assessment for _, assessment, _ in members]
    if any(
        assessment.level == "L0" or assessment.unresolved_critical_error_ids
        for assessment in assessments
    ):
        return "L0"

    satisfied_minimum_ids = {
        criterion_id
        for assessment in assessments
        for criterion_id in assessment.satisfied_minimum_criterion_ids
    }
    if satisfied_minimum_ids:
        required_minimum_ids = {
            criterion.id for criterion in dimension.minimum_criteria
        }
        if not required_minimum_ids.issubset(satisfied_minimum_ids):
            return "L1"
        if any(assessment.level == "L4" for assessment in assessments):
            return "L4"
        if any(
            assessment.level == "L3"
            or assessment.matched_excellence_signal_ids
            for assessment in assessments
        ):
            return "L3"
        return "L2"

    total_weight = sum(binding.weight_within_dimension for binding, _, _ in members)
    weighted_rank = sum(
        _LEVEL_RANK[assessment.level] * binding.weight_within_dimension
        for binding, assessment, _ in members
    ) / total_weight
    rank = max(0, min(4, math.floor(weighted_rank + 1e-9)))
    return _LEVEL_BY_RANK[rank]


def _collective_dimension_score(
    members: list[tuple[object, RequirementEvidenceAssessment, RequirementScore]],
    dimension: CompetencyDimensionRubric,
    level: ScoreLevel,
) -> float | None:
    if not members or level == "UNVERIFIED":
        return None

    assessments = [assessment for _, assessment, _ in members]
    if not any(
        assessment.satisfied_minimum_criterion_ids for assessment in assessments
    ):
        verified_weight = sum(
            binding.weight_within_dimension for binding, _, _ in members
        )
        return _round_score(
            sum(
                score.display_score * binding.weight_within_dimension
                for binding, _, score in members
            )
            / verified_weight
        )

    adjustment_by_id = {
        item.id: item.score_adjustment
        for item in dimension.excellence_signals + dimension.critical_errors
    }
    adjustment_ids = {
        signal_id
        for assessment in assessments
        for signal_id in (
            assessment.matched_excellence_signal_ids
            + assessment.unresolved_critical_error_ids
        )
    }
    adjustment = max(
        -5,
        min(5, sum(adjustment_by_id[item_id] for item_id in adjustment_ids)),
    )
    return _round_score(max(0, min(100, _LEVEL_BASE[level] + adjustment)))


def _dimension_confidence(
    assessments: list[RequirementEvidenceAssessment],
    coverage: float,
    scored: bool,
) -> str:
    if not scored or coverage < 0.60:
        return "low"
    if any(assessment.confidence == "low" for assessment in assessments):
        return "low"
    scored_assessments = [
        assessment for assessment in assessments if assessment.level != "UNVERIFIED"
    ]
    if (
        coverage >= 0.80
        and scored_assessments
        and all(assessment.confidence == "high" for assessment in scored_assessments)
    ):
        return "high"
    return "medium"


def _job_confidence(
    radar_dimensions: list[RadarDimensionResult],
    coverage: float,
) -> str:
    scored = [radar for radar in radar_dimensions if radar.score is not None]
    if not scored or coverage < 0.60:
        return "low"
    if any(radar.confidence == "low" for radar in scored):
        return "low"
    if coverage >= 0.80 and all(radar.confidence == "high" for radar in scored):
        return "high"
    return "medium"


def _fit_level(raw_score: float) -> str:
    if raw_score >= 85:
        return "高度匹配"
    if raw_score >= 70:
        return "较高匹配"
    if raw_score >= 55:
        return "有条件匹配"
    if raw_score >= 40:
        return "当前匹配度较低"
    return "存在明显岗位风险"


def _deduplicate_reasons(reasons: Iterable[ScoreReason]) -> list[ScoreReason]:
    unique: dict[tuple[object, ...], ScoreReason] = {}
    for reason in reasons:
        normalized = _canonical_reason(reason)
        unique[_reason_key(normalized)] = normalized
    return sorted(unique.values(), key=_reason_key)


def _gating_error_reasons(
    dimension: CompetencyDimensionRubric,
    assessments: list[RequirementEvidenceAssessment],
) -> list[ScoreReason]:
    l0_assessments = [
        assessment
        for assessment in assessments
        if assessment.level == "L0"
    ]
    if not l0_assessments:
        return []

    evidence_ids = sorted(
        {
            evidence_id
            for assessment in l0_assessments
            for evidence_id in assessment.limiting_evidence_ids
        }
        | {
            evidence_id
            for assessment in l0_assessments
            for reason in assessment.assessment_reasons
            for evidence_id in reason.evidence_ids
            if reason.reason_type in {"critical_error", "risk"}
        }
    )
    rubric_signal_ids = sorted(
        {
            signal_id
            for assessment in l0_assessments
            for signal_id in assessment.unresolved_critical_error_ids
        }
        | {
            signal_id
            for assessment in l0_assessments
            for reason in assessment.assessment_reasons
            for signal_id in reason.rubric_signal_ids
            if reason.reason_type in {"critical_error", "risk"}
        }
    )

    if not evidence_ids:
        return [
            _unverified_reason(
                f"门槛维度“{dimension.name}”存在 L0，适配等级最高限制为有条件匹配。"
            )
        ]
    return [
        ScoreReason(
            reason_type="critical_error",
            text=(
                f"门槛维度“{dimension.name}”存在 L0，"
                "适配等级最高限制为有条件匹配。"
            ),
            evidence_ids=evidence_ids,
            rubric_signal_ids=rubric_signal_ids,
        )
    ]


def calculate_score_snapshot(
    role_profile: RoleCompetencyProfile,
    blueprint: ScoringBlueprint,
    assessments: list[RequirementEvidenceAssessment],
    claim_verifications: list[ClaimVerification] | tuple[ClaimVerification, ...] = (),
) -> ScoreSnapshot:
    """Calculate the complete numeric snapshot from frozen report inputs."""

    dimensions = _index_dimensions(role_profile)
    ordered_bindings, bindings_by_requirement, bindings_by_dimension = _index_bindings(
        role_profile,
        blueprint,
        dimensions,
    )
    ordered_assessments, assessments_by_requirement = _ordered_assessments(
        assessments,
        ordered_bindings,
        bindings_by_requirement,
    )

    requirement_scores: list[RequirementScore] = []
    radar_dimensions: list[RadarDimensionResult] = []
    assessments_by_dimension: dict[str, list[RequirementEvidenceAssessment]] = {
        dimension_id: [] for dimension_id in dimensions
    }
    scores_by_requirement: dict[str, RequirementScore] = {}

    for binding in ordered_bindings:
        assessment = assessments_by_requirement.get(binding.requirement_id)
        if assessment is not None:
            assessments_by_dimension[binding.primary_dimension_id].append(assessment)
        if assessment is None:
            continue
        score = score_requirement(
            assessment,
            dimensions[binding.primary_dimension_id],
        )
        if score is not None:
            requirement_scores.append(score)
            scores_by_requirement[binding.requirement_id] = score

    for dimension in role_profile.dimensions:
        dimension_bindings = bindings_by_dimension[dimension.id]
        dimension_assessments = assessments_by_dimension[dimension.id]
        members: list[
            tuple[object, RequirementEvidenceAssessment, RequirementScore]
        ] = []
        for binding in dimension_bindings:
            assessment = assessments_by_requirement.get(binding.requirement_id)
            score = scores_by_requirement.get(binding.requirement_id)
            if assessment is not None and score is not None:
                members.append((binding, assessment, score))

        total_weight = sum(
            binding.weight_within_dimension for binding in dimension_bindings
        )
        verified_weight = sum(
            binding.weight_within_dimension for binding, _, _ in members
        )
        coverage = (
            _round_fraction(verified_weight / total_weight)
            if total_weight
            else 0.0
        )
        level = _dimension_level(members, dimension)
        score_value = _collective_dimension_score(members, dimension, level)
        confidence = _dimension_confidence(
            dimension_assessments,
            coverage,
            bool(members),
        )
        reasons = _dimension_reasons(
            dimension,
            dimension_assessments,
            scored=bool(members),
            coverage=coverage,
        )
        radar_dimensions.append(
            RadarDimensionResult(
                dimension_id=dimension.id,
                name=dimension.name,
                score=score_value,
                level=level,
                coverage=coverage,
                confidence=confidence,
                score_reasons=reasons,
                requirement_breakdown=[item for _, _, item in members],
            )
        )

    radar_by_dimension = {
        radar.dimension_id: radar for radar in radar_dimensions
    }
    role_coverage = _round_fraction(
        sum(
            dimension.weight * radar.coverage
            for dimension, radar in zip(role_profile.dimensions, radar_dimensions)
        )
    )
    verified_role_weight = sum(
        dimension.weight
        for dimension, radar in zip(role_profile.dimensions, radar_dimensions)
        if radar.score is not None
    )
    weighted_score_total = sum(
        dimension.weight * radar.score
        for dimension, radar in zip(role_profile.dimensions, radar_dimensions)
        if radar.score is not None
    )
    raw_score = (
        _round_score(weighted_score_total / verified_role_weight)
        if verified_role_weight
        else None
    )
    gating_dimensions = [
        (dimension, radar)
        for dimension, radar in zip(role_profile.dimensions, radar_dimensions)
        if dimension.is_gating
    ]
    gating_verified = all(radar.level != "UNVERIFIED" for _, radar in gating_dimensions)
    gating_l0 = any(
        assessment.level == "L0"
        for dimension, _ in gating_dimensions
        for binding in bindings_by_dimension[dimension.id]
        for assessment in [assessments_by_requirement.get(binding.requirement_id)]
        if assessment is not None
    )
    published = role_coverage >= 0.70 and gating_verified and raw_score is not None

    limiting_reasons: list[ScoreReason] = []
    if role_coverage < 0.70:
        limiting_reasons.append(
            _unverified_reason("岗位加权覆盖率低于 70%，岗位匹配分暂不计算。")
        )
    if not gating_verified:
        for dimension, radar in gating_dimensions:
            if radar.level == "UNVERIFIED":
                limiting_reasons.append(
                    _unverified_reason(
                        f"门槛维度“{dimension.name}”尚未得到有效评估，"
                        "岗位匹配分暂不计算。"
                    )
                )
    for dimension, _ in gating_dimensions:
        gating_assessments = [
            assessment
            for binding in bindings_by_dimension[dimension.id]
            if (assessment := assessments_by_requirement.get(binding.requirement_id))
            is not None
        ]
        limiting_reasons.extend(
            _gating_error_reasons(dimension, gating_assessments)
        )
    limiting_reasons = _deduplicate_reasons(limiting_reasons)

    fit_level = None
    if published:
        fit_level = _fit_level(raw_score)
        if gating_l0 and fit_level == "高度匹配":
            fit_level = "有条件匹配"

    job_match = JobMatchResult(
        raw_score=raw_score if published else None,
        published=published,
        fit_level=fit_level,
        coverage=role_coverage,
        confidence=_job_confidence(radar_dimensions, role_coverage),
        limiting_reasons=limiting_reasons,
    )

    normalized_claims = [
        ClaimVerification.model_validate(item)
        for item in (claim_verifications or ())
    ]
    return ScoreSnapshot(
        role_family=role_profile.role_family,
        role_profile_version=role_profile.version,
        scoring_engine_version=_SCORING_ENGINE_VERSION,
        requirement_assessments=ordered_assessments,
        requirement_scores=requirement_scores,
        radar_dimensions=radar_dimensions,
        job_match=job_match,
        claim_verifications=normalized_claims,
    )


__all__ = [
    "ScoreEngineError",
    "calculate_score_snapshot",
    "score_requirement",
]
