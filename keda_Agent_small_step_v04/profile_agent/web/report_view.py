"""Public, evidence-traceable projection of an assessment report.

The persisted :class:`AssessmentReport` intentionally contains only report
facts.  This module joins those facts with the immutable interview plan,
turns, evidence and Role Pack so the browser can open a score reason and see
the original question, answer and evidence observation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
import re
from typing import Any, Literal

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


class _ViewModel(BaseModel):
    """Base for browser-facing projections with a closed field contract."""

    model_config = ConfigDict(extra="forbid")


class EvidenceExcerptView(_ViewModel):
    """A short evidence drawer item; raw evidence IDs stay server-side."""

    turn_id: str
    conclusion: str
    quote: str
    interpretation: str
    limitation: str


# Keep the old import name available to callers while exposing only the new
# safe excerpt fields in the serialized model.
EvidenceSourceView = EvidenceExcerptView


class ReasonView(_ViewModel):
    reason_type: str
    text: str
    sources: list[EvidenceExcerptView]


class RadarDimensionView(_ViewModel):
    name: str
    score: float | None
    level: str
    coverage: float
    confidence: str
    reasons: list[ReasonView]


class InterviewTranscriptTurnView(_ViewModel):
    turn_id: str
    sequence_number: int
    question: str
    answer: str | None
    question_mode: str
    requirement_label: str
    asked_at: datetime
    answered_at: datetime | None
    evidence_status: Literal["supporting", "limiting", "mixed", "none"]
    evidence_cta: str = "查看本轮依据"


class CandidateOverviewView(_ViewModel):
    candidate_name: str | None = None
    target_role: str
    education_summary: str | None = None
    experience_summary: str | None = None
    jd_focus: list[str]
    interview_rounds: int
    generated_at: datetime


class DecisionSignalView(_ViewModel):
    title: str
    text: str
    dimension_names: list[str]
    confidence: str


class ReinterviewFocusView(_ViewModel):
    priority: int
    dimension_name: str
    reason: str
    question: str
    follow_ups: list[str]
    positive_signals: list[str]
    risk_signals: list[str]
    pass_criteria: list[str]
    suggested_minutes: int


class EnterpriseAssessmentView(_ViewModel):
    decision: str
    decision_label: str
    provisional_score: float | None = None
    confidence: str
    conditions: list[str]
    decision_reasons: list[str]
    overall_assessment: str
    strengths: list[DecisionSignalView]
    risks: list[DecisionSignalView]
    unknowns: list[DecisionSignalView]
    reinterview_plan: list[ReinterviewFocusView]
    evidence_excerpts: list[EvidenceExcerptView]


class InterviewPathStepView(_ViewModel):
    turn_id: str
    question_mode: str
    outcome: str


class ClaimVerificationView(_ViewModel):
    status: str
    explanation: str


class JobMatchView(_ViewModel):
    raw_score: float | None = None
    published: bool
    fit_level: str | None = None
    coverage: float
    confidence: str
    limiting_reasons: list[ReasonView]


class ReportViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    demo: bool
    target_role: str
    role_profile_version: str
    scoring_engine_version: str
    candidate_overview: CandidateOverviewView
    enterprise_assessment: EnterpriseAssessmentView
    job_match: JobMatchView
    radar_dimensions: list[RadarDimensionView]
    narrative: dict[str, Any]
    interview_path: list[InterviewPathStepView]
    interview_transcript: list[InterviewTranscriptTurnView]
    claim_verifications: list[ClaimVerificationView]
    assessment_limitations: list[str]
    demo_variant: str = "assessment"
    demo_case_title: str | None = None
    demo_case_description: str | None = None


_INTERNAL_TEXT_ID = re.compile(
    r"(?<![A-Za-z0-9_])(?:role_dim_[A-Za-z0-9_]+|req_[A-Za-z0-9_]+|"
    r"(?:d\d+_(?:min|exc|err|alt)_[A-Za-z0-9_]+)|"
    r"(?:ev(?:idence)?|claim|ast|assessment|candidate)_[A-Za-z0-9_]+)"
    r"(?![A-Za-z0-9_])"
)
_INTERNAL_LABEL = re.compile(
    r"(?<![A-Za-z0-9_])(?:RubricMatch|Requirement)(?![A-Za-z0-9_])"
)


def _public_server_copy(
    value: str | None,
    *,
    labels: Mapping[str, str] | None = None,
    field: str = "公开文案",
) -> str:
    """Validate generated copy before exposing it in the public view.

    Known server-side IDs may be translated only when the caller supplies a
    specific immutable label map.  Any remaining internal token is an error;
    it is never deleted or collapsed into a misleading half-sentence.  The
    ``E3 visa``-style text is intentionally outside the internal-token grammar.
    """

    text = (value or "").strip()
    if labels:
        # Replace exact tokens only.  A token embedded in a normal identifier
        # must not be partially rewritten.
        for internal, label in sorted(labels.items(), key=lambda item: -len(item[0])):
            token = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(internal)}(?![A-Za-z0-9_])"
            )
            text = token.sub(label, text)
    if _INTERNAL_TEXT_ID.search(text) or _INTERNAL_LABEL.search(text):
        raise ValueError(f"{field} 包含未翻译的内部标识，拒绝公开")
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"[：:，,、；;]\s*[。.!！]", "。", text)
    text = re.sub(r"[：:，,、；;]\s*(?:[，,、；;]|$)", "", text)
    return text.strip(" ，,、:：；;")


def _public_quote(quote: str, answer: str) -> str | None:
    """Return an exact partial quote, omitting a complete answer.

    Validation is deliberately performed on the raw strings.  Leading or
    trailing whitespace is not normalized into a seemingly valid quote.
    """

    if not quote or not quote.strip() or not answer or quote not in answer:
        return None
    if quote == answer:
        return None
    return quote


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
    """Keep the locked reason text while exposing readable rubric text."""

    labels = dict(rubric_text_by_id)
    labels.update({"RubricMatch": "证据匹配", "Requirement": "岗位要求"})
    rubric_texts = [
        rubric_text_by_id[rubric_id]
        for rubric_id in reason.rubric_signal_ids
    ]
    if not rubric_texts:
        return _public_server_copy(reason.text, labels=labels, field="评分理由")
    return _public_server_copy(
        f"{reason.text}（规则依据：{'；'.join(rubric_texts)}）",
        labels=labels,
        field="评分理由",
    )


def _source_for_evidence(
    evidence_id: str,
    evidences_by_id: Mapping[str, Evidence],
    turns_by_id: Mapping[str, InterviewTurn],
    reason: ScoreReason | None = None,
) -> EvidenceSourceView | None:
    evidence = evidences_by_id.get(evidence_id)
    if evidence is None:
        raise ValueError(f"Reason 引用了不存在的 evidence_id: {evidence_id}")
    turn = turns_by_id.get(evidence.turn_id)
    if turn is None:
        raise ValueError(
            f"Evidence 引用了不存在的 turn_id: {evidence.turn_id}"
        )
    answer = turn.answer or ""
    quote = _public_quote(evidence.source_excerpt, answer)
    if not quote:
        if evidence.source_excerpt == answer and answer:
            # The transcript is the sole public location for a complete
            # answer.  Do not copy or shorten it into a source drawer.
            return None
        raise ValueError(
            "Evidence.source_excerpt 不属于关联 turn.answer: " + evidence.id
        )
    conclusion_by_type = {
        "strength": "该回答支持当前能力判断。",
        "risk": "该回答限制当前能力判断。",
        "critical_error": "该回答提示需要重点关注的限制。",
    }
    return EvidenceExcerptView(
        turn_id=turn.id,
        conclusion=conclusion_by_type.get(
            reason.reason_type if reason is not None else "",
            "该回答提供了可回溯的面试依据。",
        ),
        quote=quote,
        interpretation="该片段提供了可回溯的面试依据。",
        limitation="该证据只支持当前问答场景，迁移能力仍需独立验证。",
    )


def _reason_view(
    reason: ScoreReason,
    *,
    dimension: CompetencyDimensionRubric | None,
    evidences_by_id: Mapping[str, Evidence],
    turns_by_id: Mapping[str, InterviewTurn],
    rubric_text_by_id: Mapping[str, str],
) -> ReasonView:
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
        unknown_rubric_ids = sorted(
            set(reason.rubric_signal_ids) - dimension_rubric_ids
        )
        if unknown_rubric_ids:
            raise ValueError(
                "Reason 引用了不存在或不属于该维度的 Rubric ID: "
                + ", ".join(unknown_rubric_ids)
            )
    sources: list[EvidenceExcerptView] = []
    seen_evidence_ids: set[str] = set()
    for evidence_id in reason.evidence_ids:
        if evidence_id in seen_evidence_ids:
            continue
        seen_evidence_ids.add(evidence_id)
        source = _source_for_evidence(
            evidence_id,
            evidences_by_id,
            turns_by_id,
            reason,
        )
        if source is not None:
            sources.append(source)
    return ReasonView(
        reason_type=reason.reason_type,
        text=_reason_text(reason, rubric_text_by_id),
        sources=sources,
    )


def _validate_enterprise_references(
    report: AssessmentReport,
    *,
    profile_dimensions: Mapping[str, CompetencyDimensionRubric],
    turns_by_id: Mapping[str, InterviewTurn],
    evidences_by_id: Mapping[str, Evidence],
) -> None:
    """Validate all enterprise joins before any public helper projects them."""

    enterprise = report.enterprise_assessment
    dimension_ids = set(profile_dimensions)

    for signal_group in (
        enterprise.strengths,
        enterprise.risks,
        enterprise.unknowns,
    ):
        for signal in signal_group:
            for dimension_id in signal.dimension_ids:
                if dimension_id not in dimension_ids:
                    raise ValueError(
                        "Enterprise signal 引用了不存在的 Role Dimension: "
                        + dimension_id
                    )
            for evidence_id in signal.evidence_ids:
                if evidence_id not in evidences_by_id:
                    raise ValueError(
                        "Enterprise signal 引用了不存在的 evidence_id: "
                        + evidence_id
                    )

    for focus in enterprise.reinterview_plan:
        if focus.dimension_id not in dimension_ids:
            raise ValueError(
                "Enterprise reinterview 引用了不存在的 Role Dimension: "
                + focus.dimension_id
            )
        for evidence_id in focus.related_evidence_ids:
            if evidence_id not in evidences_by_id:
                raise ValueError(
                    "Enterprise reinterview 引用了不存在的 evidence_id: "
                    + evidence_id
                )

    for excerpt in enterprise.evidence_excerpts:
        evidence = evidences_by_id.get(excerpt.evidence_id)
        if evidence is None:
            raise ValueError(
                "Enterprise evidence excerpt 引用了不存在的 evidence_id: "
                + excerpt.evidence_id
            )
        if evidence.turn_id != excerpt.turn_id:
            raise ValueError(
                "Enterprise evidence excerpt 的 evidence.turn_id 与 excerpt.turn_id 不一致: "
                + excerpt.evidence_id
            )
        turn = turns_by_id.get(excerpt.turn_id)
        if turn is None:
            raise ValueError(
                "Enterprise evidence excerpt 引用了不存在的 turn_id: "
                + excerpt.turn_id
            )
        answer = turn.answer or ""
        if not excerpt.quote or not excerpt.quote.strip():
            raise ValueError(
                "Enterprise evidence excerpt quote 不能为空或仅含空白: "
                + excerpt.evidence_id
            )
        if excerpt.quote not in answer:
            raise ValueError(
                "Enterprise evidence excerpt quote 不属于关联 turn.answer: "
                + excerpt.evidence_id
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
    _validate_enterprise_references(
        report,
        profile_dimensions=profile_dimensions,
        turns_by_id=turns_by_id,
        evidences_by_id=evidences_by_id,
    )


def _interview_transcript_view(
    turns: Iterable[InterviewTurn],
    evidences: Iterable[Evidence],
    requirements: Mapping[str, Any],
) -> list[InterviewTranscriptTurnView]:
    evidences_by_turn: dict[str, list[Evidence]] = {}
    for evidence in evidences:
        evidences_by_turn.setdefault(evidence.turn_id, []).append(evidence)

    transcript: list[InterviewTranscriptTurnView] = []
    for turn in sorted(turns, key=lambda item: item.sequence_number):
        linked_evidences = evidences_by_turn.get(turn.id, [])
        polarities = {evidence.polarity for evidence in linked_evidences}
        if polarities == {"supporting"}:
            evidence_status = "supporting"
        elif polarities == {"contradicting"}:
            evidence_status = "limiting"
        elif polarities == {"supporting", "contradicting"}:
            evidence_status = "mixed"
        else:
            evidence_status = "none"
        requirement = requirements[turn.primary_requirement_id]
        transcript.append(
            InterviewTranscriptTurnView(
                turn_id=turn.id,
                sequence_number=turn.sequence_number,
                question=turn.question,
                answer=turn.answer,
                question_mode=turn.question_mode,
                requirement_label=_public_server_copy(
                    requirement.description,
                    field="Requirement 展示文案",
                ),
                asked_at=turn.asked_at,
                answered_at=turn.answered_at,
                evidence_status=evidence_status,
            )
        )
    return transcript


def _public_labels(
    profile: RoleCompetencyProfile,
    requirements: Mapping[str, Any],
    rubric_text_by_id: Mapping[str, str],
) -> dict[str, str]:
    labels = dict(rubric_text_by_id)
    labels.update(
        {
            dimension.id: dimension.name
            for dimension in profile.dimensions
        }
    )
    labels.update(
        {
            requirement_id: requirement.description
            for requirement_id, requirement in requirements.items()
        }
    )
    return labels


def _dimension_labels(
    profile: RoleCompetencyProfile,
) -> dict[str, str]:
    return {dimension.id: dimension.name for dimension in profile.dimensions}


def _candidate_overview_view(
    report: AssessmentReport,
) -> CandidateOverviewView:
    overview = report.candidate_overview
    return CandidateOverviewView(
        candidate_name=_public_server_copy(
            overview.candidate_name,
            field="candidate_name",
        )
        or None,
        target_role=_public_server_copy(
            overview.target_role,
            field="candidate_overview.target_role",
        ),
        education_summary=(
            _public_server_copy(
                overview.education_summary,
                field="candidate_overview.education_summary",
            )
            or None
        ),
        experience_summary=(
            _public_server_copy(
                overview.experience_summary,
                field="candidate_overview.experience_summary",
            )
            or None
        ),
        jd_focus=[
            _public_server_copy(item, field="candidate_overview.jd_focus")
            for item in overview.jd_focus
            if _public_server_copy(item, field="candidate_overview.jd_focus")
        ],
        interview_rounds=overview.interview_rounds,
        generated_at=overview.generated_at,
    )


def _decision_signal_view(
    signal: Any,
    *,
    dimension_labels: Mapping[str, str],
) -> DecisionSignalView:
    return DecisionSignalView(
        title=_public_server_copy(
            signal.title,
            labels=dimension_labels,
            field="Enterprise signal.title",
        ),
        text=_public_server_copy(
            signal.text,
            labels=dimension_labels,
            field="Enterprise signal.text",
        ),
        dimension_names=[dimension_labels[item] for item in signal.dimension_ids],
        confidence=signal.confidence,
    )


def _reinterview_focus_view(
    focus: Any,
    *,
    dimension_labels: Mapping[str, str],
) -> ReinterviewFocusView:
    return ReinterviewFocusView(
        priority=focus.priority,
        dimension_name=_public_server_copy(
            focus.dimension_name,
            labels=dimension_labels,
            field="Enterprise reinterview.dimension_name",
        ),
        reason=_public_server_copy(
            focus.reason,
            labels=dimension_labels,
            field="Enterprise reinterview.reason",
        ),
        question=_public_server_copy(
            focus.question,
            labels=dimension_labels,
            field="Enterprise reinterview.question",
        ),
        follow_ups=[
            _public_server_copy(
                item,
                labels=dimension_labels,
                field="Enterprise reinterview.follow_ups",
            )
            for item in focus.follow_ups
        ],
        positive_signals=[
            _public_server_copy(
                item,
                labels=dimension_labels,
                field="Enterprise reinterview.positive_signals",
            )
            for item in focus.positive_signals
        ],
        risk_signals=[
            _public_server_copy(
                item,
                labels=dimension_labels,
                field="Enterprise reinterview.risk_signals",
            )
            for item in focus.risk_signals
        ],
        pass_criteria=[
            _public_server_copy(
                item,
                labels=dimension_labels,
                field="Enterprise reinterview.pass_criteria",
            )
            for item in focus.pass_criteria
        ],
        suggested_minutes=focus.suggested_minutes,
    )


def _enterprise_evidence_excerpt_views(
    report: AssessmentReport,
    turns_by_id: Mapping[str, InterviewTurn],
) -> list[EvidenceExcerptView]:
    result: list[EvidenceExcerptView] = []
    for excerpt in report.enterprise_assessment.evidence_excerpts:
        turn = turns_by_id.get(excerpt.turn_id)
        if turn is None:
            raise ValueError(
                "Enterprise evidence excerpt 引用了不存在的 turn_id: "
                + excerpt.turn_id
            )
        quote = _public_quote(excerpt.quote, turn.answer or "")
        if not quote:
            # The complete answer is already available in the transcript and
            # must not be copied, shortened, or normalized into a public
            # excerpt/source block.
            if excerpt.quote == (turn.answer or ""):
                continue
            raise ValueError(
                "Enterprise evidence excerpt quote 不属于关联 turn.answer"
            )
        result.append(
            EvidenceExcerptView(
                turn_id=turn.id,
                conclusion=_public_server_copy(
                    excerpt.conclusion,
                    field="Enterprise evidence conclusion",
                ),
                quote=quote,
                interpretation=_public_server_copy(
                    excerpt.interpretation,
                    field="Enterprise evidence interpretation",
                ),
                limitation=_public_server_copy(
                    excerpt.limitation,
                    field="Enterprise evidence limitation",
                ),
            )
        )
    return result


def _enterprise_assessment_view(
    report: AssessmentReport,
    turns_by_id: Mapping[str, InterviewTurn],
    *,
    dimension_labels: Mapping[str, str],
) -> EnterpriseAssessmentView:
    enterprise = report.enterprise_assessment
    return EnterpriseAssessmentView(
        decision=enterprise.decision,
        decision_label=_public_server_copy(
            enterprise.decision_label,
            field="Enterprise decision_label",
        ),
        provisional_score=enterprise.provisional_score,
        confidence=enterprise.confidence,
        conditions=[
            _public_server_copy(
                item,
                labels=dimension_labels,
                field="Enterprise condition",
            )
            for item in enterprise.conditions
        ],
        decision_reasons=[
            _public_server_copy(
                item,
                labels=dimension_labels,
                field="Enterprise decision_reason",
            )
            for item in enterprise.decision_reasons
        ],
        overall_assessment=_public_server_copy(
            enterprise.overall_assessment,
            labels=dimension_labels,
            field="Enterprise overall_assessment",
        ),
        strengths=[
            _decision_signal_view(signal, dimension_labels=dimension_labels)
            for signal in enterprise.strengths
        ],
        risks=[
            _decision_signal_view(signal, dimension_labels=dimension_labels)
            for signal in enterprise.risks
        ],
        unknowns=[
            _decision_signal_view(signal, dimension_labels=dimension_labels)
            for signal in enterprise.unknowns
        ],
        reinterview_plan=[
            _reinterview_focus_view(focus, dimension_labels=dimension_labels)
            for focus in enterprise.reinterview_plan
        ],
        evidence_excerpts=_enterprise_evidence_excerpt_views(
            report,
            turns_by_id,
        ),
    )


def _narrative_view(
    report: AssessmentReport,
    *,
    dimension_labels: Mapping[str, str],
) -> dict[str, Any]:
    narrative = report.narrative

    def item_view(item: Any) -> dict[str, Any]:
        return {
            "text": _public_server_copy(
                item.text,
                labels=dimension_labels,
                field="Narrative text",
            ),
            "dimension_names": [
                dimension_labels[dimension_id]
                for dimension_id in item.dimension_ids
            ],
        }

    return {
        "executive_summary": _public_server_copy(
            narrative.executive_summary,
            labels=dimension_labels,
            field="Narrative executive_summary",
        ),
        "strengths": [item_view(item) for item in narrative.strengths],
        "risks": [item_view(item) for item in narrative.risks],
        "unverified_areas": [
            item_view(item) for item in narrative.unverified_areas
        ],
        "fit_contexts": [item_view(item) for item in narrative.fit_contexts],
    }


def _job_match_view(
    report: AssessmentReport,
    *,
    evidences_by_id: Mapping[str, Evidence],
    turns_by_id: Mapping[str, InterviewTurn],
    rubric_text_by_id: Mapping[str, str],
) -> JobMatchView:
    job_match = report.score_snapshot.job_match
    return JobMatchView(
        raw_score=job_match.raw_score,
        published=job_match.published,
        fit_level=job_match.fit_level,
        coverage=job_match.coverage,
        confidence=job_match.confidence,
        limiting_reasons=[
            _reason_view(
                reason,
                dimension=None,
                evidences_by_id=evidences_by_id,
                turns_by_id=turns_by_id,
                rubric_text_by_id=rubric_text_by_id,
            )
            for reason in job_match.limiting_reasons
        ],
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
    labels = _public_labels(
        normalized_profile,
        plan_requirements,
        rubric_text_by_id,
    )
    dimension_labels = _dimension_labels(normalized_profile)
    _validate_report_references(
        normalized_report,
        profile_dimensions=dimensions,
        plan_requirement_ids=set(plan_requirements),
        turns_by_id=turns_by_id,
        evidences_by_id=evidences_by_id,
        rubric_text_by_id=rubric_text_by_id,
    )
    interview_transcript = _interview_transcript_view(
        normalized_turns,
        normalized_evidences,
        plan_requirements,
    )
    candidate_overview = _candidate_overview_view(
        normalized_report,
    )
    enterprise_assessment = _enterprise_assessment_view(
        normalized_report,
        turns_by_id,
        dimension_labels=dimension_labels,
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
        target_role=_public_server_copy(
            normalized_report.target_role,
            field="Report target_role",
        ),
        role_profile_version=normalized_report.score_snapshot.role_profile_version,
        scoring_engine_version=normalized_report.score_snapshot.scoring_engine_version,
        candidate_overview=candidate_overview,
        enterprise_assessment=enterprise_assessment,
        job_match=_job_match_view(
            normalized_report,
            evidences_by_id=evidences_by_id,
            turns_by_id=turns_by_id,
            rubric_text_by_id=rubric_text_by_id,
        ),
        radar_dimensions=radar_dimensions,
        narrative=_narrative_view(
            normalized_report,
            dimension_labels=dimension_labels,
        ),
        interview_path=[
            InterviewPathStepView(
                turn_id=item.turn_id,
                question_mode=item.question_mode,
                outcome=_public_server_copy(
                    item.outcome,
                    labels=labels,
                    field="InterviewPath outcome",
                ),
            )
            for item in normalized_report.interview_path
        ],
        interview_transcript=interview_transcript,
        claim_verifications=[
            ClaimVerificationView(
                status=item.status,
                explanation=_public_server_copy(
                    item.explanation,
                    labels=labels,
                    field="ClaimVerification explanation",
                ),
            )
            for item in normalized_report.score_snapshot.claim_verifications
        ],
        assessment_limitations=[
            _public_server_copy(
                item,
                labels=dimension_labels,
                field="Assessment limitation",
            )
            for item in normalized_report.assessment_limitations
        ],
        demo_variant=demo_variant,
        demo_case_title=(
            _public_server_copy(
                demo_case_title,
                field="Demo case title",
            )
            or None
        ),
        demo_case_description=_public_server_copy(
            demo_case_description,
            field="Demo case description",
        )
        or None,
    )


__all__ = [
    "CandidateOverviewView",
    "ClaimVerificationView",
    "DecisionSignalView",
    "EnterpriseAssessmentView",
    "EvidenceExcerptView",
    "EvidenceSourceView",
    "InterviewPathStepView",
    "ReasonView",
    "RadarDimensionView",
    "ReinterviewFocusView",
    "InterviewTranscriptTurnView",
    "JobMatchView",
    "ReportViewModel",
    "build_report_view",
]
