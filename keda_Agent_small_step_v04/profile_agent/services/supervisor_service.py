"""Deterministic Supervisor rules for the dynamic interview loop."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AskAction,
    AssessmentTarget,
    EvidenceRequirement,
    FinishAction,
    InterviewPlan,
    QuestionMode,
)
from profile_agent.schemas.runtime_schema import (
    Evidence,
    InterviewRuntimeState,
    InterviewTurn,
    RequirementStatus,
)
from profile_agent.services.runtime_state_service import (
    calculate_remaining_seconds,
)


Priority = Literal["high", "medium", "low"]


class SupervisorRequirementContext(BaseModel):
    """The plan and runtime facts needed to choose the next question."""

    target_id: str
    target_objective: str
    requirement_id: str
    requirement_description: str
    priority: Priority
    must_cover: bool
    status: RequirementStatus
    attempt_count: int = Field(ge=0)
    preferred_modes: list[QuestionMode] = Field(default_factory=list)
    related_claims: list[str] = Field(default_factory=list)
    evidence_summaries: list[str] = Field(default_factory=list)
    _claim_linked: bool = PrivateAttr(default=False)


# Preserve the earlier public name without adding compatibility fields to the
# formal Pydantic model.
CandidateRequirement = SupervisorRequirementContext
SupervisorCandidate = SupervisorRequirementContext


class SupervisorContext(BaseModel):
    """A bounded, fully resolved snapshot consumed by the Supervisor."""

    remaining_seconds: int = Field(ge=0)
    remaining_questions: int = Field(ge=0)
    closing_buffer_seconds: int = Field(ge=0)
    current_target_id: str | None = None
    recent_turns: list[InterviewTurn] = Field(default_factory=list)
    candidates: list[SupervisorRequirementContext] = Field(default_factory=list)
    stop_requested: bool = False
    stop_reason: str | None = None
    all_must_cover_sufficient: bool = False


_PRIORITY_RANK: dict[Priority, int] = {
    "high": 2,
    "medium": 1,
    "low": 0,
}
_ACTIVE_STATUSES = {"in_progress", "contradictory"}
_EXCLUDED_STATUSES = {"sufficient", "skipped"}


def _plan_requirements(
    plan: InterviewPlan,
) -> list[tuple[AssessmentTarget, EvidenceRequirement, int]]:
    """Flatten requirements while retaining their stable Plan order."""

    flattened: list[tuple[AssessmentTarget, EvidenceRequirement, int]] = []
    seen_requirement_ids: set[str] = set()
    order = 0

    for target in plan.targets:
        for requirement in target.evidence_requirements:
            if requirement.id in seen_requirement_ids:
                raise ValueError(
                    f"Plan 中存在重复的 requirement_id: {requirement.id}"
                )
            seen_requirement_ids.add(requirement.id)
            flattened.append((target, requirement, order))
            order += 1

    return flattened


def _validate_runtime_progress(
    plan_requirement_rows: list[tuple[AssessmentTarget, EvidenceRequirement, int]],
    runtime_state: InterviewRuntimeState,
) -> None:
    """Reject any drift between the immutable Plan and runtime projection."""

    for key, progress in runtime_state.requirement_progress.items():
        if key != progress.requirement_id:
            raise ValueError(
                "runtime progress key 与 requirement_id 不一致: "
                f"{key} != {progress.requirement_id}"
            )

    plan_ids = {requirement.id for _, requirement, _ in plan_requirement_rows}
    runtime_ids = set(runtime_state.requirement_progress)
    if plan_ids != runtime_ids:
        missing = sorted(plan_ids - runtime_ids)
        extra = sorted(runtime_ids - plan_ids)
        raise ValueError(
            "Plan requirement IDs 与 runtime progress keys 必须完全一致; "
            f"missing={missing}, extra={extra}"
        )


def _evidence_summaries(
    evidences: Sequence[Evidence],
) -> dict[str, list[str]]:
    summaries: dict[str, list[str]] = {}
    for evidence in evidences:
        summary = evidence.observation or evidence.source_excerpt
        for requirement_id in evidence.requirement_ids:
            summaries.setdefault(requirement_id, []).append(summary)
    return summaries


def _claim_texts(
    claim_registry: ClaimRegistry | None,
) -> dict[str, str]:
    if claim_registry is None:
        return {}
    return {claim.id: claim.text for claim in claim_registry.claims}


def _candidate_sort_key(
    candidate: SupervisorRequirementContext,
    *,
    current_target_id: str | None,
    plan_order: int,
) -> tuple[int, int, int, int, int, int]:
    return (
        -int(candidate.must_cover),
        -_PRIORITY_RANK[candidate.priority],
        -int(candidate.status in _ACTIVE_STATUSES),
        -int(_claim_is_linked(candidate)),
        -int(candidate.target_id == current_target_id),
        plan_order,
    )


def _claim_is_linked(candidate: SupervisorRequirementContext) -> bool:
    return candidate._claim_linked or bool(candidate.related_claims)


def build_supervisor_context(
    plan: InterviewPlan,
    runtime_state: InterviewRuntimeState,
    turns: Sequence[InterviewTurn],
    evidences: Sequence[Evidence],
    claim_registry: ClaimRegistry | None = None,
    now: datetime | None = None,
    max_attempts: int = 2,
) -> SupervisorContext:
    """Build the bounded input used by :func:`decide_next_action`."""

    if max_attempts <= 0:
        raise ValueError("max_attempts 必须大于 0")

    plan_requirement_rows = _plan_requirements(plan)
    _validate_runtime_progress(plan_requirement_rows, runtime_state)

    claim_text_by_id = _claim_texts(claim_registry)
    evidence_summary_by_requirement = _evidence_summaries(evidences)
    candidates_with_order: list[tuple[SupervisorRequirementContext, int]] = []

    for target, requirement, plan_order in plan_requirement_rows:
        progress = runtime_state.requirement_progress[requirement.id]
        if progress.status in _EXCLUDED_STATUSES:
            continue
        if progress.attempt_count >= max_attempts:
            continue

        related_claim_texts = [
            claim_text_by_id[claim_id]
            for claim_id in target.related_claim_ids
            if claim_id in claim_text_by_id
        ]
        candidate = SupervisorRequirementContext(
            target_id=target.id,
            target_objective=target.objective,
            requirement_id=requirement.id,
            requirement_description=requirement.description,
            priority=target.priority,
            must_cover=target.must_cover,
            status=progress.status,
            attempt_count=progress.attempt_count,
            preferred_modes=list(target.preferred_modes),
            related_claims=related_claim_texts,
            evidence_summaries=evidence_summary_by_requirement.get(
                requirement.id,
                [],
            ),
        )
        candidate._claim_linked = bool(target.related_claim_ids)
        candidates_with_order.append((candidate, plan_order))

    candidates_with_order.sort(
        key=lambda item: _candidate_sort_key(
            item[0],
            current_target_id=runtime_state.current_target_id,
            plan_order=item[1],
        )
    )

    must_cover_statuses = [
        progress.status
        for target, requirement, _ in plan_requirement_rows
        if target.must_cover
        for progress in [runtime_state.requirement_progress[requirement.id]]
    ]
    all_must_cover_sufficient = bool(must_cover_statuses) and all(
        status == "sufficient" for status in must_cover_statuses
    )

    closing_buffer_seconds = max(0, plan.closing_buffer_minutes * 60)
    remaining_seconds = calculate_remaining_seconds(
        plan,
        runtime_state,
        now=now,
    )

    return SupervisorContext(
        remaining_seconds=remaining_seconds,
        remaining_questions=max(
            0,
            plan.max_questions - runtime_state.question_count,
        ),
        closing_buffer_seconds=closing_buffer_seconds,
        current_target_id=runtime_state.current_target_id,
        recent_turns=list(turns)[-3:],
        candidates=[
            candidate for candidate, _ in candidates_with_order
        ],
        stop_requested=runtime_state.stop_requested,
        stop_reason=runtime_state.stop_reason,
        all_must_cover_sufficient=all_must_cover_sufficient,
    )


def _question_mode(candidate: SupervisorRequirementContext) -> QuestionMode:
    if candidate.attempt_count > 0 and candidate.status in _ACTIVE_STATUSES:
        return "follow_up"

    if _claim_is_linked(candidate) and "project_deep_dive" in candidate.preferred_modes:
        return "project_deep_dive"

    for mode in candidate.preferred_modes:
        if mode != "follow_up":
            return mode

    return "scenario"


def _finish(reason: str) -> FinishAction:
    return FinishAction(reason=reason)


def decide_next_action(
    context: SupervisorContext,
) -> AskAction | FinishAction:
    """Apply the Supervisor hard-stop and deterministic selection rules."""

    if context.stop_requested:
        return _finish(
            f"stop_requested: {context.stop_reason or 'stop_requested'}"
        )

    if context.remaining_seconds <= context.closing_buffer_seconds:
        return _finish("进入 closing buffer")

    if context.remaining_questions <= 0:
        return _finish("question limit exhausted")

    if context.all_must_cover_sufficient:
        return _finish("all must_cover requirements are sufficient")

    if not context.candidates:
        return _finish("no candidate requirement remains")

    candidate = context.candidates[0]
    mode = _question_mode(candidate)
    if mode == "follow_up":
        reason = f"follow up on {candidate.requirement_id}"
    elif mode == "project_deep_dive":
        reason = f"verify linked claim through project deep dive: {candidate.requirement_id}"
    else:
        reason = f"cover {candidate.requirement_id} with {mode}"

    return AskAction(
        target_id=candidate.target_id,
        primary_requirement_id=candidate.requirement_id,
        question_mode=mode,
        reason=reason,
    )
