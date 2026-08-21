"""Match interview Evidence to the versioned Role Pack rubric.

The LLM in this service is a semantic linker only.  All identifiers and
provenance are checked in Python before a match batch is returned, so later
deterministic assessment code never has to trust an unvalidated reference.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from profile_agent.llm import llm
from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.schemas.report_schema import (
    CompetencyDimensionRubric,
    RoleCompetencyProfile,
    RubricMatchBatch,
    ScoringBlueprint,
)
from profile_agent.schemas.runtime_schema import Evidence, InterviewTurn


class RubricMatchValidationError(ValueError):
    """Raised when a structured rubric match is not provenance-safe."""


_SYSTEM_PROMPT = """
你是 Evidence-to-Rubric 结构化匹配器，只负责语义匹配，不负责评分。

严格约束：
- 只使用输入中已经存在的 Evidence、Requirement、Rubric criterion、signal、
  critical error 和 accepted alternative ID；不要编造 ID。
- 不要输出任何 score、level、coverage、confidence 或岗位匹配度。
- 每条匹配必须来自该 Evidence 真实支持的 requirement_ids。
- 只有 Evidence 明确描述候选人已经发生的错误时，才可以匹配 critical error。
  未提及、没有说明、信息缺失或无法确认都不是 critical error，应保持未验证。
- 不同但合理的推理方式可以使用 accepted alternative ID。
- 未匹配的 Evidence 是允许的，不要为了覆盖率强行匹配。

请只返回符合 RubricMatchBatch 的 JSON 结构。
""".strip()


def _serialized(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )


def _serialized_list(models: list[BaseModel]) -> str:
    return json.dumps(
        [model.model_dump(mode="json") for model in models],
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )


def _index_by_id(items: list[Any], item_kind: str) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for item in items:
        item_id = getattr(item, "id", None)
        if not isinstance(item_id, str) or not item_id.strip():
            raise RubricMatchValidationError(
                f"{item_kind} 必须有非空 ID"
            )
        if item_id in indexed:
            raise RubricMatchValidationError(
                f"{item_kind} ID 重复: {item_id}"
            )
        indexed[item_id] = item
    return indexed


def _plan_indexes(
    plan: InterviewPlan,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, set[str]]]:
    targets_by_id = _index_by_id(plan.targets, "Target")
    requirements_by_id: dict[str, Any] = {}
    requirements_by_target: dict[str, set[str]] = {}

    for target in plan.targets:
        requirement_ids = requirements_by_target.setdefault(target.id, set())
        for requirement in target.evidence_requirements:
            if requirement.id in requirements_by_id:
                raise RubricMatchValidationError(
                    f"Requirement ID 重复: {requirement.id}"
                )
            requirements_by_id[requirement.id] = requirement
            requirement_ids.add(requirement.id)

    return targets_by_id, requirements_by_id, requirements_by_target


def _turn_index(
    turns: list[InterviewTurn],
    targets_by_id: dict[str, Any],
    requirements_by_id: dict[str, Any],
    requirements_by_target: dict[str, set[str]],
) -> dict[str, InterviewTurn]:
    turns_by_id = _index_by_id(turns, "InterviewTurn")

    for turn in turns:
        if turn.target_id not in targets_by_id:
            raise RubricMatchValidationError(
                f"InterviewTurn 引用了不存在的 target_id: {turn.target_id}"
            )
        if turn.primary_requirement_id not in requirements_by_id:
            raise RubricMatchValidationError(
                "InterviewTurn 引用了不存在的 requirement_id: "
                f"{turn.primary_requirement_id}"
            )
        if turn.primary_requirement_id not in requirements_by_target[turn.target_id]:
            raise RubricMatchValidationError(
                f"InterviewTurn 的 requirement 不属于 target: "
                f"{turn.primary_requirement_id}"
            )

    return turns_by_id


def _evidence_index(
    evidence: list[Evidence],
    turns_by_id: dict[str, InterviewTurn],
    requirements_by_id: dict[str, Any],
    requirements_by_target: dict[str, set[str]],
) -> dict[str, Evidence]:
    evidence_by_id = _index_by_id(evidence, "Evidence")

    for item in evidence:
        if item.turn_id not in turns_by_id:
            raise RubricMatchValidationError(
                f"Evidence 引用了不存在的 turn_id: {item.turn_id}"
            )

        turn = turns_by_id[item.turn_id]
        target_requirement_ids = requirements_by_target[turn.target_id]
        if len(item.requirement_ids) != len(set(item.requirement_ids)):
            raise RubricMatchValidationError(
                f"Evidence 的 requirement_ids 重复: {item.id}"
            )

        for requirement_id in item.requirement_ids:
            if requirement_id not in requirements_by_id:
                raise RubricMatchValidationError(
                    "Evidence 引用了不存在的 requirement_id: "
                    f"{requirement_id}"
                )
            if requirement_id not in target_requirement_ids:
                raise RubricMatchValidationError(
                    "Evidence 的 requirement provenance 与 turn 不一致: "
                    f"{item.id} -> {requirement_id}"
                )

    return evidence_by_id


def _dimension_indexes(
    role_profile: RoleCompetencyProfile,
) -> dict[str, CompetencyDimensionRubric]:
    dimensions_by_id = _index_by_id(role_profile.dimensions, "Role Dimension")
    result: dict[str, CompetencyDimensionRubric] = {}

    for dimension_id, dimension in dimensions_by_id.items():
        rubric_items = (
            dimension.minimum_criteria
            + dimension.excellence_signals
            + dimension.critical_errors
            + dimension.accepted_alternatives
        )
        rubric_ids = [item.id for item in rubric_items]
        if len(rubric_ids) != len(set(rubric_ids)):
            raise RubricMatchValidationError(
                f"Role Dimension 的 Rubric ID 重复: {dimension_id}"
            )
        result[dimension_id] = dimension

    return result


def _binding_index(
    blueprint: ScoringBlueprint,
    role_profile: RoleCompetencyProfile,
    plan_requirements: dict[str, Any],
    dimensions_by_id: dict[str, CompetencyDimensionRubric],
) -> dict[str, Any]:
    if blueprint.role_family != role_profile.role_family:
        raise RubricMatchValidationError(
            "ScoringBlueprint 的 role_family 与 Role Pack 不一致: "
            f"{blueprint.role_family}"
        )
    if blueprint.role_profile_version != role_profile.version:
        raise RubricMatchValidationError(
            "ScoringBlueprint 的 Role Pack version 不一致: "
            f"{blueprint.role_profile_version}"
        )

    bindings_by_requirement: dict[str, Any] = {}
    for binding in blueprint.bindings:
        requirement_id = binding.requirement_id
        if requirement_id not in plan_requirements:
            raise RubricMatchValidationError(
                "ScoringBlueprint 引用了不存在的 requirement_id: "
                f"{requirement_id}"
            )
        if requirement_id in bindings_by_requirement:
            raise RubricMatchValidationError(
                f"ScoringBlueprint binding 重复: {requirement_id}"
            )

        dimension = dimensions_by_id.get(binding.primary_dimension_id)
        if dimension is None:
            raise RubricMatchValidationError(
                "ScoringBlueprint 引用了不存在的 Role Dimension: "
                f"{binding.primary_dimension_id}"
            )
        if binding.rubric_id != dimension.id:
            raise RubricMatchValidationError(
                "ScoringBlueprint 的 rubric_id 不属于绑定的 Role Dimension: "
                f"{binding.rubric_id}"
            )
        bindings_by_requirement[requirement_id] = binding

    plan_requirement_ids = set(plan_requirements)
    bound_requirement_ids = set(bindings_by_requirement)
    missing = sorted(plan_requirement_ids - bound_requirement_ids)
    if missing:
        raise RubricMatchValidationError(
            "ScoringBlueprint 缺少 requirement binding: "
            + ", ".join(missing)
        )

    return bindings_by_requirement


def _validate_id_list(
    values: list[str],
    allowed: set[str],
    field_name: str,
    match_evidence_id: str,
) -> None:
    if len(values) != len(set(values)):
        raise RubricMatchValidationError(
            f"{field_name} ID 重复: {match_evidence_id}"
        )
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise RubricMatchValidationError(
            f"{field_name} 引用了不存在的 Rubric ID: "
            + ", ".join(unknown)
        )


def _validate_matches(
    batch: RubricMatchBatch,
    evidence_by_id: dict[str, Evidence],
    plan_requirements: dict[str, Any],
    bindings_by_requirement: dict[str, Any],
    dimensions_by_id: dict[str, CompetencyDimensionRubric],
) -> RubricMatchBatch:
    seen_pairs: set[tuple[str, str]] = set()

    for match in batch.matches:
        evidence_item = evidence_by_id.get(match.evidence_id)
        if evidence_item is None:
            raise RubricMatchValidationError(
                f"RubricMatch 引用了不存在的 evidence_id: {match.evidence_id}"
            )
        if match.requirement_id not in plan_requirements:
            raise RubricMatchValidationError(
                "RubricMatch 引用了不存在的 requirement_id: "
                f"{match.requirement_id}"
            )

        pair = (match.evidence_id, match.requirement_id)
        if pair in seen_pairs:
            raise RubricMatchValidationError(
                "RubricMatch 的 (evidence_id, requirement_id) 重复: "
                f"{match.evidence_id}, {match.requirement_id}"
            )
        seen_pairs.add(pair)

        if match.requirement_id not in evidence_item.requirement_ids:
            raise RubricMatchValidationError(
                "RubricMatch 的 requirement 不属于 Evidence.requirement_ids: "
                f"{match.evidence_id} -> {match.requirement_id}"
            )

        binding = bindings_by_requirement[match.requirement_id]
        dimension = dimensions_by_id[binding.primary_dimension_id]
        _validate_id_list(
            match.matched_minimum_criteria,
            {item.id for item in dimension.minimum_criteria},
            "matched_minimum_criteria",
            match.evidence_id,
        )
        _validate_id_list(
            match.matched_excellence_signals,
            {item.id for item in dimension.excellence_signals},
            "matched_excellence_signals",
            match.evidence_id,
        )
        _validate_id_list(
            match.matched_critical_errors,
            {item.id for item in dimension.critical_errors},
            "matched_critical_errors",
            match.evidence_id,
        )
        _validate_id_list(
            match.accepted_alternative_ids,
            {item.id for item in dimension.accepted_alternatives},
            "accepted_alternative_ids",
            match.evidence_id,
        )

        if (
            match.matched_critical_errors
            and evidence_item.polarity != "contradicting"
        ):
            raise RubricMatchValidationError(
                "critical error 必须有明确的 contradicting Evidence；"
                "遗漏或未提及不能作为 critical error: "
                f"{match.evidence_id}"
            )

    return batch


def _build_messages(
    plan: InterviewPlan,
    blueprint: ScoringBlueprint,
    role_profile: RoleCompetencyProfile,
    turns: list[InterviewTurn],
    evidence: list[Evidence],
) -> list[tuple[str, str]]:
    context = "\n\n".join(
        (
            f"InterviewPlan:\n{_serialized(plan)}",
            f"ScoringBlueprint:\n{_serialized(blueprint)}",
            f"RoleCompetencyProfile and full rubric:\n{_serialized(role_profile)}",
            f"InterviewTurns:\n{_serialized_list(turns)}",
            f"Evidence facts:\n{_serialized_list(evidence)}",
            "请返回 RubricMatchBatch。",
        )
    )
    return [("system", _SYSTEM_PROMPT), ("human", context)]


def match_evidence_to_rubric(
    plan: InterviewPlan,
    blueprint: ScoringBlueprint,
    role_profile: RoleCompetencyProfile,
    turns: list[InterviewTurn],
    evidence: list[Evidence],
    llm_client=llm,
) -> RubricMatchBatch:
    """Perform one structured semantic match and validate its provenance.

    Input models are only read.  The returned batch is a validated copy of the
    LLM response and may omit Evidence that has no defensible rubric match.
    """

    targets_by_id, plan_requirements, requirements_by_target = _plan_indexes(plan)
    turns_by_id = _turn_index(
        turns,
        targets_by_id,
        plan_requirements,
        requirements_by_target,
    )
    evidence_by_id = _evidence_index(
        evidence,
        turns_by_id,
        plan_requirements,
        requirements_by_target,
    )
    dimensions_by_id = _dimension_indexes(role_profile)
    bindings_by_requirement = _binding_index(
        blueprint,
        role_profile,
        plan_requirements,
        dimensions_by_id,
    )

    messages = _build_messages(
        plan=plan,
        blueprint=blueprint,
        role_profile=role_profile,
        turns=turns,
        evidence=evidence,
    )
    raw_batch = llm_client.structured(messages, RubricMatchBatch)
    try:
        batch = RubricMatchBatch.model_validate(raw_batch)
    except ValidationError as exc:
        raise RubricMatchValidationError(
            "LLM 返回的 RubricMatchBatch 不符合 schema"
        ) from exc

    return _validate_matches(
        batch=batch,
        evidence_by_id=evidence_by_id,
        plan_requirements=plan_requirements,
        bindings_by_requirement=bindings_by_requirement,
        dimensions_by_id=dimensions_by_id,
    )
