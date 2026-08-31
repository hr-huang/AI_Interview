"""Prepare the candidate-safe context passed to QuestionGenerator."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
import re
import unicodedata

from profile_agent.schemas.interview_schema import (
    AskAction,
    InterviewPlan,
    normalize_candidate_focus,
)
from profile_agent.schemas.runtime_schema import InterviewTurn
from profile_agent.schemas.scenario_rag_schema import (
    LockedScenarioContext,
    QuestionProvenance,
    ScenarioConstraintProjection,
    ScenarioRetrievalRequest,
    ScenarioSelection,
)
from profile_agent.services.constraint_selector_service import select_constraint
from profile_agent.services.scenario_bank_service import ScenarioCatalog
from profile_agent.services.scenario_retrieval_service import (
    NoCompatibleScenarioModuleError,
    ScenarioRetriever,
    build_scenario_retrieval_request,
    select_fallback_module,
    validate_scenario_selection,
)


_RAG_MODES = frozenset({"scenario", "system_design", "coding"})
_NEUTRAL_CANDIDATE_FOCUS = "整体方案设计"


_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+")
_DESCRIPTION_PREFIXES = (
    "验证候选人能否",
    "验证候选人",
    "能够说明",
    "验证",
    "考察",
)
_DESCRIPTION_VERB_PREFIXES = ("请说明", "请描述", "能否", "说明", "描述")


def _text_features(value: str) -> tuple[str, set[str], set[str]]:
    """Return punctuation/whitespace-insensitive CJK bigrams and ASCII tokens."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    chinese = "".join(
        character for character in normalized if "\u3400" <= character <= "\u9fff"
    )
    bigrams = {chinese[index:index + 2] for index in range(len(chinese) - 1)}
    return chinese, bigrams, set(_ASCII_TOKEN_RE.findall(normalized))


def _compact_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _longest_common_chinese_substring(left: str, right: str) -> int:
    left_chinese, _, _ = _text_features(left)
    right_chinese, _, _ = _text_features(right)
    previous = [0] * (len(right_chinese) + 1)
    longest = 0
    for left_character in left_chinese:
        current = [0]
        for index, right_character in enumerate(right_chinese, start=1):
            length = previous[index - 1] + 1 if left_character == right_character else 0
            current.append(length)
            longest = max(longest, length)
        previous = current
    return longest


def _hidden_groups(
    scenario,
    module,
    catalog: ScenarioCatalog,
) -> tuple[list[str], list[str]]:
    constraints = catalog.constraints_for_module(module.module_id)
    short_reviewed_signals = [*module.evidence_signals, *module.critical_errors]
    protected_facts = [
        *scenario.base_constraints,
        *(constraint.description for constraint in constraints),
        *(constraint.fact or "" for constraint in constraints),
    ]
    return short_reviewed_signals, protected_facts


def _contains_untrusted_focus(
    value: str,
    short_reviewed_signals: Sequence[str],
    protected_facts: Sequence[str],
) -> bool:
    candidate_compact = _compact_text(value)
    _, candidate_bigrams, candidate_ascii = _text_features(value)

    # Signals/errors are reviewed labels: copying the complete label is never
    # allowed, even for three-character labels such as "新鲜度".
    for hidden_value in short_reviewed_signals:
        hidden_compact = _compact_text(hidden_value)
        if hidden_compact and hidden_compact in candidate_compact:
            return True
        _, _, hidden_ascii = _text_features(hidden_value)
        if len(candidate_ascii & hidden_ascii) >= 2:
            return True

    for hidden_value in protected_facts:
        hidden_compact = _compact_text(hidden_value)
        if hidden_compact and hidden_compact in candidate_compact:
            return True
        if _longest_common_chinese_substring(value, hidden_value) >= 4:
            return True

        _, hidden_bigrams, hidden_ascii = _text_features(hidden_value)
        shared_bigrams = len(candidate_bigrams & hidden_bigrams)
        # Short Planner copy needs three distributed relationships and at
        # least 25% coverage.  A generic two-character term such as "退款"
        # contributes one bigram, so it cannot trigger this branch.
        if (
            candidate_bigrams
            and shared_bigrams >= 3
            and shared_bigrams / len(candidate_bigrams) >= 0.25
        ):
            return True
        if len(candidate_ascii & hidden_ascii) >= 2:
            return True
    return False


def _focus_from_description(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    focus = value.replace("？", "").replace("?", "").strip()
    for prefix in _DESCRIPTION_PREFIXES:
        if focus.startswith(prefix):
            focus = focus[len(prefix):].strip("：: ")
            break
    for prefix in _DESCRIPTION_VERB_PREFIXES:
        if focus.startswith(prefix):
            focus = focus[len(prefix):].strip("：: ")
            break
    return normalize_candidate_focus(focus)


def _requirement_candidate_copy(
    plan: InterviewPlan,
    action: AskAction,
) -> tuple[str | None, str | None]:
    for target in plan.targets:
        if target.id != action.target_id:
            continue
        for requirement in target.evidence_requirements:
            if requirement.id == action.primary_requirement_id:
                return requirement.candidate_focus, requirement.description
    return None, None


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
    candidate_focus: str | None = None,
    requirement_description: str | None = None,
) -> LockedScenarioContext:
    if selection.status not in {"hit", "fallback"}:
        raise ValueError("scenario selection is not usable")
    try:
        canonical_scenario, canonical_module = catalog.resolve(selection.retrieval_unit_id)
    except KeyError as exc:
        raise ValueError("scenario selection is not canonical") from exc
    if (
        selection.scenario_id != canonical_scenario.scenario_id
        or selection.module_id != canonical_module.module_id
    ):
        raise ValueError("scenario selection identity does not match catalog")
    short_reviewed_signals, protected_facts = _hidden_groups(
        canonical_scenario,
        canonical_module,
        catalog,
    )
    safe_candidate_focus = normalize_candidate_focus(candidate_focus)
    if safe_candidate_focus is None:
        safe_candidate_focus = _focus_from_description(requirement_description)
    if (
        safe_candidate_focus is None
        or _contains_untrusted_focus(
            safe_candidate_focus,
            short_reviewed_signals,
            protected_facts,
        )
    ):
        safe_candidate_focus = _NEUTRAL_CANDIDATE_FOCUS
    revealed = list(dict.fromkeys([*revealed_ids, *([selected_constraint.constraint_id] if selected_constraint else [])]))
    selected_projection = (
        ScenarioConstraintProjection.from_constraint(selected_constraint)
        if selected_constraint is not None
        else None
    )
    provenance = QuestionProvenance(
        target_requirement_id=action.primary_requirement_id,
        primary_dimension_id=canonical_module.primary_dimension_id,
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
        primary_dimension_id=selection.module.primary_dimension_id,
        retrieval_unit_id=selection.retrieval_unit_id,
        business_goal=canonical_scenario.business_goal,
        candidate_brief=canonical_scenario.candidate_brief,
        candidate_focus=safe_candidate_focus,
        selected_constraint=selected_projection,
        revealed_constraint_ids=revealed,
        retrieval_status=selection.status,
        fallback_reason=selection.fallback_reason,
        provenance=provenance,
    )


def _follow_up_context(
    action: AskAction,
    history: Sequence[InterviewTurn],
    catalog: ScenarioCatalog,
    *,
    as_of: date,
    evidence_gap_tags: Iterable[str],
    candidate_focus: str | None,
    requirement_description: str | None,
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
        candidate_focus=candidate_focus,
        requirement_description=requirement_description,
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
    candidate_focus, requirement_description = _requirement_candidate_copy(plan, action)
    if action.question_mode == "follow_up":
        return _follow_up_context(
            action,
            history,
            catalog,
            as_of=effective_as_of,
            evidence_gap_tags=evidence_gap_tags,
            candidate_focus=candidate_focus,
            requirement_description=requirement_description,
        )
    if action.question_mode not in _RAG_MODES:
        return None
    request: ScenarioRetrievalRequest = build_scenario_retrieval_request(
        action, plan, evidence_gap_tags
    )
    try:
        if retriever is None:
            selection = select_fallback_module(
                request, catalog, "scenario retriever unavailable"
            )
        else:
            selection = retriever.retrieve(request, as_of=effective_as_of)
    except NoCompatibleScenarioModuleError:
        # This is an expected coverage boundary, not catalog corruption.  The
        # locked Requirement still defines what must be asked, so continue
        # without Scenario grounding rather than forcing an incompatible
        # reviewed module or failing the candidate session.
        return None
    if selection.status in {"hit", "fallback"}:
        # Embedded objects cross the retrieval boundary untrusted.  Validate
        # only their stable identity, then rehydrate canonical catalog assets.
        selection = selection.model_copy(update={"scenario": None, "module": None})
    selection = validate_scenario_selection(
        request, selection, catalog, effective_as_of
    )
    return _context_from_selection(
        action,
        selection,
        catalog=catalog,
        candidate_focus=candidate_focus,
        requirement_description=requirement_description,
    )


__all__ = ["prepare_question_context"]
