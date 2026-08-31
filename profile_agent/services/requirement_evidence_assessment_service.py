"""Build deterministic, provenance-safe Requirement evidence assessments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from profile_agent.schemas.report_schema import (
    CompetencyDimensionRubric,
    RequirementEvidenceAssessment,
    RoleCompetencyProfile,
    RubricMatch,
    RubricMatchBatch,
    RubricQuality,
    ScoreLevel,
    ScoreReason,
    ScoringBlueprint,
)
from profile_agent.schemas.runtime_schema import Evidence, InterviewTurn


class RequirementEvidenceAssessmentError(ValueError):
    """Raised when an assessment input cannot be proven to be well-founded."""


@dataclass(frozen=True)
class ValidatedEvidenceMatch:
    """A match whose evidence and interview-turn references are resolved."""

    match: RubricMatch
    evidence: Evidence
    turn: InterviewTurn


_QUALITY_RANK = {
    "unverified": 0,
    "weak": 1,
    "medium": 2,
    "strong": 3,
}
_QUALITY_AXES = (
    "correctness",
    "specificity",
    "reasoning",
    "tradeoff_awareness",
    "transferability",
)
_MATCH_FIELDS = (
    "matched_minimum_criteria",
    "matched_excellence_signals",
    "matched_critical_errors",
    "accepted_alternative_ids",
)


def _index_by_id(items: Iterable[object], item_kind: str) -> dict[str, object]:
    indexed: dict[str, object] = {}
    for item in items:
        item_id = getattr(item, "id", None)
        if not isinstance(item_id, str) or not item_id.strip():
            raise RequirementEvidenceAssessmentError(
                f"{item_kind} 必须有非空 ID"
            )
        if item_id in indexed:
            raise RequirementEvidenceAssessmentError(
                f"{item_kind} ID 重复: {item_id}"
            )
        indexed[item_id] = item
    return indexed


def _profile_dimensions(
    role_profile: RoleCompetencyProfile,
) -> dict[str, CompetencyDimensionRubric]:
    dimensions = _index_by_id(role_profile.dimensions, "Role Dimension")
    result: dict[str, CompetencyDimensionRubric] = {}
    for dimension_id, raw_dimension in dimensions.items():
        dimension = raw_dimension
        assert isinstance(dimension, CompetencyDimensionRubric)
        rubric_items = (
            dimension.minimum_criteria
            + dimension.excellence_signals
            + dimension.critical_errors
            + dimension.accepted_alternatives
        )
        rubric_ids = [item.id for item in rubric_items]
        if len(rubric_ids) != len(set(rubric_ids)):
            raise RequirementEvidenceAssessmentError(
                f"Role Dimension 的 Rubric ID 重复: {dimension_id}"
            )
        result[dimension_id] = dimension
    return result


def _blueprint_bindings(
    blueprint: ScoringBlueprint,
    role_profile: RoleCompetencyProfile,
    dimensions: dict[str, CompetencyDimensionRubric],
) -> tuple[list[object], dict[str, object]]:
    if blueprint.role_family != role_profile.role_family:
        raise RequirementEvidenceAssessmentError(
            "ScoringBlueprint 的 role_family 与 Role Pack 不一致: "
            f"{blueprint.role_family}"
        )
    if blueprint.role_profile_version != role_profile.version:
        raise RequirementEvidenceAssessmentError(
            "ScoringBlueprint 的 Role Pack version 不一致: "
            f"{blueprint.role_profile_version}"
        )

    ordered_bindings: list[object] = []
    bindings_by_requirement: dict[str, object] = {}
    for binding in blueprint.bindings:
        requirement_id = binding.requirement_id
        if not isinstance(requirement_id, str) or not requirement_id.strip():
            raise RequirementEvidenceAssessmentError(
                "ScoringBlueprint 的 requirement_id 必须非空"
            )
        if requirement_id in bindings_by_requirement:
            raise RequirementEvidenceAssessmentError(
                f"ScoringBlueprint binding 重复: {requirement_id}"
            )
        dimension = dimensions.get(binding.primary_dimension_id)
        if dimension is None:
            raise RequirementEvidenceAssessmentError(
                "ScoringBlueprint 引用了不存在的 Role Dimension: "
                f"{binding.primary_dimension_id}"
            )
        if binding.rubric_id != dimension.id:
            raise RequirementEvidenceAssessmentError(
                "ScoringBlueprint 的 rubric_id 不属于绑定的 Role Dimension: "
                f"{binding.rubric_id}"
            )
        if not 0 < binding.weight_within_dimension <= 1:
            raise RequirementEvidenceAssessmentError(
                f"ScoringBlueprint 的 Requirement 权重无效: {requirement_id}"
            )
        ordered_bindings.append(binding)
        bindings_by_requirement[requirement_id] = binding
    return ordered_bindings, bindings_by_requirement


def _turn_index(
    turns: list[InterviewTurn],
    requirement_ids: set[str],
) -> dict[str, InterviewTurn]:
    turns_by_id = _index_by_id(turns, "InterviewTurn")
    result: dict[str, InterviewTurn] = {}
    for turn_id, raw_turn in turns_by_id.items():
        turn = raw_turn
        assert isinstance(turn, InterviewTurn)
        if turn.primary_requirement_id not in requirement_ids:
            raise RequirementEvidenceAssessmentError(
                "InterviewTurn 引用了不存在的 blueprint requirement_id: "
                f"{turn.primary_requirement_id}"
            )
        result[turn_id] = turn
    return result


def _evidence_index(
    evidences: list[Evidence],
    turns_by_id: dict[str, InterviewTurn],
    requirement_ids: set[str],
) -> dict[str, Evidence]:
    evidences_by_id = _index_by_id(evidences, "Evidence")
    result: dict[str, Evidence] = {}
    for evidence_id, raw_evidence in evidences_by_id.items():
        evidence = raw_evidence
        assert isinstance(evidence, Evidence)
        if evidence.turn_id not in turns_by_id:
            raise RequirementEvidenceAssessmentError(
                "Evidence 引用了不存在的 turn_id: "
                f"{evidence.turn_id}"
            )
        if len(evidence.requirement_ids) != len(set(evidence.requirement_ids)):
            raise RequirementEvidenceAssessmentError(
                f"Evidence 的 requirement_ids 重复: {evidence_id}"
            )
        unknown_requirement_ids = sorted(
            set(evidence.requirement_ids) - requirement_ids
        )
        if unknown_requirement_ids:
            raise RequirementEvidenceAssessmentError(
                "Evidence 引用了不存在的 blueprint requirement_id: "
                + ", ".join(unknown_requirement_ids)
            )
        result[evidence_id] = evidence
    return result


def _validate_id_list(
    values: list[str],
    allowed: set[str],
    field_name: str,
    match: RubricMatch,
) -> None:
    if len(values) != len(set(values)):
        raise RequirementEvidenceAssessmentError(
            f"{field_name} ID 重复: {match.evidence_id}"
        )
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise RequirementEvidenceAssessmentError(
            f"{field_name} 引用了不存在的 Rubric ID: "
            + ", ".join(unknown)
        )


def _validate_matches(
    match_batch: RubricMatchBatch,
    evidences_by_id: dict[str, Evidence],
    turns_by_id: dict[str, InterviewTurn],
    bindings_by_requirement: dict[str, object],
    dimensions: dict[str, CompetencyDimensionRubric],
) -> dict[str, list[ValidatedEvidenceMatch]]:
    records_by_requirement: dict[str, list[ValidatedEvidenceMatch]] = {
        requirement_id: [] for requirement_id in bindings_by_requirement
    }
    seen_pairs: set[tuple[str, str]] = set()

    for match in match_batch.matches:
        evidence = evidences_by_id.get(match.evidence_id)
        if evidence is None:
            raise RequirementEvidenceAssessmentError(
                "RubricMatch 引用了不存在的 evidence_id: "
                f"{match.evidence_id}"
            )
        if match.requirement_id not in bindings_by_requirement:
            raise RequirementEvidenceAssessmentError(
                "RubricMatch 引用了不存在的 blueprint requirement_id: "
                f"{match.requirement_id}"
            )
        pair = (match.evidence_id, match.requirement_id)
        if pair in seen_pairs:
            raise RequirementEvidenceAssessmentError(
                "RubricMatch 的 (evidence_id, requirement_id) 重复: "
                f"{match.evidence_id}, {match.requirement_id}"
            )
        seen_pairs.add(pair)
        if match.requirement_id not in evidence.requirement_ids:
            raise RequirementEvidenceAssessmentError(
                "RubricMatch 的 requirement 不属于 Evidence.requirement_ids: "
                f"{match.evidence_id} -> {match.requirement_id}"
            )

        binding = bindings_by_requirement[match.requirement_id]
        dimension = dimensions[binding.primary_dimension_id]
        _validate_id_list(
            match.matched_minimum_criteria,
            {item.id for item in dimension.minimum_criteria},
            "matched_minimum_criteria",
            match,
        )
        _validate_id_list(
            match.matched_excellence_signals,
            {item.id for item in dimension.excellence_signals},
            "matched_excellence_signals",
            match,
        )
        _validate_id_list(
            match.matched_critical_errors,
            {item.id for item in dimension.critical_errors},
            "matched_critical_errors",
            match,
        )
        _validate_id_list(
            match.accepted_alternative_ids,
            {item.id for item in dimension.accepted_alternatives},
            "accepted_alternative_ids",
            match,
        )
        if (
            match.matched_critical_errors
            and evidence.polarity != "contradicting"
        ):
            raise RequirementEvidenceAssessmentError(
                "critical error 必须有明确的 contradicting Evidence: "
                f"{match.evidence_id}"
            )

        records_by_requirement[match.requirement_id].append(
            ValidatedEvidenceMatch(
                match=match,
                evidence=evidence,
                turn=turns_by_id[evidence.turn_id],
            )
        )

    for records in records_by_requirement.values():
        records.sort(key=lambda record: record.evidence.id)
    return records_by_requirement


def _has_rubric_hit(record: ValidatedEvidenceMatch) -> bool:
    return any(getattr(record.match, field) for field in _MATCH_FIELDS)


def _supporting_records(
    records: list[ValidatedEvidenceMatch],
) -> list[ValidatedEvidenceMatch]:
    return [
        record
        for record in records
        if record.evidence.polarity == "supporting"
        and (
            record.match.matched_minimum_criteria
            or record.match.matched_excellence_signals
            or record.match.accepted_alternative_ids
        )
    ]


def _limiting_records(
    records: list[ValidatedEvidenceMatch],
) -> list[ValidatedEvidenceMatch]:
    return [
        record
        for record in records
        if record.evidence.polarity == "contradicting" and _has_rubric_hit(record)
    ]


def _content_key(record: ValidatedEvidenceMatch) -> tuple[str, str, str, str]:
    return (
        record.turn.question_mode,
        record.evidence.polarity,
        record.evidence.observation.strip(),
        record.evidence.source_excerpt.strip(),
    )


def _deduplicate_content(
    records: list[ValidatedEvidenceMatch],
) -> list[ValidatedEvidenceMatch]:
    unique: dict[tuple[str, str, str, str], ValidatedEvidenceMatch] = {}
    for record in sorted(records, key=lambda item: item.evidence.id):
        unique.setdefault(_content_key(record), record)
    return list(unique.values())


def aggregate_quality(records: list[ValidatedEvidenceMatch]) -> RubricQuality:
    """Take the highest verified quality value on every axis."""

    supporting = [
        record for record in records if record.evidence.polarity == "supporting"
    ]
    values: dict[str, str] = {}
    for axis in _QUALITY_AXES:
        candidates = [getattr(record.match.quality, axis) for record in supporting]
        values[axis] = (
            max(candidates, key=_QUALITY_RANK.__getitem__)
            if candidates
            else "unverified"
        )
    return RubricQuality(**values)


def is_independent_transfer(
    record: ValidatedEvidenceMatch,
    original_modes: set[str],
) -> bool:
    """Return whether a supporting record proves successful mode transfer."""

    return (
        record.evidence.polarity == "supporting"
        and record.match.quality.transferability == "strong"
        and record.turn.question_mode not in original_modes
    )


def determine_assessment_level(
    *,
    has_valid_match: bool,
    has_unresolved_critical_error: bool,
    minimum_sufficiency_met: bool,
    quality: RubricQuality,
    has_independent_transfer_success: bool,
) -> ScoreLevel:
    """Apply the frozen L0-L4 gates without producing a numeric score."""

    if not has_valid_match:
        return "UNVERIFIED"
    if has_unresolved_critical_error:
        return "L0"
    if not minimum_sufficiency_met:
        return "L1"
    strong_depth_axes = sum(
        value == "strong"
        for value in (
            quality.specificity,
            quality.reasoning,
            quality.tradeoff_awareness,
        )
    )
    meets_l3 = quality.correctness == "strong" and strong_depth_axes >= 2
    if meets_l3 and has_independent_transfer_success:
        return "L4"
    if meets_l3:
        return "L3"
    return "L2"


def _confidence(
    *,
    coverage: float,
    unique_supporting: list[ValidatedEvidenceMatch],
    satisfied_minimum_ids: set[str],
    conflict_exists: bool,
) -> str:
    if coverage < 0.60:
        return "low"
    if (
        len(unique_supporting) == 1
        and unique_supporting[0].evidence.strength == "weak"
    ):
        return "low"

    supporting_modes = {
        record.turn.question_mode for record in unique_supporting
    }
    core_criterion_is_weak = False
    for criterion_id in satisfied_minimum_ids:
        criterion_records = [
            record
            for record in unique_supporting
            if criterion_id in record.match.matched_minimum_criteria
        ]
        if criterion_records and all(
            record.evidence.strength == "weak" for record in criterion_records
        ):
            core_criterion_is_weak = True
            break

    if (
        coverage >= 0.80
        and len(supporting_modes) >= 2
        and not conflict_exists
        and not core_criterion_is_weak
    ):
        return "high"
    return "medium"


def _reason(
    reason_type: str,
    text: str,
    *,
    evidence_ids: Iterable[str] = (),
    rubric_signal_ids: Iterable[str] = (),
) -> ScoreReason:
    return ScoreReason(
        reason_type=reason_type,
        text=text,
        evidence_ids=sorted(set(evidence_ids)),
        rubric_signal_ids=sorted(set(rubric_signal_ids)),
    )


def _build_reasons(
    *,
    valid_records: list[ValidatedEvidenceMatch],
    supporting: list[ValidatedEvidenceMatch],
    limiting: list[ValidatedEvidenceMatch],
    satisfied_minimum_ids: set[str],
    minimum_ids: set[str],
    matched_excellence_ids: set[str],
    accepted_alternative_ids: set[str],
) -> list[ScoreReason]:
    if not valid_records:
        return [
            _reason(
                "unverified",
                "没有经过验证的 RubricMatch，当前 Requirement 尚未得到充分验证。",
            )
        ]

    reasons: list[ScoreReason] = []
    support_signal_ids = (
        satisfied_minimum_ids | matched_excellence_ids | accepted_alternative_ids
    )
    if supporting and support_signal_ids:
        reasons.append(
            _reason(
                "strength",
                "支持证据命中了最低充分条件、优秀信号或可接受替代方案。",
                evidence_ids=(record.evidence.id for record in supporting),
                rubric_signal_ids=support_signal_ids,
            )
        )

    critical_records = [
        record for record in limiting if record.match.matched_critical_errors
    ]
    if critical_records:
        reasons.append(
            _reason(
                "critical_error",
                "存在 Evidence 明确命中的关键错误，需作为限制条件保留。",
                evidence_ids=(record.evidence.id for record in critical_records),
                rubric_signal_ids=(
                    signal_id
                    for record in critical_records
                    for signal_id in record.match.matched_critical_errors
                ),
            )
        )

    noncritical_limiting = [
        record for record in limiting if not record.match.matched_critical_errors
    ]
    if noncritical_limiting:
        reasons.append(
            _reason(
                "risk",
                "存在带有明确 Rubric 命中的反向 Evidence，当前结论受到限制。",
                evidence_ids=(
                    record.evidence.id for record in noncritical_limiting
                ),
                rubric_signal_ids=(
                    signal_id
                    for record in noncritical_limiting
                    for field in _MATCH_FIELDS
                    for signal_id in getattr(record.match, field)
                ),
            )
        )

    missing_minimum_ids = sorted(minimum_ids - satisfied_minimum_ids)
    if missing_minimum_ids and not accepted_alternative_ids:
        reasons.append(
            _reason(
                "unverified",
                "最低充分条件仍有未验证项: " + ", ".join(missing_minimum_ids),
            )
        )
    return reasons


def _build_one_assessment(
    binding: object,
    dimension: CompetencyDimensionRubric,
    records: list[ValidatedEvidenceMatch],
) -> RequirementEvidenceAssessment:
    valid_records = [record for record in records if _has_rubric_hit(record)]
    supporting = _supporting_records(valid_records)
    limiting = _limiting_records(valid_records)
    unique_supporting = _deduplicate_content(supporting)

    satisfied_minimum_ids = {
        criterion_id
        for record in supporting
        for criterion_id in record.match.matched_minimum_criteria
    }
    matched_excellence_ids = {
        signal_id
        for record in supporting
        for signal_id in record.match.matched_excellence_signals
    }
    accepted_alternative_ids = {
        alternative_id
        for record in supporting
        for alternative_id in record.match.accepted_alternative_ids
    }
    unresolved_critical_error_ids = {
        error_id
        for record in limiting
        for error_id in record.match.matched_critical_errors
    }

    minimum_ids = {criterion.id for criterion in dimension.minimum_criteria}
    minimum_sufficiency_met = (
        minimum_ids <= satisfied_minimum_ids or bool(accepted_alternative_ids)
    )
    coverage = min(
        1.0,
        max(0.0, len(satisfied_minimum_ids) / len(minimum_ids)),
    )
    quality = aggregate_quality(valid_records)

    base_records = [
        record
        for record in unique_supporting
        if record.match.quality.transferability != "strong"
    ]
    if base_records:
        original_modes = {record.turn.question_mode for record in base_records}
    elif unique_supporting:
        original_modes = {unique_supporting[0].turn.question_mode}
    else:
        original_modes = set()
    transfer_records = [
        record
        for record in unique_supporting
        if is_independent_transfer(record, original_modes)
    ]

    confidence = _confidence(
        coverage=coverage,
        unique_supporting=unique_supporting,
        satisfied_minimum_ids=satisfied_minimum_ids,
        conflict_exists=bool(supporting and limiting),
    )
    level = determine_assessment_level(
        has_valid_match=bool(valid_records),
        has_unresolved_critical_error=any(
            record.evidence.strength in {"medium", "strong"}
            for record in limiting
            if record.match.matched_critical_errors
        ),
        minimum_sufficiency_met=minimum_sufficiency_met,
        quality=quality,
        has_independent_transfer_success=bool(transfer_records),
    )
    reasons = _build_reasons(
        valid_records=valid_records,
        supporting=supporting,
        limiting=limiting,
        satisfied_minimum_ids=satisfied_minimum_ids,
        minimum_ids=minimum_ids,
        matched_excellence_ids=matched_excellence_ids,
        accepted_alternative_ids=accepted_alternative_ids,
    )

    return RequirementEvidenceAssessment(
        requirement_id=binding.requirement_id,
        dimension_id=binding.primary_dimension_id,
        level=level,
        coverage=coverage,
        confidence=confidence,
        satisfied_minimum_criterion_ids=sorted(satisfied_minimum_ids),
        matched_excellence_signal_ids=sorted(matched_excellence_ids),
        unresolved_critical_error_ids=sorted(unresolved_critical_error_ids),
        accepted_alternative_ids=sorted(accepted_alternative_ids),
        supporting_evidence_ids=sorted(
            {record.evidence.id for record in supporting}
        ),
        limiting_evidence_ids=sorted({record.evidence.id for record in limiting}),
        transfer_evidence_ids=sorted(
            {record.evidence.id for record in transfer_records}
        ),
        quality=quality,
        assessment_reasons=reasons,
    )


def build_requirement_evidence_assessments(
    role_profile: RoleCompetencyProfile,
    blueprint: ScoringBlueprint,
    match_batch: RubricMatchBatch,
    evidences: list[Evidence],
    turns: list[InterviewTurn],
) -> list[RequirementEvidenceAssessment]:
    """Aggregate validated rubric matches in deterministic blueprint order."""

    dimensions = _profile_dimensions(role_profile)
    ordered_bindings, bindings_by_requirement = _blueprint_bindings(
        blueprint,
        role_profile,
        dimensions,
    )
    requirement_ids = set(bindings_by_requirement)
    turns_by_id = _turn_index(turns, requirement_ids)
    evidences_by_id = _evidence_index(evidences, turns_by_id, requirement_ids)
    records_by_requirement = _validate_matches(
        match_batch,
        evidences_by_id,
        turns_by_id,
        bindings_by_requirement,
        dimensions,
    )

    assessments: list[RequirementEvidenceAssessment] = []
    for binding in ordered_bindings:
        dimension = dimensions[binding.primary_dimension_id]
        assessments.append(
            _build_one_assessment(
                binding,
                dimension,
                records_by_requirement[binding.requirement_id],
            )
        )
    return assessments


__all__ = [
    "RequirementEvidenceAssessmentError",
    "ValidatedEvidenceMatch",
    "aggregate_quality",
    "build_requirement_evidence_assessments",
    "determine_assessment_level",
    "is_independent_transfer",
]
