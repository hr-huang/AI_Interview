"""Prepare the candidate-safe context passed to QuestionGenerator."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from profile_agent.schemas.interview_schema import AskAction, InterviewPlan
from profile_agent.schemas.runtime_schema import InterviewTurn
from profile_agent.schemas.scenario_rag_schema import (
    LockedScenarioContext,
    QuestionProvenance,
    ScenarioRetrievalRequest,
    ScenarioSelection,
)
from profile_agent.services.constraint_selector_service import select_constraint
from profile_agent.services.scenario_bank_service import ScenarioCatalog
from profile_agent.services.scenario_retrieval_service import (
    ScenarioRetriever,
    build_scenario_retrieval_request,
    select_fallback_module,
    validate_scenario_selection,
)


_RAG_MODES = frozenset({"scenario", "system_design", "coding"})


def _revealed_ids(history: Sequence[InterviewTurn], requirement_id: str, retrieval_unit_id: str) -> list[str]:
    result: list[str] = []
    for turn in history:
        if turn.primary_requirement_id != requirement_id:
            continue
        provenance = getattr(turn, "question_provenance", None)
        if provenance is None or provenance.retrieval_unit_id != retrieval_unit_id:
            continue
        values = [*provenance.revealed_constraint_ids]
        if provenance.selected_constraint_id is not None:
            values.append(provenance.selected_constraint_id)
        for value in values:
            if value not in result:
                result.append(value)
    return result


def _context_from_selection(
    action: AskAction,
    selection: ScenarioSelection,
    *,
    catalog: ScenarioCatalog,
    revealed_ids: Sequence[str] = (),
    selected_constraint=None,
) -> LockedScenarioContext:
    if selection.status not in {"hit", "fallback"}:
        raise ValueError("scenario selection is not usable")
    if selection.scenario is None or selection.module is None:
        raise ValueError("scenario selection must contain canonical objects")
    revealed = list(dict.fromkeys([*revealed_ids, *([selected_constraint.constraint_id] if selected_constraint else [])]))
    provenance = QuestionProvenance(
        target_requirement_id=action.primary_requirement_id,
        primary_dimension_id=selection.module.primary_dimension_id,
        retrieval_unit_id=selection.retrieval_unit_id,
        scenario_id=selection.scenario_id,
        module_id=selection.module_id,
        selected_constraint_id=selected_constraint.constraint_id if selected_constraint else None,
        revealed_constraint_ids=revealed,
        retrieval_status=selection.status,
        fallback_reason=selection.fallback_reason,
    )
    return LockedScenarioContext(
        scenario_id=selection.scenario_id,
        module_id=selection.module_id,
        retrieval_unit_id=selection.retrieval_unit_id,
        business_goal=selection.scenario.business_goal,
        opening_goal=selection.module.opening_goal,
        selected_constraint=selected_constraint,
        revealed_constraint_ids=revealed,
        retrieval_status=selection.status,
        fallback_reason=selection.fallback_reason,
        scenario=selection.scenario,
        module=selection.module,
        provenance=provenance,
    )


def _follow_up_context(
    action: AskAction,
    history: Sequence[InterviewTurn],
    catalog: ScenarioCatalog,
    *,
    as_of: date,
    evidence_gap_tags: Iterable[str],
) -> LockedScenarioContext | None:
    latest = None
    latest_provenance = None
    for turn in reversed(history):
        if turn.primary_requirement_id != action.primary_requirement_id:
            continue
        provenance = getattr(turn, "question_provenance", None)
        if provenance is not None and provenance.retrieval_unit_id:
            latest, latest_provenance = turn, provenance
            break
    if latest is None or latest_provenance is None:
        return None
    if latest_provenance.retrieval_status not in {"hit", "fallback"}:
        return None
    try:
        scenario, module = catalog.resolve(latest_provenance.retrieval_unit_id)
    except KeyError:
        return None
    if scenario.status != "active" or module.status != "active":
        return None
    if scenario.valid_from > as_of or (scenario.valid_until is not None and as_of > scenario.valid_until):
        return None
    if module.valid_from > as_of or (module.valid_until is not None and as_of > module.valid_until):
        return None
    if latest_provenance.scenario_id != scenario.scenario_id or latest_provenance.module_id != module.module_id:
        return None

    revealed = _revealed_ids(history, action.primary_requirement_id, module.retrieval_unit_id)
    selected = select_constraint(
        module=module,
        constraints=catalog.constraints_for_module(module.module_id),
        evidence_gap_tags=evidence_gap_tags,
        revealed_ids=revealed,
        difficulty="intermediate",
    )
    selection = ScenarioSelection(
        status=latest_provenance.retrieval_status,
        retrieval_unit_id=module.retrieval_unit_id,
        scenario_id=scenario.scenario_id,
        module_id=module.module_id,
        scenario=scenario,
        module=module,
        fallback_reason=latest_provenance.fallback_reason,
    )
    return _context_from_selection(
        action,
        selection,
        catalog=catalog,
        revealed_ids=revealed,
        selected_constraint=selected,
    )


def prepare_question_context(
    *,
    action: AskAction,
    plan: InterviewPlan,
    history: Sequence[InterviewTurn],
    catalog: ScenarioCatalog,
    retriever: ScenarioRetriever | None = None,
    as_of: date | None = None,
    evidence_gap_tags: Iterable[str] = (),
) -> LockedScenarioContext | None:
    """Prepare one scenario context without adding another agent or graph node."""

    effective_as_of = as_of or catalog.as_of
    if not isinstance(effective_as_of, date):
        raise TypeError("as_of must be a date")
    if action.question_mode == "follow_up":
        return _follow_up_context(
            action,
            history,
            catalog,
            as_of=effective_as_of,
            evidence_gap_tags=evidence_gap_tags,
        )
    if action.question_mode not in _RAG_MODES:
        return None
    request: ScenarioRetrievalRequest = build_scenario_retrieval_request(
        action, plan, evidence_gap_tags
    )
    if retriever is None:
        selection = select_fallback_module(
            request, catalog, "scenario retriever unavailable"
        )
    else:
        selection = retriever.retrieve(request, as_of=effective_as_of)
    selection = validate_scenario_selection(
        request, selection, catalog, effective_as_of
    )
    return _context_from_selection(action, selection, catalog=catalog)


__all__ = ["prepare_question_context"]
