"""Deterministic Scenario Module request, validation and fallback helpers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from profile_agent.schemas.interview_schema import AskAction, InterviewPlan
from profile_agent.schemas.scenario_rag_schema import (
    ScenarioModule,
    ScenarioRetrievalRequest,
    ScenarioSelection,
)
from profile_agent.knowledge.qdrant_scenario_store import QdrantScenarioStore
from profile_agent.services.scenario_bank_service import ScenarioCatalog


def _target_and_requirement(action: AskAction, plan: InterviewPlan):
    target = next((item for item in plan.targets if item.id == action.target_id), None)
    if target is None:
        raise ValueError(f"unknown target_id: {action.target_id}")
    requirement = next(
        (item for item in target.evidence_requirements if item.id == action.primary_requirement_id),
        None,
    )
    if requirement is None:
        raise ValueError(f"unknown primary_requirement_id: {action.primary_requirement_id}")
    if not requirement.planned_role_dimension_id:
        raise ValueError(
            f"requirement {requirement.id} must declare planned_role_dimension_id"
        )
    return target, requirement


def _query_parts(values: Iterable[str]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def build_scenario_retrieval_request(
    action: AskAction,
    plan: InterviewPlan,
    evidence_gap_tags: Iterable[str] = (),
) -> ScenarioRetrievalRequest:
    """Build the narrow, deterministic request emitted by Supervisor output."""

    target, requirement = _target_and_requirement(action, plan)
    gap_tags = _query_parts(evidence_gap_tags)
    semantic_query = "\n".join(
        _query_parts(
            [
                target.objective,
                requirement.description,
                *gap_tags,
                action.reason,
            ]
        )
    )
    return ScenarioRetrievalRequest(
        primary_dimension_id=requirement.planned_role_dimension_id,
        requirement_type=target.target_type,
        question_mode=action.question_mode,
        difficulty="intermediate",
        objective=target.objective,
        evidence_gap=gap_tags,
        semantic_query=semantic_query,
    )


def _is_valid_on(value: date, as_of: date) -> bool:
    return value.valid_from <= as_of and (
        value.valid_until is None or as_of <= value.valid_until
    )


def validate_scenario_selection(
    request: ScenarioRetrievalRequest,
    selection: ScenarioSelection,
    catalog: ScenarioCatalog,
    as_of: date | None = None,
) -> ScenarioSelection:
    """Re-read and validate the canonical Scenario/Module from the JSON bank."""

    request = ScenarioRetrievalRequest.model_validate(request)
    selection = ScenarioSelection.model_validate(selection)
    if selection.status not in {"hit", "fallback"}:
        return selection

    effective_as_of = as_of or catalog.as_of
    if not isinstance(effective_as_of, date):
        raise TypeError("as_of must be a date")
    if not selection.retrieval_unit_id:
        raise ValueError("selected scenario requires retrieval_unit_id")

    try:
        scenario, module = catalog.resolve(selection.retrieval_unit_id)
    except KeyError as exc:
        raise ValueError("retrieval_unit_id is not present in ScenarioCatalog") from exc

    if selection.scenario_id != scenario.scenario_id or selection.module_id != module.module_id:
        raise ValueError("selection identity does not match ScenarioCatalog")
    if selection.scenario is not None and selection.scenario != scenario:
        raise ValueError("scenario payload does not match ScenarioCatalog")
    if selection.module is not None and selection.module != module:
        raise ValueError("module payload does not match ScenarioCatalog")

    if scenario.role_family != request.role_family or module.role_family != request.role_family:
        raise ValueError("role_family mismatch")
    if (
        scenario.role_profile_version != request.role_profile_version
        or module.role_profile_version != request.role_profile_version
    ):
        raise ValueError("role_profile_version mismatch")
    if module.primary_dimension_id != request.primary_dimension_id:
        raise ValueError("primary_dimension_id mismatch")
    if request.question_mode not in module.supported_modes:
        raise ValueError("question_mode is not supported by selected module")
    if request.requirement_type not in module.supported_requirement_types:
        raise ValueError("requirement_type is not supported by selected module")
    if request.difficulty not in module.difficulties:
        raise ValueError("difficulty is not supported by selected module")
    if scenario.status != "active" or module.status != "active":
        raise ValueError("selected scenario/module is not active")
    if not _is_valid_on(scenario, effective_as_of) or not _is_valid_on(module, effective_as_of):
        raise ValueError("selected scenario/module is not valid on as_of")

    return selection.model_copy(update={"scenario": scenario, "module": module})


def select_fallback_module(
    request: ScenarioRetrievalRequest,
    catalog: ScenarioCatalog,
    fallback_reason: str,
) -> ScenarioSelection:
    """Select one reviewed module deterministically when retrieval is unavailable."""

    request = ScenarioRetrievalRequest.model_validate(request)
    reason = str(fallback_reason).strip()
    if not reason:
        raise ValueError("fallback_reason must not be blank")

    candidates = [
        module
        for module in catalog.active_modules
        if module.primary_dimension_id == request.primary_dimension_id
        and request.question_mode in module.supported_modes
        and request.requirement_type in module.supported_requirement_types
        and request.difficulty in module.difficulties
    ]
    if not candidates:
        raise ValueError("no reviewed fallback module satisfies hard filters")
    module = sorted(candidates, key=lambda item: (not item.default_for_dimension, item.module_id))[0]
    scenario = catalog.get_scenario(module.scenario_id)
    return ScenarioSelection(
        status="fallback",
        retrieval_unit_id=module.retrieval_unit_id,
        scenario_id=module.scenario_id,
        module_id=module.module_id,
        scenario=scenario,
        module=module,
        fallback_reason=reason,
    )


class ScenarioRetriever:
    """Coordinate one store search with canonical JSON validation/fallback."""

    def __init__(self, *, store: QdrantScenarioStore, catalog: ScenarioCatalog) -> None:
        self.store = store
        self.catalog = catalog

    def retrieve(
        self,
        request: ScenarioRetrievalRequest,
        *,
        as_of: date | None = None,
        limit: int = 3,
        reranker: object | None = None,
    ) -> ScenarioSelection:
        request = ScenarioRetrievalRequest.model_validate(request)
        effective_as_of = as_of or self.catalog.as_of
        result = self.store.search(
            request,
            as_of=effective_as_of,
            limit=limit,
            reranker=reranker,
        )
        if result.status == "hit" and result.candidates:
            candidate = result.candidates[0]
            selection = ScenarioSelection(
                status="hit",
                retrieval_unit_id=candidate.retrieval_unit_id,
                scenario_id=candidate.scenario_id,
                module_id=candidate.module_id,
                score=candidate.score,
                index_version=result.index_version,
            )
            try:
                return validate_scenario_selection(
                    request, selection, self.catalog, effective_as_of
                )
            except (KeyError, TypeError, ValueError) as exc:
                return select_fallback_module(
                    request, self.catalog, f"scenario selection validation failed: {exc}"
                )
        reason = f"scenario retrieval {result.status}"
        return select_fallback_module(request, self.catalog, reason)


__all__ = [
    "build_scenario_retrieval_request",
    "ScenarioRetriever",
    "select_fallback_module",
    "validate_scenario_selection",
]
