"""Orchestrate the deterministic end-to-end assessment report stage.

The semantic boundaries are injectable so offline tests can provide fixed
blueprints, rubric matches and narrative drafts.  This module owns only the
stage ordering and the report-shaped deterministic projections; it never
turns a narrative failure into a scoring failure.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.schemas.job_schema import JobProfile
from profile_agent.schemas.report_schema import (
    AssessmentReport,
    CandidateOverview,
    ClaimVerification,
    DecisionSignal,
    DevelopmentAction,
    EnterpriseAssessment,
    InterviewPathStep,
    NarrativeItem,
    RequirementEvidenceAssessment,
    ReportNarrativeDraft,
    RoleCompetencyProfile,
    RubricMatchBatch,
    ScoreSnapshot,
    ScoringBlueprint,
)
from profile_agent.schemas.resume_schema import ResumeProfile
from profile_agent.schemas.runtime_schema import (
    Evidence,
    InterviewRuntimeState,
    InterviewTurn,
)
from profile_agent.services.claim_verification_service import (
    aggregate_claim_verifications,
)
from profile_agent.services.requirement_evidence_assessment_service import (
    build_requirement_evidence_assessments,
)
from profile_agent.services.report_writer_service import (
    EnterpriseCopyDraft,
    fallback_enterprise_copy,
    write_enterprise_copy,
)
from profile_agent.services.enterprise_report_service import (
    ReportConsistencyError,
    build_decision_signals,
    build_evidence_excerpts,
    derive_hiring_decision,
    select_reinterview_dimensions,
    validate_enterprise_assessment,
)
from profile_agent.services.role_profile_service import load_role_profile
from profile_agent.services.rubric_matcher_service import (
    match_evidence_to_rubric,
)
from profile_agent.services.score_engine_service import calculate_score_snapshot
from profile_agent.services.scoring_blueprint_service import (
    build_scoring_blueprint,
)


DEFAULT_ROLE_FAMILY = "ai_application_engineering"
DEFAULT_ROLE_PROFILE_VERSION = "2026-H2"


class AssessmentReportStateError(ValueError):
    """Raised when the interview runtime has not reached its terminal state."""


@dataclass(frozen=True)
class AssessmentReportSemanticServices:
    """Optional service bundle used by production callers and offline tests."""

    blueprint_builder: Callable[..., Any] = build_scoring_blueprint
    rubric_matcher: Callable[..., Any] = match_evidence_to_rubric
    assessment_builder: Callable[..., Any] = (
        build_requirement_evidence_assessments
    )
    score_engine: Callable[..., Any] = calculate_score_snapshot
    # Keep the old injection name for offline callers; its contract is now
    # EnterpriseCopyDraft rather than the legacy narrative shape.
    narrative_writer: Callable[..., Any] = write_enterprise_copy
    enterprise_copy_writer: Callable[..., Any] | None = None


def _resolve_service(
    semantic_services: object | None,
    explicit: Callable[..., Any] | None,
    name: str,
    default: Callable[..., Any],
) -> Callable[..., Any]:
    if explicit is not None:
        return explicit
    if semantic_services is None:
        return default

    aliases = {
        "blueprint_builder": (
            "blueprint_builder",
            "build_scoring_blueprint",
            "blueprint",
            "build_blueprint",
        ),
        "rubric_matcher": (
            "rubric_matcher",
            "match_evidence_to_rubric",
            "matcher",
            "matches",
        ),
        "assessment_builder": (
            "assessment_builder",
            "build_requirement_evidence_assessments",
            "assessment",
            "build_assessments",
        ),
        "score_engine": (
            "score_engine",
            "calculate_score_snapshot",
            "score",
        ),
        "narrative_writer": (
            "narrative_writer",
            "enterprise_copy_writer",
            "write_enterprise_copy",
            "write_report_narrative",
            "writer",
        ),
    }
    for alias in aliases[name]:
        candidate: object | None = None
        if isinstance(semantic_services, Mapping):
            candidate = semantic_services.get(alias)
        else:
            candidate = getattr(semantic_services, alias, None)
        if callable(candidate):
            return candidate

    return default


def _plan_indexes(
    plan: InterviewPlan,
) -> tuple[dict[str, object], dict[str, object], list[str]]:
    targets_by_id: dict[str, object] = {}
    requirements_by_id: dict[str, object] = {}
    ordered_requirement_ids: list[str] = []

    for target in plan.targets:
        if target.id in targets_by_id:
            raise ValueError(f"Target ID 重复: {target.id}")
        targets_by_id[target.id] = target
        for requirement in target.evidence_requirements:
            if requirement.id in requirements_by_id:
                raise ValueError(f"Requirement ID 重复: {requirement.id}")
            requirements_by_id[requirement.id] = requirement
            ordered_requirement_ids.append(requirement.id)

    if not ordered_requirement_ids:
        raise ValueError("InterviewPlan 必须包含至少一个 Evidence Requirement")
    return targets_by_id, requirements_by_id, ordered_requirement_ids


def _validate_history(
    plan: InterviewPlan,
    turns: list[InterviewTurn],
    evidences: list[Evidence],
) -> None:
    targets_by_id, requirements_by_id, _ = _plan_indexes(plan)
    turns_by_id: dict[str, InterviewTurn] = {}

    for turn in turns:
        if turn.id in turns_by_id:
            raise ValueError(f"InterviewTurn ID 重复: {turn.id}")
        if turn.target_id not in targets_by_id:
            raise ValueError(
                f"InterviewTurn 引用了不存在的 target_id: {turn.target_id}"
            )
        target = targets_by_id[turn.target_id]
        target_requirement_ids = {
            requirement.id for requirement in target.evidence_requirements
        }
        if turn.primary_requirement_id not in requirements_by_id:
            raise ValueError(
                "InterviewTurn 引用了不存在的 requirement_id: "
                + turn.primary_requirement_id
            )
        if turn.primary_requirement_id not in target_requirement_ids:
            raise ValueError(
                "InterviewTurn 的 requirement 不属于 target: "
                + turn.primary_requirement_id
            )
        turns_by_id[turn.id] = turn

    evidence_ids: set[str] = set()
    for evidence in evidences:
        if evidence.id in evidence_ids:
            raise ValueError(f"Evidence ID 重复: {evidence.id}")
        evidence_ids.add(evidence.id)
        turn = turns_by_id.get(evidence.turn_id)
        if turn is None:
            raise ValueError(
                f"Evidence 引用了不存在的 turn_id: {evidence.turn_id}"
            )
        if len(evidence.requirement_ids) != len(set(evidence.requirement_ids)):
            raise ValueError(f"Evidence 的 requirement_ids 重复: {evidence.id}")
        target = targets_by_id[turn.target_id]
        target_requirement_ids = {
            requirement.id for requirement in target.evidence_requirements
        }
        unknown = sorted(
            set(evidence.requirement_ids) - set(requirements_by_id)
        )
        if unknown:
            raise ValueError(
                f"Evidence 引用了不存在的 requirement_id: {', '.join(unknown)}"
            )
        outside_target = sorted(
            set(evidence.requirement_ids) - target_requirement_ids
        )
        if outside_target:
            raise ValueError(
                "Evidence 的 requirement provenance 与 turn 不一致: "
                + evidence.id
            )


def _validate_blueprint(
    plan: InterviewPlan,
    profile: RoleCompetencyProfile,
    blueprint: ScoringBlueprint,
) -> None:
    _, requirements_by_id, ordered_requirement_ids = _plan_indexes(plan)
    dimension_ids = {dimension.id for dimension in profile.dimensions}

    if blueprint.role_family != profile.role_family:
        raise ValueError(
            "ScoringBlueprint 的 role_family 与 Role Pack 不一致: "
            + blueprint.role_family
        )
    if blueprint.role_profile_version != profile.version:
        raise ValueError(
            "ScoringBlueprint 的 Role Pack version 不一致: "
            + blueprint.role_profile_version
        )

    bound_ids = [binding.requirement_id for binding in blueprint.bindings]
    unknown = sorted(set(bound_ids) - set(requirements_by_id))
    if unknown:
        raise ValueError(
            "ScoringBlueprint 引用了不存在的 Requirement ID: "
            + ", ".join(unknown)
        )
    missing = [
        requirement_id
        for requirement_id in ordered_requirement_ids
        if requirement_id not in set(bound_ids)
    ]
    if missing:
        raise ValueError(
            "ScoringBlueprint 缺少 Requirement binding: " + ", ".join(missing)
        )

    for binding in blueprint.bindings:
        if binding.primary_dimension_id not in dimension_ids:
            raise ValueError(
                "ScoringBlueprint 引用了不存在的 Role Dimension: "
                + binding.primary_dimension_id
            )
        if binding.rubric_id != binding.primary_dimension_id:
            raise ValueError(
                "ScoringBlueprint 的 rubric_id 不属于绑定的 Role Dimension: "
                + binding.rubric_id
            )


def build_interview_path(
    plan: InterviewPlan,
    turns: list[InterviewTurn],
    evidences: list[Evidence],
) -> list[InterviewPathStep]:
    """Project immutable turns into a deterministic, evidence-linked path."""

    _validate_history(plan, turns, evidences)
    evidence_by_turn: dict[str, list[Evidence]] = {}
    for evidence in evidences:
        evidence_by_turn.setdefault(evidence.turn_id, []).append(evidence)

    ordered_turns = sorted(turns, key=lambda turn: (turn.sequence_number, turn.id))
    path: list[InterviewPathStep] = []
    for turn in ordered_turns:
        linked = sorted(
            evidence_by_turn.get(turn.id, []),
            key=lambda evidence: evidence.id,
        )
        if not linked:
            outcome = "unverified"
        elif any(item.polarity == "contradicting" for item in linked):
            outcome = "contradicting"
        else:
            outcome = "supporting"
        path.append(
            InterviewPathStep(
                turn_id=turn.id,
                question_mode=turn.question_mode,
                requirement_id=turn.primary_requirement_id,
                outcome=outcome,
                evidence_ids=[item.id for item in linked],
            )
        )
    return path


def build_limitations(
    snapshot: ScoreSnapshot,
) -> list[str]:
    """Build stable report limitation text from the frozen score snapshot."""

    limitations = [
        "本报告固定使用 Role Pack "
        f"{snapshot.role_profile_version} 和评分引擎 {snapshot.scoring_engine_version}。"
    ]
    unverified_dimensions = [
        radar.name
        for radar in snapshot.radar_dimensions
        if radar.level == "UNVERIFIED"
    ]
    if unverified_dimensions:
        limitations.append(
            "以下能力维度尚未得到充分验证：" + "、".join(unverified_dimensions) + "。"
        )
    if snapshot.job_match.coverage < 0.70:
        limitations.append("岗位加权覆盖率低于 70%，岗位匹配分暂不计算。")
    if not snapshot.job_match.published and snapshot.job_match.coverage >= 0.70:
        limitations.append("至少一个门槛维度尚未得到有效评估，岗位匹配分暂不计算。")
    return limitations


def _normalise_resume_profile(
    value: ResumeProfile | Mapping[str, Any] | None,
) -> ResumeProfile | None:
    if value is None:
        return None
    return ResumeProfile.model_validate(value)


def _normalise_job_profile(
    value: JobProfile | Mapping[str, Any] | None,
) -> JobProfile | None:
    if value is None:
        return None
    return JobProfile.model_validate(value)


def _candidate_experience_summary(profile: ResumeProfile | None) -> str | None:
    if profile is None:
        return None

    entries: list[str] = []
    for experience in profile.work_experiences:
        label = " / ".join(
            part.strip()
            for part in (experience.company, experience.role, experience.period or "")
            if part and part.strip()
        )
        if label:
            entries.append(label)
    for project in profile.projects:
        if project.name.strip():
            entries.append(project.name.strip())
    entries.extend(item.strip() for item in profile.other_experiences if item.strip())
    return "；".join(dict.fromkeys(entries)) or None


def build_candidate_overview(
    *,
    candidate_id: str | None,
    target_role: str | None,
    turns: Iterable[InterviewTurn],
    role_profile: RoleCompetencyProfile,
    resume_profile: ResumeProfile | Mapping[str, Any] | None = None,
    job_profile: JobProfile | Mapping[str, Any] | None = None,
) -> CandidateOverview:
    """Build the report header from typed, optional pre-interview context."""

    resume = _normalise_resume_profile(resume_profile)
    job = _normalise_job_profile(job_profile)
    resolved_target_role = (
        (target_role or "").strip()
        or (job.role.strip() if job is not None else "")
        or role_profile.display_name
    )
    education_summary = None
    if resume is not None:
        education = [item.strip() for item in resume.education if item.strip()]
        education_summary = "；".join(dict.fromkeys(education)) or None

    jd_focus: list[str] = []
    if job is not None:
        jd_focus = [
            requirement.name.strip()
            for requirement in job.requirements
            if requirement.name.strip()
        ]
        if not jd_focus:
            jd_focus = [item.strip() for item in job.responsibilities if item.strip()]
        jd_focus = list(dict.fromkeys(jd_focus))[:5]

    return CandidateOverview(
        candidate_id=(candidate_id or "未提供").strip() or "未提供",
        target_role=resolved_target_role,
        education_summary=education_summary,
        experience_summary=_candidate_experience_summary(resume),
        jd_focus=jd_focus,
        interview_rounds=len(list(turns)),
        generated_at=datetime.now(timezone.utc),
    )


def _project_legacy_narrative(
    enterprise: EnterpriseAssessment,
) -> ReportNarrativeDraft:
    """Project enterprise fields into the temporary legacy narrative shape."""

    def signal_item(signal: DecisionSignal) -> NarrativeItem:
        return NarrativeItem(
            text=signal.text,
            dimension_ids=list(signal.dimension_ids),
            evidence_ids=list(signal.evidence_ids),
        )

    development_actions = [
        DevelopmentAction(
            dimension_id=focus.dimension_id,
            current_gap=focus.reason,
            actions=[focus.question],
            acceptance_criteria=list(focus.pass_criteria),
        )
        for focus in enterprise.reinterview_plan
    ]
    return ReportNarrativeDraft(
        executive_summary=enterprise.overall_assessment,
        strengths=[signal_item(signal) for signal in enterprise.strengths],
        risks=[signal_item(signal) for signal in enterprise.risks],
        unverified_areas=[signal_item(signal) for signal in enterprise.unknowns],
        fit_contexts=[],
        development_actions=development_actions,
    )


def _normalise_inputs(
    plan: InterviewPlan,
    runtime_state: InterviewRuntimeState,
    turns: Iterable[InterviewTurn],
    evidences: Iterable[Evidence],
    claim_registry: ClaimRegistry,
) -> tuple[
    InterviewPlan,
    InterviewRuntimeState,
    list[InterviewTurn],
    list[Evidence],
    ClaimRegistry,
]:
    return (
        InterviewPlan.model_validate(plan),
        InterviewRuntimeState.model_validate(runtime_state),
        [InterviewTurn.model_validate(turn) for turn in turns],
        [Evidence.model_validate(evidence) for evidence in evidences],
        ClaimRegistry.model_validate(claim_registry),
    )


def generate_assessment_report(
    plan: InterviewPlan,
    runtime_state: InterviewRuntimeState,
    turns: list[InterviewTurn],
    evidences: list[Evidence] | None = None,
    claim_registry: ClaimRegistry | None = None,
    role_family: str = DEFAULT_ROLE_FAMILY,
    role_profile_version: str = DEFAULT_ROLE_PROFILE_VERSION,
    semantic_services: object | None = None,
    *,
    evidence: list[Evidence] | None = None,
    target_role: str | None = None,
    role_version: str | None = None,
    scoring_blueprint: ScoringBlueprint | None = None,
    candidate_id: str = "未提供",
    resume_profile: ResumeProfile | Mapping[str, Any] | None = None,
    job_profile: JobProfile | Mapping[str, Any] | None = None,
    blueprint_builder: Callable[..., Any] | None = None,
    rubric_matcher: Callable[..., Any] | None = None,
    assessment_builder: Callable[..., Any] | None = None,
    score_engine: Callable[..., Any] | None = None,
    narrative_writer: Callable[..., Any] | None = None,
) -> AssessmentReport:
    """Run the complete report stage with one deterministic score snapshot.

    Only the EnterpriseCopyDraft writer call is inside the fallback boundary.
    Role Pack, provenance, assessment, score and report-consistency failures
    therefore remain visible to the caller instead of being rewritten as prose.
    """

    if evidences is None:
        evidences = evidence
    elif evidence is not None:
        raise ValueError("evidences 与 evidence 不能同时提供")
    if evidences is None:
        raise TypeError("必须提供 evidences/evidence")
    if claim_registry is None:
        raise TypeError("必须提供 claim_registry")

    if role_version is not None:
        if (
            role_profile_version != DEFAULT_ROLE_PROFILE_VERSION
            and role_profile_version != role_version
        ):
            raise ValueError(
                "role_version 与 role_profile_version 不一致: "
                f"{role_version} != {role_profile_version}"
            )
        role_profile_version = role_version

    plan, runtime_state, turns, evidences, claim_registry = _normalise_inputs(
        plan,
        runtime_state,
        turns,
        evidences,
        claim_registry,
    )
    if not runtime_state.stop_requested:
        raise AssessmentReportStateError("面试尚未结束，不能生成最终报告")

    profile = load_role_profile(role_family, role_profile_version)
    _validate_history(plan, turns, evidences)

    blueprint_builder = _resolve_service(
        semantic_services,
        blueprint_builder,
        "blueprint_builder",
        build_scoring_blueprint,
    )
    rubric_matcher = _resolve_service(
        semantic_services,
        rubric_matcher,
        "rubric_matcher",
        match_evidence_to_rubric,
    )
    assessment_builder = _resolve_service(
        semantic_services,
        assessment_builder,
        "assessment_builder",
        build_requirement_evidence_assessments,
    )
    score_engine = _resolve_service(
        semantic_services,
        score_engine,
        "score_engine",
        calculate_score_snapshot,
    )
    enterprise_copy_writer = _resolve_service(
        semantic_services,
        narrative_writer,
        "narrative_writer",
        write_enterprise_copy,
    )

    if scoring_blueprint is None:
        blueprint = ScoringBlueprint.model_validate(
            blueprint_builder(plan, profile)
        )
    else:
        blueprint = ScoringBlueprint.model_validate(scoring_blueprint)
    _validate_blueprint(plan, profile, blueprint)

    matches = RubricMatchBatch.model_validate(
        rubric_matcher(plan, blueprint, profile, turns, evidences)
    )
    assessments = [
        RequirementEvidenceAssessment.model_validate(item)
        for item in assessment_builder(
            profile,
            blueprint,
            matches,
            evidences,
            turns,
        )
    ]

    claim_verifications = aggregate_claim_verifications(
        claim_registry,
        evidences,
    )
    claim_verifications = [
        ClaimVerification.model_validate(item) for item in claim_verifications
    ]

    snapshot = ScoreSnapshot.model_validate(
        score_engine(
            profile,
            blueprint,
            assessments,
            claim_verifications,
        )
    )
    interview_path = build_interview_path(plan, turns, evidences)
    limitations = build_limitations(snapshot)

    # Everything below is a deterministic projection over the immutable
    # snapshot, except for one bounded EnterpriseCopyDraft writer call.
    decision = derive_hiring_decision(snapshot)
    candidate_overview = build_candidate_overview(
        candidate_id=candidate_id,
        target_role=target_role,
        turns=turns,
        role_profile=profile,
        resume_profile=resume_profile,
        job_profile=job_profile,
    )
    strengths, risks, unknowns = build_decision_signals(snapshot, profile)
    evidence_excerpts = build_evidence_excerpts(snapshot, evidences, turns)
    selected_dimension_ids = select_reinterview_dimensions(snapshot, profile)

    try:
        enterprise_copy = EnterpriseCopyDraft.model_validate(
            enterprise_copy_writer(
                snapshot,
                profile,
                evidences,
                selected_dimension_ids,
            )
        )
    except ReportConsistencyError:
        raise
    except Exception:
        # A writer outage or malformed draft is recovered by the
        # dimension-specific deterministic copy.
        enterprise_copy = fallback_enterprise_copy(
            snapshot,
            profile,
            selected_dimension_ids,
            evidence=evidences,
        )

    enterprise_assessment = EnterpriseAssessment(
        decision=decision.code,
        decision_label=decision.decision_label,
        provisional_score=decision.provisional_score,
        confidence=decision.confidence,
        conditions=list(decision.conditions),
        decision_reasons=list(decision.decision_reasons),
        overall_assessment=enterprise_copy.overall_assessment,
        strengths=strengths,
        risks=risks,
        unknowns=unknowns,
        reinterview_plan=list(enterprise_copy.reinterview_plan),
        evidence_excerpts=evidence_excerpts,
    )
    # Keep consistency failures visible to the reporting stage.  In
    # particular, do not turn a contradictory enterprise result into prose.
    validate_enterprise_assessment(enterprise_assessment, snapshot, turns)
    narrative = _project_legacy_narrative(enterprise_assessment)

    return AssessmentReport(
        target_role=target_role or profile.display_name,
        score_snapshot=snapshot,
        narrative=narrative,
        candidate_overview=candidate_overview,
        enterprise_assessment=enterprise_assessment,
        interview_path=interview_path,
        assessment_limitations=limitations,
    )


__all__ = [
    "AssessmentReportSemanticServices",
    "AssessmentReportStateError",
    "build_candidate_overview",
    "build_interview_path",
    "build_limitations",
    "generate_assessment_report",
]
