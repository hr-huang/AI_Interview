"""InterviewRuntimeState 的确定性初始化与更新规则。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.schemas.runtime_schema import (
    InterviewRuntimeState,
    RequirementProgress,
    RequirementStatus,
)


def _requirement_to_target(plan: InterviewPlan) -> dict[str, str]:
    if not plan.targets:
        raise ValueError("InterviewPlan 至少包含一个 Target")

    target_ids: set[str] = set()
    requirement_to_target: dict[str, str] = {}

    for target in plan.targets:
        if target.id in target_ids:
            raise ValueError(f"重复的 target_id: {target.id}")
        target_ids.add(target.id)

        for requirement in target.evidence_requirements:
            if requirement.id in requirement_to_target:
                raise ValueError(
                    f"重复的 requirement_id: {requirement.id}"
                )
            requirement_to_target[requirement.id] = target.id

    if not requirement_to_target:
        raise ValueError("InterviewPlan 至少包含一个 Evidence Requirement")

    return requirement_to_target


def initialize_runtime_state(
    plan: InterviewPlan,
    started_at: datetime | None = None,
) -> InterviewRuntimeState:
    requirement_to_target = _requirement_to_target(plan)

    progress = {
        requirement_id: RequirementProgress(requirement_id=requirement_id)
        for requirement_id in requirement_to_target
    }

    return InterviewRuntimeState(
        started_at=started_at or datetime.now(timezone.utc),
        requirement_progress=progress,
    )


def calculate_remaining_seconds(
    plan: InterviewPlan,
    runtime_state: InterviewRuntimeState,
    now: datetime | None = None,
) -> int:
    current_time = now or datetime.now(timezone.utc)
    elapsed_seconds = int(
        (current_time - runtime_state.started_at).total_seconds()
    )
    total_seconds = plan.duration_minutes * 60
    return max(0, total_seconds - max(0, elapsed_seconds))


def _assert_requirement_progress_consistency(
    runtime_state: InterviewRuntimeState,
) -> None:
    for key, progress in runtime_state.requirement_progress.items():
        if key != progress.requirement_id:
            raise ValueError(
                "requirement_progress key 与 requirement_id 不一致: "
                f"{key} != {progress.requirement_id}"
            )


def _validate_updated_runtime_state(
    updated: InterviewRuntimeState,
) -> InterviewRuntimeState:
    validated = InterviewRuntimeState.model_validate(updated.model_dump())
    _assert_requirement_progress_consistency(validated)
    return validated


def record_question_asked(
    plan: InterviewPlan,
    runtime_state: InterviewRuntimeState,
    target_id: str,
    primary_requirement_id: str,
    now: datetime | None = None,
) -> InterviewRuntimeState:
    requirement_to_target = _requirement_to_target(plan)
    _assert_requirement_progress_consistency(runtime_state)

    if runtime_state.stop_requested:
        raise ValueError("stop_requested=True，不能继续记录问题")

    if calculate_remaining_seconds(plan, runtime_state, now=now) <= 0:
        raise ValueError("面试时间已耗尽，不能继续记录问题")

    if primary_requirement_id not in requirement_to_target:
        raise ValueError(
            f"不存在的 requirement_id: {primary_requirement_id}"
        )

    expected_target_id = requirement_to_target[primary_requirement_id]
    if target_id != expected_target_id:
        raise ValueError(
            f"requirement {primary_requirement_id} 不属于 target {target_id}"
        )

    if primary_requirement_id not in runtime_state.requirement_progress:
        raise ValueError(
            "RuntimeState 缺少 requirement_progress: "
            f"{primary_requirement_id}"
        )

    if runtime_state.question_count >= plan.max_questions:
        raise ValueError("已达到问题数量上限")

    updated = runtime_state.model_copy(deep=True)
    updated.question_count += 1
    updated.current_target_id = target_id

    if target_id not in updated.visited_target_ids:
        updated.visited_target_ids.append(target_id)

    progress = updated.requirement_progress[primary_requirement_id]
    progress.attempt_count += 1
    if progress.status == "not_started":
        progress.status = "in_progress"

    return _validate_updated_runtime_state(updated)


def _append_unique(existing: list[str], values: Iterable[str]) -> None:
    seen = set(existing)
    for value in values:
        if value not in seen:
            existing.append(value)
            seen.add(value)


def record_requirement_evidence(
    runtime_state: InterviewRuntimeState,
    requirement_id: str,
    status: RequirementStatus,
    supporting_evidence_ids: list[str],
    contradicting_evidence_ids: list[str],
    known_evidence_ids: set[str],
) -> InterviewRuntimeState:
    _assert_requirement_progress_consistency(runtime_state)

    if requirement_id not in runtime_state.requirement_progress:
        raise ValueError(f"不存在的 requirement_id: {requirement_id}")

    supporting_ids = set(supporting_evidence_ids)
    contradicting_ids = set(contradicting_evidence_ids)
    overlap = supporting_ids & contradicting_ids
    if overlap:
        evidence_ids = ", ".join(sorted(overlap))
        raise ValueError(
            "同一 evidence_id 不能同时出现在 supporting 与 "
            f"contradicting 输入中: {evidence_ids}"
        )

    referenced_ids = supporting_ids | contradicting_ids
    missing_ids = referenced_ids - known_evidence_ids
    if missing_ids:
        missing = ", ".join(sorted(missing_ids))
        raise ValueError(f"不存在的 evidence_id: {missing}")

    updated = runtime_state.model_copy(deep=True)
    progress = updated.requirement_progress[requirement_id]
    progress.status = status
    _append_unique(
        progress.supporting_evidence_ids,
        supporting_evidence_ids,
    )
    _append_unique(
        progress.contradicting_evidence_ids,
        contradicting_evidence_ids,
    )

    return _validate_updated_runtime_state(updated)


def request_stop(
    runtime_state: InterviewRuntimeState,
    reason: str,
) -> InterviewRuntimeState:
    _assert_requirement_progress_consistency(runtime_state)

    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError("stop reason 不能为空")

    updated = runtime_state.model_copy(deep=True)
    updated.stop_requested = True
    updated.stop_reason = clean_reason
    return _validate_updated_runtime_state(updated)
