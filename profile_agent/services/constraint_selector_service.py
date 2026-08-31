"""Deterministic selection and runtime persistence for scenario constraints."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from profile_agent.schemas.scenario_rag_schema import (
    ScenarioConstraint,
    ScenarioModule,
)


_DIFFICULTY_RANK = {"foundation": 0, "intermediate": 1, "advanced": 2}


def _normalise_tags(values: Iterable[str]) -> set[str]:
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def select_constraint(
    *,
    module: ScenarioModule,
    constraints: Sequence[ScenarioConstraint],
    evidence_gap_tags: Sequence[str] = (),
    revealed_ids: Sequence[str] = (),
    difficulty: str | None = None,
) -> ScenarioConstraint | None:
    """Return at most one reviewed, unused constraint.

    Gap overlap is the primary signal.  When no structured gap is available,
    the declared ``constraint_ids`` order is the review order.  No model call
    or fuzzy semantic decision is made here.
    """

    if not isinstance(module, ScenarioModule):
        module = ScenarioModule.model_validate(module)
    revealed = {str(value) for value in revealed_ids}
    requested_tags = _normalise_tags(evidence_gap_tags)
    module_order = {constraint_id: index for index, constraint_id in enumerate(module.constraint_ids)}
    eligible: list[tuple[tuple[int, int, int, str], ScenarioConstraint]] = []
    requested_rank = _DIFFICULTY_RANK.get(difficulty or "intermediate", 1)
    for fallback_index, constraint in enumerate(constraints):
        if not isinstance(constraint, ScenarioConstraint):
            constraint = ScenarioConstraint.model_validate(constraint)
        if constraint.module_id != module.module_id or constraint.scenario_id != module.scenario_id:
            continue
        if constraint.status != "active" or constraint.constraint_id in revealed:
            continue
        overlap = len(requested_tags & _normalise_tags(constraint.evidence_gap_tags))
        distance = abs(_DIFFICULTY_RANK.get(constraint.difficulty, 1) - requested_rank)
        declared_order = module_order.get(constraint.constraint_id, len(module_order) + fallback_index)
        # Negative overlap sorts exact matches first; the remaining keys make
        # the result stable even when a fixture omits module constraint IDs.
        eligible.append(((-overlap, distance, declared_order, constraint.constraint_id), constraint))
    eligible.sort(key=lambda item: item[0])
    return eligible[0][1] if eligible else None


__all__ = ["select_constraint"]
