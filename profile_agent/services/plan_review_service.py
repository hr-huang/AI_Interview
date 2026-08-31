from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    AssessmentTargetDraft,
    EvidenceRequirement,
    InterviewPlan,
)
from profile_agent.schemas.report_schema import (
    RoleCompetencyProfile,
    ScoringBlueprint,
)
from profile_agent.services.interview_planner_service import (
    calculate_closing_buffer,
    calculate_max_questions,
)
from profile_agent.services.scoring_blueprint_service import (
    build_scoring_blueprint,
)


class TargetUpdate(BaseModel):
    target_id: str
    priority: Literal["high", "medium", "low"] | None = None
    objective: str | None = None
    time_budget_minutes: int | None = Field(default=None, ge=1)


class PlanOverrideSet(BaseModel):
    duration_minutes: int | None = None
    minimum_transfer_validations: int = Field(default=1, ge=1, le=3)
    target_updates: list[TargetUpdate] = Field(default_factory=list)
    custom_targets: list[AssessmentTargetDraft] = Field(default_factory=list)


def freeze_reviewed_plan(
    original: InterviewPlan,
    overrides: PlanOverrideSet,
    role_profile: RoleCompetencyProfile,
) -> tuple[InterviewPlan, ScoringBlueprint]:
    duration = (
        original.duration_minutes
        if overrides.duration_minutes is None
        else overrides.duration_minutes
    )
    if duration not in {30, 45, 60}:
        raise ValueError("面试时长只能是 30、45 或 60 分钟")

    updates = {item.target_id: item for item in overrides.target_updates}
    if len(updates) != len(overrides.target_updates):
        raise ValueError("同一 Target 不能重复修改")

    targets: list[AssessmentTarget] = []
    for target in original.targets:
        update = updates.pop(target.id, None)
        if update is None:
            targets.append(target)
            continue
        if target.must_cover and update.priority not in {None, "high"}:
            raise ValueError("核心目标不能降级或删除")
        if update.objective is not None and not update.objective.strip():
            raise ValueError("验证目标不能为空")
        changes = {
            "priority": update.priority,
            "objective": (
                update.objective.strip()
                if update.objective is not None
                else None
            ),
            "time_budget_minutes": update.time_budget_minutes,
        }
        targets.append(
            target.model_copy(
                update={
                    key: value
                    for key, value in changes.items()
                    if value is not None
                }
            )
        )
    if updates:
        raise ValueError("PlanOverride 引用了不存在的 Target")

    targets.extend(
        _finalize_custom_targets(
            overrides.custom_targets,
            role_profile,
            original,
        )
    )
    final = original.model_copy(
        update={
            "duration_minutes": duration,
            "max_questions": calculate_max_questions(duration),
            "closing_buffer_minutes": calculate_closing_buffer(duration),
            "targets": targets,
        }
    )
    _validate_frozen_plan(final, overrides.minimum_transfer_validations)
    return final, build_scoring_blueprint(final, role_profile)


def _finalize_custom_targets(
    drafts: list[AssessmentTargetDraft],
    role_profile: RoleCompetencyProfile,
    original: InterviewPlan,
) -> list[AssessmentTarget]:
    valid_dimensions = {item.id for item in role_profile.dimensions}
    valid_competencies = {
        competency_id
        for target in original.targets
        for competency_id in target.competency_ids
    }
    valid_claims = {
        claim_id
        for target in original.targets
        for claim_id in target.related_claim_ids
    }
    targets: list[AssessmentTarget] = []
    for target_index, draft in enumerate(drafts, start=1):
        if draft.must_cover:
            raise ValueError("企业补充目标不能设置 must_cover=True")
        if not draft.objective.strip():
            raise ValueError("企业补充目标不能为空")
        if draft.time_budget_minutes <= 0:
            raise ValueError("企业补充目标的时间预算必须大于 0")
        if not draft.evidence_requirements:
            raise ValueError("企业补充目标必须包含证据要求")
        unknown_competencies = set(draft.competency_ids) - valid_competencies
        if unknown_competencies:
            raise ValueError(
                "企业补充目标引用了不存在的 competency ID: "
                + ", ".join(sorted(unknown_competencies))
            )
        unknown_claims = set(draft.related_claim_ids) - valid_claims
        if unknown_claims:
            raise ValueError(
                "企业补充目标引用了不存在的 claim ID: "
                + ", ".join(sorted(unknown_claims))
            )

        target_id = f"custom_{target_index:02d}"
        requirements: list[EvidenceRequirement] = []
        for requirement_index, requirement in enumerate(
            draft.evidence_requirements,
            start=1,
        ):
            dimension_id = requirement.planned_role_dimension_id
            if dimension_id not in valid_dimensions:
                raise ValueError(
                    "企业补充目标必须映射到现有 Role Dimension: "
                    + str(dimension_id)
                )
            requirements.append(
                EvidenceRequirement(
                    id=f"{target_id}_req_{requirement_index:02d}",
                    description=requirement.description,
                    candidate_focus=requirement.candidate_focus,
                    planned_role_dimension_id=dimension_id,
                    requires_transfer_validation=(
                        requirement.requires_transfer_validation
                    ),
                )
            )
        targets.append(
            AssessmentTarget(
                id=target_id,
                objective=draft.objective.strip(),
                target_type=draft.target_type,
                competency_ids=list(draft.competency_ids),
                evidence_requirements=requirements,
                related_claim_ids=list(draft.related_claim_ids),
                priority=draft.priority,
                must_cover=False,
                time_budget_minutes=draft.time_budget_minutes,
                preferred_modes=list(draft.preferred_modes),
            )
        )
    return targets


def _validate_frozen_plan(
    plan: InterviewPlan,
    minimum_transfer_validations: int,
) -> None:
    transfer_count = sum(
        requirement.requires_transfer_validation
        for target in plan.targets
        for requirement in target.evidence_requirements
    )
    if transfer_count < minimum_transfer_validations:
        raise ValueError("最终计划缺少要求的迁移验证")
    available_minutes = plan.duration_minutes - plan.closing_buffer_minutes
    total_budget = sum(
        target.time_budget_minutes for target in plan.targets
    )
    if total_budget > available_minutes:
        raise ValueError("最终计划时间预算超过可用时间")
