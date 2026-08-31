"""Provider-independent evaluation for the reviewed Scenario RAG case bank."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Protocol

from profile_agent.schemas.scenario_calibration_schema import (
    ScenarioCalibrationCaseResult,
    ScenarioCalibrationReport,
    ScenarioCalibrationRunMetadata,
    ScenarioCalibrationStatus,
    ScenarioRetrievalCase,
)
from profile_agent.schemas.scenario_rag_schema import (
    ScenarioCandidateSet,
    ScenarioRetrievalRequest,
    ScenarioSelection,
)


DEFAULT_SCENARIO_RETRIEVAL_CASES_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "scenario_rag"
    / "retrieval_cases.json"
)
SCENARIO_CALIBRATION_CASE_COUNT = 24
SCENARIO_CALIBRATION_MIN_TOP1_ACCEPTABLE_RATE = 0.75
SCENARIO_CALIBRATION_MIN_TOP3_RECALL = 0.90
REQUIRED_CASE_IDS_BY_DIMENSION: dict[str, tuple[str, ...]] = {
    "role_dim_01": (
        "task_routing",
        "multi_agent_handoff",
        "shared_state_conflict",
        "termination_loop",
    ),
    "role_dim_02": (
        "ambiguous_business_goal",
        "workflow_decomposition",
        "success_metric",
        "human_boundary",
    ),
    "role_dim_03": (
        "memory_lifecycle",
        "knowledge_version",
        "tool_context_boundary",
        "citation_traceability",
    ),
    "role_dim_04": (
        "ai_delivery_pipeline",
        "regression_verification",
        "model_change_rollout",
        "coding_review",
    ),
    "role_dim_05": (
        "tool_idempotency",
        "prompt_injection",
        "retrieval_trust",
        "call_chain_attribution",
    ),
    "role_dim_06": (
        "llm_cost_reduction",
        "model_routing",
        "cache_quality_tradeoff",
        "latency_budget",
    ),
}


class ScenarioCalibrationRetriever(Protocol):
    """The only retriever seam accepted by calibration evaluation."""

    def retrieve(
        self,
        case: ScenarioRetrievalCase,
        *,
        as_of: date,
        limit: int,
    ) -> ScenarioCandidateSet | ScenarioSelection:
        """Return a typed ranked result or a typed retrieval outcome."""


def _payload_from_source(source: Any) -> Any:
    if source is None:
        source = DEFAULT_SCENARIO_RETRIEVAL_CASES_PATH
    if isinstance(source, (str, Path)):
        path = Path(source)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"scenario calibration fixture missing: {path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("scenario calibration fixture is invalid") from exc
    return source


def _rows_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, Mapping):
        rows = payload.get("cases")
        if isinstance(rows, list):
            return rows
        raise ValueError("scenario calibration fixture must contain a cases list")
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)):
        raise ValueError("scenario calibration fixture must be a JSON list")
    return list(payload)


def _validate_case_bank(cases: list[ScenarioRetrievalCase]) -> tuple[ScenarioRetrievalCase, ...]:
    if len(cases) != SCENARIO_CALIBRATION_CASE_COUNT:
        raise ValueError(
            "scenario calibration requires exactly "
            f"{SCENARIO_CALIBRATION_CASE_COUNT} cases"
        )

    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("scenario calibration case_id values must be unique")

    expected_ids = {
        case_id
        for dimension_ids in REQUIRED_CASE_IDS_BY_DIMENSION.values()
        for case_id in dimension_ids
    }
    actual_ids = set(case_ids)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        unexpected = sorted(actual_ids - expected_ids)
        detail: list[str] = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if unexpected:
            detail.append("unexpected=" + ",".join(unexpected))
        raise ValueError(
            "scenario calibration case IDs are not the reviewed set: "
            + "; ".join(detail)
        )

    for dimension_id, required_ids in REQUIRED_CASE_IDS_BY_DIMENSION.items():
        actual_for_dimension = {
            case.case_id
            for case in cases
            if case.primary_dimension_id == dimension_id
        }
        if actual_for_dimension != set(required_ids):
            missing = sorted(set(required_ids) - actual_for_dimension)
            unexpected = sorted(actual_for_dimension - set(required_ids))
            detail = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if unexpected:
                detail.append("unexpected=" + ",".join(unexpected))
            raise ValueError(
                f"{dimension_id} must contain the four reviewed cases: "
                + "; ".join(detail)
            )

    return tuple(cases)


def load_scenario_retrieval_cases(
    source: Any = None,
) -> tuple[ScenarioRetrievalCase, ...]:
    """Load the frozen 24-case bank and enforce its reviewed identity set."""

    rows = _rows_from_payload(_payload_from_source(source))
    cases: list[ScenarioRetrievalCase] = []
    for index, row in enumerate(rows):
        try:
            cases.append(ScenarioRetrievalCase.model_validate(row))
        except Exception as exc:
            raise ValueError(f"invalid scenario calibration case at index {index}") from exc
    return _validate_case_bank(cases)


load_scenario_calibration_cases = load_scenario_retrieval_cases


def build_scenario_calibration_request(
    case: ScenarioRetrievalCase,
) -> ScenarioRetrievalRequest:
    """Project a reviewed case into the runtime retrieval request contract."""

    case = ScenarioRetrievalCase.model_validate(case)
    return ScenarioRetrievalRequest(
        primary_dimension_id=case.primary_dimension_id,
        requirement_type=case.requirement_type,
        question_mode=case.question_mode,
        difficulty=case.difficulty,
        objective=case.query,
        semantic_query=case.query,
    )


def _typed_result(
    result: ScenarioCandidateSet | ScenarioSelection,
) -> tuple[ScenarioCalibrationStatus, str | None, tuple[str, ...]]:
    """Project one of the two typed runtime result contracts to Top-3 IDs."""

    if isinstance(result, ScenarioCandidateSet):
        status = result.status
        if status != "hit":
            return status, None, ()
        module_ids = tuple(candidate.module_id for candidate in result.candidates[:3])
        if not module_ids:
            # The Pydantic contract normally makes this unreachable.  Keep a
            # defensive explicit failure for model_construct/broken adapters.
            raise ValueError("hit candidate set must contain candidates")
        return status, module_ids[0], module_ids

    if isinstance(result, ScenarioSelection):
        status = result.status
        if status == "hit":
            if not result.module_id:
                raise ValueError("hit selection must contain module_id")
            return status, result.module_id, (result.module_id,)
        if status == "fallback":
            if not result.module_id:
                raise ValueError("fallback selection must contain module_id")
            return status, result.module_id, ()
        return status, None, ()

    raise TypeError(
        "retriever.retrieve must return ScenarioCandidateSet or ScenarioSelection"
    )


_FALLBACK_STATUSES = frozenset(
    {"fallback", "bypass", "no_match", "unavailable", "index_mismatch", "invalid_result"}
)


@dataclass(frozen=True)
class ScenarioCalibrationAcceptanceSummary:
    """Pure, explicit release-gate summary for one calibration report.

    ``forbidden_hit_count`` is retained as a Top-3 diagnostic and is therefore
    deliberately absent from the gate checks.  Runtime selection consumes only
    Top-1, so a forbidden module ranked second or third is useful follow-up
    evidence but does not fail this acceptance decision.
    """

    passed: bool
    top1_acceptable_rate: float
    top3_recall: float
    forbidden_top1_hit_count: int
    fallback_count: int
    forbidden_hit_count: int
    failed_checks: tuple[str, ...]

    @property
    def is_accepted(self) -> bool:
        """Readable alias for callers that prefer a decision-style property."""

        return self.passed

    def __bool__(self) -> bool:
        return self.passed


def ScenarioCalibrationAcceptance(
    report: ScenarioCalibrationReport | Mapping[str, Any],
) -> ScenarioCalibrationAcceptanceSummary:
    """Return the pure calibration acceptance decision for ``report``.

    The release thresholds are fixed by the reviewed plan:
    Top-1 acceptable rate >= 0.75, Top-3 recall >= 0.90, no Top-1 forbidden
    selections, and no fallback outcomes.  Top-3 forbidden hits remain a
    diagnostic and never participate in ``failed_checks``.
    """

    report = ScenarioCalibrationReport.model_validate(report)
    failed_checks: list[str] = []
    if report.top1_acceptable_rate < SCENARIO_CALIBRATION_MIN_TOP1_ACCEPTABLE_RATE:
        failed_checks.append("top1_acceptable_rate")
    if report.top3_recall < SCENARIO_CALIBRATION_MIN_TOP3_RECALL:
        failed_checks.append("top3_recall")
    if report.forbidden_top1_hit_count != 0:
        failed_checks.append("forbidden_top1")
    if report.fallback_count != 0:
        failed_checks.append("fallback")
    return ScenarioCalibrationAcceptanceSummary(
        passed=not failed_checks,
        top1_acceptable_rate=report.top1_acceptable_rate,
        top3_recall=report.top3_recall,
        forbidden_top1_hit_count=report.forbidden_top1_hit_count,
        fallback_count=report.fallback_count,
        forbidden_hit_count=report.forbidden_hit_count,
        failed_checks=tuple(failed_checks),
    )


# Keep a conventional lower-case spelling available to Python callers while
# retaining the reviewed ``ScenarioCalibrationAcceptance`` API name.
scenario_calibration_acceptance = ScenarioCalibrationAcceptance
evaluate_scenario_calibration_acceptance = ScenarioCalibrationAcceptance


def evaluate_scenario_retrieval(
    cases: Sequence[ScenarioRetrievalCase | Mapping[str, Any]],
    retriever: ScenarioCalibrationRetriever,
    as_of: date | None = None,
    metadata: ScenarioCalibrationRunMetadata | Mapping[str, Any] | None = None,
) -> ScenarioCalibrationReport:
    """Evaluate Top-1/Top-3 retrieval semantics through one typed retriever.

    The retriever is called exactly once per case using
    ``retrieve(case, *, as_of, limit=3)``.  Runtime unavailable/no-match
    states are represented by typed ``ScenarioCandidateSet`` or
    ``ScenarioSelection`` results and counted in ``fallback_count``.  Adapter
    contract violations and provider exceptions are deliberately propagated.
    """

    if isinstance(cases, (str, bytes, bytearray)):
        raise TypeError("cases must be a sequence of ScenarioRetrievalCase")
    retrieve = getattr(retriever, "retrieve", None)
    if not callable(retrieve):
        raise TypeError("retriever must provide retrieve(case, *, as_of, limit)")
    try:
        normalized_cases = [ScenarioRetrievalCase.model_validate(case) for case in cases]
    except TypeError:
        raise TypeError("cases must be a sequence of ScenarioRetrievalCase") from None
    case_ids = [case.case_id for case in normalized_cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("scenario calibration case_id values must be unique")
    effective_as_of = date.today() if as_of is None else as_of
    if not isinstance(effective_as_of, date):
        raise TypeError("as_of must be a date")
    run_metadata = (
        None
        if metadata is None
        else ScenarioCalibrationRunMetadata.model_validate(metadata)
    )
    if run_metadata is not None and run_metadata.as_of != effective_as_of:
        raise ValueError("calibration metadata as_of must match evaluation as_of")

    results: list[ScenarioCalibrationCaseResult] = []
    for case in normalized_cases:
        raw_result = retrieve(case, as_of=effective_as_of, limit=3)
        status, top1_module_id, top3_module_ids = _typed_result(raw_result)
        top1_acceptable = (
            status == "hit"
            and top1_module_id in set(case.acceptable_module_ids)
        )
        acceptable_in_top3 = (
            status == "hit"
            and bool(set(top3_module_ids) & set(case.acceptable_module_ids))
        )
        forbidden_module_ids = set(case.forbidden_module_ids)
        top1_forbidden = (
            top1_module_id is not None and top1_module_id in forbidden_module_ids
        )
        forbidden_hits = [
            module_id
            for module_id in top3_module_ids
            if module_id in forbidden_module_ids
        ]
        results.append(
            ScenarioCalibrationCaseResult(
                case_id=case.case_id,
                status=status,
                top1_module_id=top1_module_id,
                top3_module_ids=list(top3_module_ids),
                top1_acceptable=top1_acceptable,
                top1_forbidden=top1_forbidden,
                acceptable_in_top3=acceptable_in_top3,
                forbidden_hits=forbidden_hits,
                fallback=status == "fallback",
            )
        )

    count = len(results)
    top1_hits = sum(result.top1_acceptable for result in results)
    top3_hits = sum(result.acceptable_in_top3 for result in results)
    forbidden_hit_count = sum(bool(result.forbidden_hits) for result in results)
    forbidden_top1_hit_count = sum(result.top1_forbidden for result in results)
    fallback_count = sum(
        result.status in _FALLBACK_STATUSES for result in results
    )
    return ScenarioCalibrationReport(
        case_count=count,
        top1_acceptable_rate=top1_hits / count if count else 0.0,
        top3_recall=top3_hits / count if count else 0.0,
        forbidden_hit_count=forbidden_hit_count,
        forbidden_top1_hit_count=forbidden_top1_hit_count,
        fallback_count=fallback_count,
        case_results=results,
        metadata=run_metadata,
    )


__all__ = [
    "DEFAULT_SCENARIO_RETRIEVAL_CASES_PATH",
    "REQUIRED_CASE_IDS_BY_DIMENSION",
    "SCENARIO_CALIBRATION_CASE_COUNT",
    "SCENARIO_CALIBRATION_MIN_TOP1_ACCEPTABLE_RATE",
    "SCENARIO_CALIBRATION_MIN_TOP3_RECALL",
    "ScenarioCalibrationAcceptance",
    "ScenarioCalibrationAcceptanceSummary",
    "ScenarioCalibrationRetriever",
    "build_scenario_calibration_request",
    "evaluate_scenario_calibration_acceptance",
    "evaluate_scenario_retrieval",
    "load_scenario_calibration_cases",
    "load_scenario_retrieval_cases",
    "scenario_calibration_acceptance",
]
