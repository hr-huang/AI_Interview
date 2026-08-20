"""InterviewRuntimeState 的确定性初始化与更新规则。"""

from __future__ import annotations

from datetime import datetime, timezone

from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.schemas.runtime_schema import (
    InterviewRuntimeState,
    RequirementProgress,
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
