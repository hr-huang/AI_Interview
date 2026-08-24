"""Public, evidence-traceable projection of an assessment report.

The persisted :class:`AssessmentReport` intentionally contains only report
facts.  This module joins those facts with the immutable interview plan,
turns, evidence and Role Pack so the browser can open a score reason and see
the original question, answer and evidence observation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.schemas.report_schema import (
    AssessmentReport,
    CompetencyDimensionRubric,
    RadarDimensionResult,
    RoleCompetencyProfile,
    ScoreReason,
)
from profile_agent.schemas.runtime_schema import Evidence, InterviewTurn


class EvidenceSourceView(BaseModel):
    evidence_id: str
    turn_id: str
    question: str
    answer: str
    observation: str
    source_excerpt: str


class ReasonView(BaseModel):
    reason_type: str
    text: str
    evidence_ids: list[str]
    rubric_signal_ids: list[str]
    sources: list[EvidenceSourceView]


class RadarDimensionView(BaseModel):
    dimension_id: str
    name: str
    score: float | None
    level: str
    coverage: float
    confidence: str
    reasons: list[ReasonView]


class ReportViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    demo: bool
    target_role: str
    role_profile_version: str
    scoring_engine_version: str
    job_match: dict[str, Any]
    radar_dimensions: list[RadarDimensionView]
    narrative: dict[str, Any]
    interview_path: list[dict[str, Any]]
    claim_verifications: list[dict[str, Any]]
    assessment_limitations: list[str]
    demo_variant: str = "assessment"
    demo_case_title: str | None = None
    demo_case_description: str | None = None


def _normalise_models(
    values: Iterable[Any], model_type: type[BaseModel], label: str
) -> list[BaseModel]:
    result: list[BaseModel] = []
    for value in values:
        try:
            result.append(model_type.model_validate(value))
        except Exception as error:
            raise ValueError(f"{label} 不符合结构化契约") from error
    return result


def _index_by_id(values: Iterable[Any], label: str) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for value in values:
        item_id = getattr(value, "id", None)
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError(f"{label} 必须有非空 ID")
        if item_id in indexed:
            raise ValueError(f"{label} ID 重复: {item_id}")
        indexed[item_id] = value
    return indexed


def _plan_indexes(plan: InterviewPlan) -> tuple[dict[str, Any], dict[str, Any]]:
    targets = _index_by_id(plan.targets, "Target")
    requirements: dict[str, Any] = {}
    for target in plan.targets:
        for requirement in target.evidence_requirements:
            if requirement.id in requirements:
                raise ValueError(f"Requirement ID 重复: {requirement.id}")
            requirements[requirement.id] = requirement
    return targets, requirements


def _validate_history(
    plan: InterviewPlan,
    turns: list[InterviewTurn],
    evidences: list[Evidence],
) -> tuple[dict[str, InterviewTurn], dict[str, Evidence]]:
    targets, requirements = _plan_indexes(plan)
    turns_by_id = _index_by_id(turns, "InterviewTurn")
    for turn in turns:
        if turn.target_id not in targets:
            raise ValueError(
                f"InterviewTurn 引用了不存在的 target_id: {turn.target_id}"
            )
        if turn.primary_requirement_id not in requirements:
            raise ValueError(
                "InterviewTurn 引用了不存在的 requirement_id: "
                + turn.primary_requirement_id
            )
        target_requirement_ids = {
            item.id for item in targets[turn.target_id].evidence_requirements
        }
        if turn.primary_requirement_id not in target_requirement_ids:
            raise ValueError(
                "InterviewTurn 的 requirement 不属于 target: "
                + turn.primary_requirement_id
            )

    evidences_by_id = _index_by_id(evidences, "Evidence")
    for evidence in evidences:
        turn = turns_by_id.get(evidence.turn_id)
        if turn is None:
            raise ValueError(
                f"Evidence 引用了不存在的 turn_id: {evidence.turn_id}"
            )
        unknown_requirements = sorted(
            set(evidence.requirement_ids) - set(requirements)
        )
        if len(evidence.requirement_ids) != len(set(evidence.requirement_ids)):
            raise ValueError(f"Evidence 的 requirement_ids 重复: {evidence.id}")
        if unknown_requirements:
            raise ValueError(
                "Evidence 引用了不存在的 requirement_id: "
                + ", ".join(unknown_requirements)
            )
        if evidence.source_excerpt and evidence.source_excerpt not in (
            turn.answer or ""
        ):
            raise ValueError(
                "Evidence.source_excerpt 不属于关联 turn.answer: "
                + evidence.id
            )
        target_requirement_ids = {
            item.id for item in targets[turn.target_id].evidence_requirements
        }
        outside_target = sorted(
            set(evidence.requirement_ids) - target_requirement_ids
        )
        if outside_target:
            raise ValueError(
                "Evidence 的 requirement provenance 与 turn 不一致: "
                + evidence.id
            )
    return turns_by_id, evidences_by_id


def _rubric_index(
    profile: RoleCompetencyProfile,
) -> tuple[dict[str, CompetencyDimensionRubric], dict[str, str]]:
    dimensions = _index_by_id(profile.dimensions, "Role Dimension")
    rubric_text_by_id: dict[str, str] = {}
    for dimension in profile.dimensions:
        all_criteria = (
            dimension.minimum_criteria
            + dimension.excellence_signals
            + dimension.critical_errors
            + dimension.accepted_alternatives
        )
        for criterion in all_criteria:
            if criterion.id in rubric_text_by_id:
                raise ValueError(f"Rubric ID 重复: {criterion.id}")
            rubric_text_by_id[criterion.id] = criterion.text
    return dimensions, rubric_text_by_id


def _reason_text(reason: ScoreReason, rubric_text_by_id: Mapping[str, str]) -> str:
    """Keep the locked reason text while exposing readable rubric text.

    The IDs remain untouched.  The report writer's generic reason text is
    followed by the versioned Role Pack wording so the view is useful without
    requiring the browser to ship a second rubric lookup table.
    """

    rubric_texts = [
        rubric_text_by_id[rubric_id]
        for rubric_id in reason.rubric_signal_ids
    ]
    if not rubric_texts:
        return reason.text
    return f"{reason.text}（规则依据：{'；'.join(rubric_texts)}）"


def _source_for_evidence(
    evidence_id: str,
    evidences_by_id: Mapping[str, Evidence],
    turns_by_id: Mapping[str, InterviewTurn],
) -> EvidenceSourceView:
    evidence = evidences_by_id.get(evidence_id)
    if evidence is None:
        raise ValueError(f"Reason 引用了不存在的 evidence_id: {evidence_id}")
    turn = turns_by_id.get(evidence.turn_id)
    if turn is None:
        raise ValueError(
            f"Evidence 引用了不存在的 turn_id: {evidence.turn_id}"
        )
    return EvidenceSourceView(
        evidence_id=evidence.id,
        turn_id=turn.id,
        question=turn.question,
        answer=turn.answer or "",
        observation=evidence.observation,
        source_excerpt=evidence.source_excerpt,
    )


def _reason_view(
    reason: ScoreReason,
    *,
    dimension: CompetencyDimensionRubric,
    evidences_by_id: Mapping[str, Evidence],
    turns_by_id: Mapping[str, InterviewTurn],
    rubric_text_by_id: Mapping[str, str],
) -> ReasonView:
    dimension_rubric_ids = {
        criterion.id
        for criterion in (
            dimension.minimum_criteria
            + dimension.excellence_signals
            + dimension.critical_errors
            + dimension.accepted_alternatives
        )
    }
    unknown_rubric_ids = sorted(
        set(reason.rubric_signal_ids) - dimension_rubric_ids
    )
    if unknown_rubric_ids:
        raise ValueError(
            "Reason 引用了不存在或不属于该维度的 Rubric ID: "
            + ", ".join(unknown_rubric_ids)
        )
    sources: list[EvidenceSourceView] = []
    seen_evidence_ids: set[str] = set()
    for evidence_id in reason.evidence_ids:
        if evidence_id in seen_evidence_ids:
            continue
        seen_evidence_ids.add(evidence_id)
        sources.append(
            _source_for_evidence(evidence_id, evidences_by_id, turns_by_id)
        )
    return ReasonView(
        reason_type=reason.reason_type,
        text=_reason_text(reason, rubric_text_by_id),
        evidence_ids=list(reason.evidence_ids),
        rubric_signal_ids=list(reason.rubric_signal_ids),
        sources=sources,
    )


def _validate_report_references(
    report: AssessmentReport,
    *,
    profile_dimensions: Mapping[str, CompetencyDimensionRubric],
    plan_requirement_ids: set[str],
    turns_by_id: Mapping[str, InterviewTurn],
    evidences_by_id: Mapping[str, Evidence],
    rubric_text_by_id: Mapping[str, str],
) -> None:
    dimension_ids = set(profile_dimensions)

    def validate_reason_ids(
        reason: ScoreReason,
        label: str,
        dimension: CompetencyDimensionRubric | None = None,
    ) -> None:
        for evidence_id in reason.evidence_ids:
            if evidence_id not in evidences_by_id:
                raise ValueError(
                    f"{label} 引用了不存在的 evidence_id: {evidence_id}"
                )
        for rubric_id in reason.rubric_signal_ids:
            if rubric_id not in rubric_text_by_id:
                raise ValueError(
                    f"{label} 引用了不存在的 Rubric ID: {rubric_id}"
                )
            if dimension is not None:
                dimension_rubric_ids = {
                    criterion.id
                    for criterion in (
                        dimension.minimum_criteria
                        + dimension.excellence_signals
                        + dimension.critical_errors
                        + dimension.accepted_alternatives
                    )
                }
                if rubric_id not in dimension_rubric_ids:
                    raise ValueError(
                        f"{label} 引用了不属于该维度的 Rubric ID: {rubric_id}"
                    )

    seen_assessment_ids: set[str] = set()
    for radar in report.score_snapshot.radar_dimensions:
        if radar.dimension_id not in dimension_ids:
            raise ValueError(
                "RadarDimension 引用了不存在的 Role Dimension: "
                + radar.dimension_id
            )
        for reason in radar.score_reasons:
            for evidence_id in reason.evidence_ids:
                if evidence_id not in evidences_by_id:
                    raise ValueError(
                        "Radar ScoreReason 引用了不存在的 evidence_id: "
                        + evidence_id
                    )
            for rubric_id in reason.rubric_signal_ids:
                if rubric_id not in {
                    criterion.id
                    for criterion in (
                        profile_dimensions[radar.dimension_id].minimum_criteria
                        + profile_dimensions[radar.dimension_id].excellence_signals
                        + profile_dimensions[radar.dimension_id].critical_errors
                        + profile_dimensions[radar.dimension_id].accepted_alternatives
                    )
                }:
                    raise ValueError(
                        "Radar ScoreReason 引用了不存在或不属于该维度的 Rubric ID: "
                        + rubric_id
                    )
        seen_breakdown_ids: set[str] = set()
        for breakdown in radar.requirement_breakdown:
            if breakdown.requirement_id in seen_breakdown_ids:
                raise ValueError(
                    "RadarDimension Requirement ID 重复: "
                    + breakdown.requirement_id
                )
            seen_breakdown_ids.add(breakdown.requirement_id)
            if breakdown.requirement_id not in plan_requirement_ids:
                raise ValueError(
                    "RadarDimension 引用了不存在的 requirement_id: "
                    + breakdown.requirement_id
                )
            if breakdown.dimension_id != radar.dimension_id:
                raise ValueError(
                    "RadarDimension Requirement provenance 与维度不一致: "
                    + breakdown.requirement_id
                )

    for assessment in report.score_snapshot.requirement_assessments:
        if assessment.requirement_id in seen_assessment_ids:
            raise ValueError(
                "RequirementAssessment ID 重复: " + assessment.requirement_id
            )
        seen_assessment_ids.add(assessment.requirement_id)
        if assessment.requirement_id not in plan_requirement_ids:
            raise ValueError(
                "RequirementAssessment 引用了不存在的 requirement_id: "
                + assessment.requirement_id
            )
        if assessment.dimension_id not in dimension_ids:
            raise ValueError(
                "RequirementAssessment 引用了不存在的 Role Dimension: "
                + assessment.dimension_id
            )
        for evidence_id in (
            *assessment.supporting_evidence_ids,
            *assessment.limiting_evidence_ids,
            *assessment.transfer_evidence_ids,
        ):
            if evidence_id not in evidences_by_id:
                raise ValueError(
                    "RequirementAssessment 引用了不存在的 evidence_id: "
                    + evidence_id
                )
        for reason in assessment.assessment_reasons:
            validate_reason_ids(
                reason,
                "RequirementAssessment ScoreReason",
                profile_dimensions[assessment.dimension_id],
            )

    seen_score_ids: set[str] = set()
    for score in report.score_snapshot.requirement_scores:
        if score.requirement_id in seen_score_ids:
            raise ValueError("RequirementScore ID 重复: " + score.requirement_id)
        seen_score_ids.add(score.requirement_id)
        if score.requirement_id not in plan_requirement_ids:
            raise ValueError(
                "RequirementScore 引用了不存在的 requirement_id: "
                + score.requirement_id
            )
        if score.dimension_id not in dimension_ids:
            raise ValueError(
                "RequirementScore 引用了不存在的 Role Dimension: "
                + score.dimension_id
            )

    for reason in report.score_snapshot.job_match.limiting_reasons:
        validate_reason_ids(reason, "JobMatch ScoreReason")
    for path_step in report.interview_path:
        if path_step.turn_id not in turns_by_id:
            raise ValueError(
                "InterviewPath 引用了不存在的 turn_id: " + path_step.turn_id
            )
        for evidence_id in path_step.evidence_ids:
            if evidence_id not in evidences_by_id:
                raise ValueError(
                    "InterviewPath 引用了不存在的 evidence_id: " + evidence_id
                )
        if path_step.requirement_id not in plan_requirement_ids:
            raise ValueError(
                "InterviewPath 引用了不存在的 requirement_id: "
                + path_step.requirement_id
            )
    for verification in report.score_snapshot.claim_verifications:
        for evidence_id in (
            *verification.supporting_evidence_ids,
            *verification.contradicting_evidence_ids,
        ):
            if evidence_id not in evidences_by_id:
                raise ValueError(
                    "ClaimVerification 引用了不存在的 evidence_id: "
                    + evidence_id
                )
    narrative = report.narrative
    for item in (
        *narrative.strengths,
        *narrative.risks,
        *narrative.unverified_areas,
        *narrative.fit_contexts,
    ):
        for dimension_id in item.dimension_ids:
            if dimension_id not in dimension_ids:
                raise ValueError(
                    "Narrative 引用了不存在的 Role Dimension: " + dimension_id
                )
        for evidence_id in item.evidence_ids:
            if evidence_id not in evidences_by_id:
                raise ValueError(
                    "Narrative 引用了不存在的 evidence_id: " + evidence_id
                )
    for action in narrative.development_actions:
        if action.dimension_id not in dimension_ids:
            raise ValueError(
                "Narrative DevelopmentAction 引用了不存在的 Role Dimension: "
                + action.dimension_id
            )


def build_report_view(
    report: AssessmentReport | Mapping[str, Any],
    plan: InterviewPlan | Mapping[str, Any],
    turns: Iterable[InterviewTurn | Mapping[str, Any]],
    evidences: Iterable[Evidence | Mapping[str, Any]],
    profile: RoleCompetencyProfile | Mapping[str, Any],
    *,
    demo: bool,
    demo_variant: str = "assessment",
    demo_case_title: str | None = None,
    demo_case_description: str | None = None,
) -> ReportViewModel:
    """Build the browser-facing report while failing closed on bad joins."""

    normalized_report = AssessmentReport.model_validate(report)
    normalized_plan = InterviewPlan.model_validate(plan)
    normalized_profile = RoleCompetencyProfile.model_validate(profile)
    normalized_turns = [
        item
        for item in _normalise_models(turns, InterviewTurn, "InterviewTurn")
    ]
    normalized_evidences = [
        item for item in _normalise_models(evidences, Evidence, "Evidence")
    ]
    turns_by_id, evidences_by_id = _validate_history(
        normalized_plan,
        normalized_turns,
        normalized_evidences,
    )
    dimensions, rubric_text_by_id = _rubric_index(normalized_profile)
    _, plan_requirements = _plan_indexes(normalized_plan)
    _validate_report_references(
        normalized_report,
        profile_dimensions=dimensions,
        plan_requirement_ids=set(plan_requirements),
        turns_by_id=turns_by_id,
        evidences_by_id=evidences_by_id,
        rubric_text_by_id=rubric_text_by_id,
    )

    radar_by_id: dict[str, RadarDimensionResult] = {}
    for radar in normalized_report.score_snapshot.radar_dimensions:
        if radar.dimension_id in radar_by_id:
            raise ValueError(f"RadarDimension ID 重复: {radar.dimension_id}")
        radar_by_id[radar.dimension_id] = radar
    missing_dimensions = [
        dimension_id
        for dimension_id in dimensions
        if dimension_id not in radar_by_id
    ]
    if missing_dimensions:
        raise ValueError(
            "ScoreSnapshot 缺少 Role Dimension: " + ", ".join(missing_dimensions)
        )

    radar_dimensions: list[RadarDimensionView] = []
    for dimension in normalized_profile.dimensions:
        radar = radar_by_id[dimension.id]
        reasons = [
            _reason_view(
                reason,
                dimension=dimension,
                evidences_by_id=evidences_by_id,
                turns_by_id=turns_by_id,
                rubric_text_by_id=rubric_text_by_id,
            )
            for reason in radar.score_reasons
        ]
        radar_dimensions.append(
            RadarDimensionView(
                dimension_id=dimension.id,
                name=dimension.name,
                score=radar.score,
                level=radar.level,
                coverage=radar.coverage,
                confidence=radar.confidence,
                reasons=reasons,
            )
        )

    return ReportViewModel(
        demo=demo,
        target_role=normalized_report.target_role,
        role_profile_version=normalized_report.score_snapshot.role_profile_version,
        scoring_engine_version=normalized_report.score_snapshot.scoring_engine_version,
        job_match=normalized_report.score_snapshot.job_match.model_dump(mode="json"),
        radar_dimensions=radar_dimensions,
        narrative=normalized_report.narrative.model_dump(mode="json"),
        interview_path=[
            item.model_dump(mode="json") for item in normalized_report.interview_path
        ],
        claim_verifications=[
            item.model_dump(mode="json")
            for item in normalized_report.score_snapshot.claim_verifications
        ],
        assessment_limitations=list(normalized_report.assessment_limitations),
        demo_variant=demo_variant,
        demo_case_title=demo_case_title,
        demo_case_description=demo_case_description,
    )


__all__ = [
    "EvidenceSourceView",
    "ReasonView",
    "RadarDimensionView",
    "ReportViewModel",
    "build_report_view",
]
