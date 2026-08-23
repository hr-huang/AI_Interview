"""Build the deterministic Requirement-to-Role-Dimension scoring blueprint."""

from __future__ import annotations

from collections import Counter

from pydantic import ValidationError

from profile_agent.llm import llm
from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.schemas.report_schema import (
    RoleCompetencyProfile,
    ScoringBlueprint,
    ScoringBlueprintDraft,
    RequirementScoringBinding,
)


class BlueprintValidationError(ValueError):
    """Raised when a draft cannot be reconciled with the supplied inputs."""


_SYSTEM_PROMPT = """
你是 Evidence-driven Assessment Report 的 Scoring Blueprint Builder。

你的唯一任务是把输入的每个 Evidence Requirement 绑定到最相关的一个主要
Role Dimension，并返回 ScoringBlueprintDraft。你只负责语义绑定，不负责评分。

严格约束：
- 每个 Requirement 恰好绑定一次；不能遗漏、重复或新增 Requirement。
- primary_dimension_id 只能使用输入 Role Profile 中的维度 ID。
- v1 中 rubric_id 必须严格等于 primary_dimension_id。
- 选择主要维度时，安全关键约束优先于通用架构表述：Requirement 或所属
  Target objective 一旦明确考察高风险操作、授权、审批、人工确认或失败恢复，
  应优先绑定含对应安全/可靠性 rubric 的维度，不能仅因同时出现状态、节点、
  Agent 架构等泛化词而绑定到通用编排维度。
- 只输出 ScoringBlueprintDraft；不要输出 ScoringBlueprint、权重、分数、等级、
  覆盖率、置信度或岗位匹配度。
- 不得评分，不得生成任何数值评分字段。
- JSON 根对象只能包含 "bindings"，不要添加 scoring_blueprint_draft 外层字段。
- bindings 中每一项必须完整包含 "requirement_id"、
  "primary_dimension_id" 和 "rubric_id"，其中 "rubric_id" 必须复制
  "primary_dimension_id" 的值。

正确结构示例：
{"bindings":[{"requirement_id":"req_01","primary_dimension_id":"role_dim_01","rubric_id":"role_dim_01"}]}
""".strip()


def _plan_context(plan: InterviewPlan) -> str:
    sections: list[str] = []
    for target in plan.targets:
        requirements = "\n".join(
            f"- {requirement.id}: {requirement.description}"
            for requirement in target.evidence_requirements
        )
        sections.append(
            f"Target {target.id}\n"
            f"objective: {target.objective}\n"
            f"target_type: {target.target_type}\n"
            f"Evidence Requirements:\n{requirements}"
        )
    return "\n\n".join(sections)


def _rubric_section(title: str, criteria) -> str:
    if not criteria:
        return f"{title}: 无"
    return "\n".join(
        f"{title} {criterion.id}: {criterion.text}"
        for criterion in criteria
    )


def _role_profile_context(role_profile: RoleCompetencyProfile) -> str:
    sections: list[str] = []
    for dimension in role_profile.dimensions:
        sections.append(
            f"Role Dimension {dimension.id}\n"
            f"name: {dimension.name}\n"
            f"gating: {dimension.is_gating}\n"
            f"{_rubric_section('minimum_criterion', dimension.minimum_criteria)}\n"
            f"{_rubric_section('excellence_signal', dimension.excellence_signals)}\n"
            f"{_rubric_section('critical_error', dimension.critical_errors)}\n"
            f"{_rubric_section('accepted_alternative', dimension.accepted_alternatives)}"
        )
    return "\n\n".join(sections)


def _messages(
    plan: InterviewPlan,
    role_profile: RoleCompetencyProfile,
) -> list[tuple[str, str]]:
    return [
        ("system", _SYSTEM_PROMPT),
        (
            "human",
            f"""
Role Profile:
role_family: {role_profile.role_family}
version: {role_profile.version}

Interview Plan:
{_plan_context(plan)}

Role Dimensions and rubric text:
{_role_profile_context(role_profile)}

请为每个 Requirement 选择最相关的 primary_dimension_id，并输出
ScoringBlueprintDraft。每个 Requirement 恰好绑定一次；不得评分。
""".strip(),
        ),
    ]


def _plan_requirements(plan: InterviewPlan):
    requirements = [
        requirement
        for target in plan.targets
        for requirement in target.evidence_requirements
    ]
    ids = [requirement.id for requirement in requirements]
    duplicate_ids = sorted(
        requirement_id
        for requirement_id, count in Counter(ids).items()
        if count > 1
    )
    if duplicate_ids:
        raise BlueprintValidationError(
            "InterviewPlan 包含重复的 Requirement ID: "
            + ", ".join(duplicate_ids)
        )
    return requirements


def _validate_draft(
    draft: ScoringBlueprintDraft,
    plan: InterviewPlan,
    role_profile: RoleCompetencyProfile,
) -> None:
    requirements = _plan_requirements(plan)
    plan_ids = {requirement.id for requirement in requirements}
    dimension_ids = {dimension.id for dimension in role_profile.dimensions}

    bound_ids = [binding.requirement_id for binding in draft.bindings]
    duplicate_ids = sorted(
        requirement_id
        for requirement_id, count in Counter(bound_ids).items()
        if count > 1
    )
    if duplicate_ids:
        raise BlueprintValidationError(
            "Requirement binding 重复: " + ", ".join(duplicate_ids)
        )

    unknown_requirement_ids = sorted(set(bound_ids) - plan_ids)
    if unknown_requirement_ids:
        raise BlueprintValidationError(
            "不存在的 Requirement ID: " + ", ".join(unknown_requirement_ids)
        )

    missing_requirement_ids = [
        requirement.id for requirement in requirements if requirement.id not in bound_ids
    ]
    if missing_requirement_ids:
        raise BlueprintValidationError(
            "Requirement binding 缺失: " + ", ".join(missing_requirement_ids)
        )

    for binding in draft.bindings:
        if binding.primary_dimension_id not in dimension_ids:
            raise BlueprintValidationError(
                "不存在的 Role Dimension ID: " + binding.primary_dimension_id
            )
        if binding.rubric_id != binding.primary_dimension_id:
            raise BlueprintValidationError(
                "v1 rubric_id 必须等于 primary_dimension_id: "
                f"{binding.rubric_id} != {binding.primary_dimension_id}"
            )


def build_scoring_blueprint(
    plan: InterviewPlan,
    role_profile: RoleCompetencyProfile,
    llm_client=llm,
) -> ScoringBlueprint:
    """Bind every plan Requirement once and normalize weights in Python."""

    response = llm_client.structured(
        _messages(plan, role_profile),
        ScoringBlueprintDraft,
    )
    try:
        draft = ScoringBlueprintDraft.model_validate(response)
    except ValidationError as exc:
        raise BlueprintValidationError(
            "LLM 返回的 ScoringBlueprintDraft 无效"
        ) from exc

    _validate_draft(draft, plan, role_profile)

    counts = Counter(binding.primary_dimension_id for binding in draft.bindings)
    bindings = [
        RequirementScoringBinding(
            requirement_id=binding.requirement_id,
            primary_dimension_id=binding.primary_dimension_id,
            weight_within_dimension=1.0 / counts[binding.primary_dimension_id],
            rubric_id=binding.rubric_id,
        )
        for binding in draft.bindings
    ]
    return ScoringBlueprint(
        role_family=role_profile.role_family,
        role_profile_version=role_profile.version,
        bindings=bindings,
    )
