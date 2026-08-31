"""Select frozen candidate answers from Supervisor semantics only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from profile_agent.calibration.schemas import ScriptedAnswerRule
from profile_agent.schemas.interview_schema import AskAction, InterviewPlan


class ScriptedAnswerSelectionError(ValueError):
    """Raised when a frozen answer cannot be selected deterministically."""


def _parse_ask_action(payload: Mapping[str, object]) -> AskAction:
    try:
        return AskAction.model_validate(payload.get("action"))
    except (TypeError, ValidationError) as error:
        raise ScriptedAnswerSelectionError(
            "interrupt payload 缺少有效 AskAction"
        ) from error


def _requirements_by_id(plan: InterviewPlan) -> dict[str, Any]:
    requirements: dict[str, Any] = {}
    for target in plan.targets:
        for requirement in target.evidence_requirements:
            if requirement.id in requirements:
                raise ScriptedAnswerSelectionError(
                    f"InterviewPlan 中 Requirement ID 重复: {requirement.id}"
                )
            requirements[requirement.id] = requirement
    return requirements


def select_scripted_answer(
    *,
    payload: Mapping[str, object],
    plan: InterviewPlan,
    rules: list[ScriptedAnswerRule],
    usage_counts: Mapping[str, int],
) -> tuple[str, str]:
    """Return the best unused scripted answer without inspecting question text."""

    action = _parse_ask_action(payload)
    requirements = _requirements_by_id(plan)
    requirement = requirements.get(action.primary_requirement_id)
    if requirement is None:
        raise ScriptedAnswerSelectionError(
            "AskAction 引用的 Requirement 不存在: "
            + action.primary_requirement_id
        )

    haystack = f"{requirement.description}\n{action.reason}".casefold()
    candidates: list[tuple[int, int, ScriptedAnswerRule]] = []
    fallbacks: list[tuple[int, ScriptedAnswerRule]] = []
    for index, rule in enumerate(rules):
        if usage_counts.get(rule.id, 0) >= rule.max_uses:
            continue
        if "*" in rule.match_any:
            fallbacks.append((index, rule))
            continue
        hit_count = sum(term.casefold() in haystack for term in rule.match_any)
        if hit_count:
            candidates.append((-hit_count, index, rule))

    if not candidates:
        if fallbacks:
            _, selected = min(fallbacks)
            return selected.answer, selected.id
        raise ScriptedAnswerSelectionError(
            "没有脚本回答可匹配 requirement "
            + action.primary_requirement_id
            + f"；description={requirement.description}；reason={action.reason}"
        )

    _, _, selected = min(candidates)
    return selected.answer, selected.id


__all__ = [
    "ScriptedAnswerSelectionError",
    "select_scripted_answer",
]
