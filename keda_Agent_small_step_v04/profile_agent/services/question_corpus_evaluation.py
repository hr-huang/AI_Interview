"""Offline evaluation for the versioned question-corpus retrieval intents.

The evaluator deliberately sits beside the production retriever.  It accepts
already-produced candidate lists (or a small injected fake/local adapter),
checks the candidate-safe retrieval contract, and computes deterministic
metrics without constructing an embedding provider or a Qdrant client.

The canonical corpus is still a candidate release: its records are
``needs_review`` and the evaluator therefore does not apply the production
``active`` filter.  Lifecycle, rights, and publication gates remain the job of
``question_corpus_governance`` and the CLI's read-only audit path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import inspect
import json
import math
from pathlib import Path
import re
from typing import Any

from profile_agent.schemas.question_rag_schema import (
    CORPUS_AS_OF,
    CORPUS_ROLE,
    CORPUS_ROLE_VERSION,
    InterviewQuestionRecord,
    LabeledQuestionIntent,
    QuestionModePolicy,
    QuestionRetrievalIntent,
)
from profile_agent.services.question_retrieval_service import (
    build_query_embedding_text,
)


MAX_EVALUATION_K = 3
METRIC_DECIMAL_PLACES = 4
EVALUATION_SCHEMA_VERSION = "question-corpus-evaluation-v1"

DEFAULT_RETRIEVAL_INTENTS_PATH = Path(
    "profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/retrieval_intents.jsonl"
)

HARD_NEGATIVE_CATEGORIES: tuple[str, ...] = (
    "wrong_dimension",
    "wrong_mode",
    "expired",
    "retired",
    "duplicate",
    "wrong_role",
    "low_trust",
)

# These identifiers are intentionally synthetic and auditable.  They are not
# records in the 30-question release and are never passed to an embedding
# input or a production store.  Task 9 may replace this catalogue with richer
# fixture records while keeping the label contract unchanged.
_FIXTURE_MODES = (
    "foundation",
    "project_deep_dive",
    "scenario",
    "system_design",
    "coding",
    "follow_up",
)
_FIXTURE_DIMENSIONS = (
    *("role_dim_01" for _ in range(6)),
    *("role_dim_02" for _ in range(5)),
    *("role_dim_03" for _ in range(6)),
    *("role_dim_04" for _ in range(4)),
    *("role_dim_05" for _ in range(6)),
    *("role_dim_06" for _ in range(3)),
)
_FIXTURE_REQUESTED_MODES = (
    "foundation", "project_deep_dive", "scenario", "system_design", "follow_up", "scenario",
    "foundation", "scenario", "project_deep_dive", "scenario", "follow_up",
    "foundation", "scenario", "system_design", "coding", "follow_up", "project_deep_dive",
    "project_deep_dive", "scenario", "coding", "follow_up",
    "foundation", "scenario", "system_design", "scenario", "follow_up", "project_deep_dive",
    "coding", "system_design", "follow_up",
)


def _build_synthetic_hard_negative_catalog() -> dict[str, dict[str, Any]]:
    """Build the frozen, per-intent hard-negative fixture registry.

    The registry is independent of labels at evaluation time.  Its IDs are
    deliberately scoped to the question they challenge so a label cannot
    accidentally reuse a negative with the wrong dimension or gold target.
    """

    catalog: dict[str, dict[str, Any]] = {}
    for number in range(1, 31):
        question_id = f"q{number:03d}"
        dimension_id = _FIXTURE_DIMENSIONS[number - 1]
        requested_mode = _FIXTURE_REQUESTED_MODES[number - 1]
        wrong_dimension_number = (number % 6) + 1
        current_dimension_number = int(dimension_id.rsplit("_", 1)[-1])
        if wrong_dimension_number == current_dimension_number:
            wrong_dimension_number = (current_dimension_number % 6) + 1
        wrong_dimension = f"role_dim_{wrong_dimension_number:02d}"
        # All release modes intentionally omit follow_up from their compatible
        # mode lists; follow_up is therefore a safe wrong-mode fixture except
        # when it is the requested mode, where coding is incompatible instead.
        wrong_mode = "coding" if requested_mode == "follow_up" else "follow_up"
        if question_id == "q021":
            wrong_mode = "foundation"
        entries = {
            "wrong_dimension": {"dimension_id": wrong_dimension},
            "wrong_mode": {"dimension_id": dimension_id, "question_mode": wrong_mode},
            "expired": {"dimension_id": dimension_id, "valid_until": "2026-08-26"},
            "retired": {"dimension_id": dimension_id, "status": "retired"},
            "duplicate": {"dimension_id": dimension_id, "duplicate_of": question_id},
            "wrong_role": {"dimension_id": dimension_id, "role": "other_role_fixture"},
            "low_trust": {"dimension_id": dimension_id, "trust_level": "low"},
        }
        for category, attributes in entries.items():
            fixture_id = f"hn_{category}_q{number:03d}"
            catalog[fixture_id] = {
                "fixture_id": fixture_id,
                "category": category,
                "question_id": fixture_id,
                "role": CORPUS_ROLE,
                "status": "active",
                "trust_level": "medium",
                "source": "offline-synthetic-fixture",
                "intent_question_id": question_id,
                **attributes,
            }
    return catalog


SYNTHETIC_HARD_NEGATIVE_CATALOG = _build_synthetic_hard_negative_catalog()

_DIFFICULTY_BY_MODE: dict[str, str] = {
    "foundation": "intermediate",
    "project_deep_dive": "intermediate",
    "scenario": "intermediate",
    "system_design": "advanced",
    "coding": "advanced",
    "follow_up": "intermediate",
}
_REQUIRED_INTENT_FIELDS = frozenset(
    {
        "intent_id",
        "role",
        "role_version",
        "dimension_id",
        "requested_mode",
        "query_text",
        "gold_question_id",
        "acceptable_question_ids",
        "hard_negative_ids",
        "label_notes",
    }
)
_FORBIDDEN_QUERY_PATTERNS = (
    re.compile(r"(?i)(?:https?://|www\.)\S+"),
    re.compile(r"(?i)[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}"),
    re.compile(r"(?<!\d)(?:\+?86[\s.-]?)?1[3-9](?:[\s.-]?\d){9}(?!\d)"),
)
_TIER_ALIASES = {
    "exact": "exact",
    "primary": "exact",
    "compatible": "compatible",
    "fallback": "compatible",
}


class EvaluationValidationError(ValueError):
    """A label, candidate, or trace violates the evaluation contract."""


# Compatibility spelling for callers that name errors after the service.
QuestionCorpusEvaluationError = EvaluationValidationError


def stable_json_hash(value: Any) -> str:
    """Return a repeatable SHA-256 hash for JSON-safe calibration payloads."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise EvaluationValidationError("value cannot be hashed as JSON") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def compare_manifest_preview(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> tuple[str, ...]:
    """Compare stable manifest preview fields without echoing corpus values."""

    if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
        raise EvaluationValidationError("manifest previews must be mappings")
    differences: list[str] = []
    for key in sorted(set(expected) | set(actual)):
        if expected.get(key) != actual.get(key):
            differences.append(str(key))
    return tuple(differences)


def _field(value: Any, *names: str, default: Any = None) -> Any:
    """Read a field from mappings and Pydantic/dataclass-like objects."""

    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        try:
            result = getattr(value, name)
        except AttributeError:
            continue
        return result
    return default


def _as_list(value: Any) -> list[Any] | None:
    if value is None or isinstance(value, (str, bytes, bytearray, Mapping)):
        return None
    if isinstance(value, Sequence):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return None


def _normalise_tier(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        return None
    text = raw.strip().casefold()
    if text.startswith("modematchtier."):
        text = text.rsplit(".", 1)[-1]
    return _TIER_ALIASES.get(text)


def _finite_score(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _round_metric(value: float) -> float:
    rounded = round(float(value), METRIC_DECIMAL_PLACES)
    return 0.0 if rounded == 0 else rounded


def _coerce_intent(value: LabeledQuestionIntent | Mapping[str, Any]) -> LabeledQuestionIntent:
    if isinstance(value, LabeledQuestionIntent):
        return value
    if isinstance(value, Mapping):
        missing = _REQUIRED_INTENT_FIELDS.difference(value)
        if missing:
            raise EvaluationValidationError("retrieval intent is missing required fields")
    try:
        return LabeledQuestionIntent.model_validate(value)
    except Exception as exc:
        raise EvaluationValidationError("retrieval intent is invalid") from exc


def _coerce_record(value: InterviewQuestionRecord | Mapping[str, Any]) -> InterviewQuestionRecord:
    if isinstance(value, InterviewQuestionRecord):
        return value
    try:
        return InterviewQuestionRecord.model_validate(value)
    except Exception as exc:
        raise EvaluationValidationError("question record is invalid") from exc


def _record_values(records: Any) -> list[Any]:
    if records is None:
        return []
    snapshot_records = _field(records, "records", default=None)
    if snapshot_records is not None and records is not snapshot_records:
        records = snapshot_records
    if isinstance(records, Mapping):
        # A catalog mapping is useful to injected stores and is unambiguous
        # when it contains records keyed by question_id.
        return list(records.values())
    values = _as_list(records)
    return values or []


def _normalise_records(
    records: Any,
) -> tuple[dict[str, InterviewQuestionRecord], int, int]:
    """Return a canonical ID map and corpus invalid/duplicate counts."""

    by_id: dict[str, InterviewQuestionRecord] = {}
    invalid_count = 0
    duplicate_count = 0
    for raw in _record_values(records):
        try:
            record = _coerce_record(raw)
        except EvaluationValidationError:
            invalid_count += 1
            continue
        if record.question_id in by_id:
            duplicate_count += 1
        else:
            by_id[record.question_id] = record
    # A duplicate content hash is one additional duplicate for every repeated
    # record, not one error per hash bucket.  Recompute from validated values so
    # malformed records do not affect the count.
    hash_counts: dict[str, int] = {}
    for record in by_id.values():
        hash_counts[record.content_hash] = hash_counts.get(record.content_hash, 0) + 1
    duplicate_count += sum(max(0, count - 1) for count in hash_counts.values())
    return by_id, invalid_count, duplicate_count


def _expected_match_tier(
    intent: LabeledQuestionIntent | QuestionRetrievalIntent,
    record: InterviewQuestionRecord,
    policy: QuestionModePolicy,
) -> str | None:
    requested_mode = (
        intent.requested_mode
        if isinstance(intent, LabeledQuestionIntent)
        else intent.question_mode
    )
    dimension_id = intent.dimension_id
    primary_mode = record.primary_mode or record.question_mode
    if primary_mode == requested_mode or record.question_mode == requested_mode:
        return "exact"
    try:
        allowed = set(policy.compatible_order_for(dimension_id))
    except (TypeError, ValueError):
        return None
    if (
        primary_mode in allowed
        and requested_mode in record.compatible_modes
        and requested_mode != primary_mode
    ):
        return "compatible"
    return None


def _collect_intent_validation_issues(
    intents: Sequence[LabeledQuestionIntent],
    records: Any = None,
    *,
    policy: QuestionModePolicy | None = None,
    as_of: date = CORPUS_AS_OF,
) -> list[str]:
    issues: list[str] = []
    if not intents:
        issues.append("at least one retrieval intent is required")
        return issues
    seen_intent_ids: set[str] = set()
    seen_gold_ids: set[str] = set()
    by_id: dict[str, InterviewQuestionRecord] = {}
    if records is not None:
        by_id, invalid_count, _duplicate_count = _normalise_records(records)
        if invalid_count:
            issues.append("records contain invalid entries")
    selected_policy = policy or QuestionModePolicy.default()
    all_categories: set[str] = set()
    for intent in intents:
        if intent.intent_id in seen_intent_ids:
            issues.append(f"duplicate intent_id: {intent.intent_id}")
        seen_intent_ids.add(intent.intent_id)
        if intent.gold_question_id in seen_gold_ids:
            issues.append(f"duplicate gold_question_id: {intent.gold_question_id}")
        seen_gold_ids.add(intent.gold_question_id)
        if intent.role != CORPUS_ROLE or intent.role_version != CORPUS_ROLE_VERSION:
            issues.append(f"intent role/version mismatch: {intent.intent_id}")
        if any(pattern.search(intent.query_text) for pattern in _FORBIDDEN_QUERY_PATTERNS):
            issues.append(f"query_text contains unsafe content: {intent.intent_id}")
        if intent.gold_question_id not in intent.acceptable_question_ids:
            issues.append(f"gold must be acceptable: {intent.intent_id}")

        gold = by_id.get(intent.gold_question_id)
        if records is not None and gold is None:
            issues.append(f"gold question is missing: {intent.gold_question_id}")
        if gold is not None:
            if gold.role != intent.role or gold.role_version != intent.role_version:
                issues.append(f"gold role/version mismatch: {intent.intent_id}")
            if gold.dimension_id != intent.dimension_id:
                issues.append(f"gold dimension mismatch: {intent.intent_id}")
            if _expected_match_tier(intent, gold, selected_policy) != "exact":
                issues.append(f"gold must be exact: {intent.intent_id}")

        for acceptable_id in intent.acceptable_question_ids:
            if acceptable_id == intent.gold_question_id:
                continue
            acceptable = by_id.get(acceptable_id)
            if records is not None and acceptable is None:
                issues.append(f"acceptable question is missing: {acceptable_id}")
                continue
            if acceptable is None:
                continue
            if acceptable.dimension_id != intent.dimension_id:
                issues.append(f"acceptable crosses dimension: {intent.intent_id}")
            if _expected_match_tier(intent, acceptable, selected_policy) is None:
                issues.append(f"acceptable has invalid mode: {intent.intent_id}")

        if not intent.hard_negative_ids:
            issues.append(f"hard negatives must not be empty: {intent.intent_id}")
        if len(set(intent.hard_negative_ids)) != len(intent.hard_negative_ids):
            issues.append(f"hard negatives contain duplicates: {intent.intent_id}")
        accepted_ids = set(intent.acceptable_question_ids)
        for negative_id in intent.hard_negative_ids:
            if negative_id in accepted_ids or negative_id == intent.gold_question_id:
                issues.append(f"hard negative overlaps positive: {intent.intent_id}")
            fixture = SYNTHETIC_HARD_NEGATIVE_CATALOG.get(negative_id)
            if fixture is None:
                issues.append(f"hard negative fixture is unknown: {negative_id}")
                continue
            category = fixture["category"]
            all_categories.add(category)
            if fixture.get("intent_question_id") != intent.gold_question_id:
                issues.append(f"hard negative fixture targets wrong gold: {intent.intent_id}")
            if category == "wrong_dimension" and fixture.get("dimension_id") == intent.dimension_id:
                issues.append(f"wrong-dimension fixture matches dimension: {intent.intent_id}")
            elif category == "wrong_mode":
                mode = fixture.get("question_mode")
                if mode == intent.requested_mode:
                    issues.append(f"wrong-mode fixture matches requested mode: {intent.intent_id}")
                if gold is not None and mode in gold.compatible_modes:
                    issues.append(f"wrong-mode fixture is compatible: {intent.intent_id}")
            elif category == "expired" and fixture.get("valid_until", "9999-12-31") >= as_of.isoformat():
                issues.append(f"expired fixture is not expired: {intent.intent_id}")
            elif category == "retired" and fixture.get("status") != "retired":
                issues.append(f"retired fixture is active: {intent.intent_id}")
            elif category == "duplicate" and fixture.get("duplicate_of") != intent.gold_question_id:
                issues.append(f"duplicate fixture targets wrong gold: {intent.intent_id}")
            elif category == "wrong_role" and fixture.get("role") == intent.role:
                issues.append(f"wrong-role fixture matches role: {intent.intent_id}")
            elif category == "low_trust" and fixture.get("trust_level") != "low":
                issues.append(f"low-trust fixture is trusted: {intent.intent_id}")

    # A complete canonical set must explicitly exercise all seven categories.
    # Small test fixtures remain useful without pretending to cover the whole
    # catalogue.
    if len(intents) >= 30:
        missing_categories = set(HARD_NEGATIVE_CATEGORIES) - all_categories
        if missing_categories:
            issues.append(
                "hard negative category coverage missing: "
                + ",".join(sorted(missing_categories))
            )
        if len(intents) != 30:
            issues.append("complete retrieval intent set must contain exactly 30 rows")
        if len(seen_gold_ids) != 30:
            issues.append("complete retrieval intent set must cover 30 unique gold questions")
    return sorted(set(issues))


def validate_labeled_intents(
    intents: Sequence[LabeledQuestionIntent | Mapping[str, Any]],
    records: Any = None,
    *,
    policy: QuestionModePolicy | Mapping[str, Any] | None = None,
    as_of: date = CORPUS_AS_OF,
) -> tuple[str, ...]:
    """Validate labeled intents and raise a non-echoing contract error."""

    try:
        normalized = [_coerce_intent(value) for value in intents]
    except TypeError as exc:
        raise EvaluationValidationError("retrieval intents must be a sequence") from exc
    try:
        selected_policy = (
            QuestionModePolicy.default()
            if policy is None
            else QuestionModePolicy.model_validate(policy)
        )
    except Exception as exc:
        raise EvaluationValidationError("evaluation mode policy is invalid") from exc
    issues = _collect_intent_validation_issues(
        normalized,
        records,
        policy=selected_policy,
        as_of=as_of,
    )
    if issues:
        raise EvaluationValidationError("; ".join(issues))
    return ()


validate_retrieval_intents = validate_labeled_intents
validate_intents = validate_labeled_intents


def load_retrieval_intents(
    path: str | Path = DEFAULT_RETRIEVAL_INTENTS_PATH,
    *,
    records: Any = None,
    policy: QuestionModePolicy | Mapping[str, Any] | None = None,
    as_of: date = CORPUS_AS_OF,
) -> list[LabeledQuestionIntent]:
    """Load strict JSONL labels and validate their cross-record relations."""

    try:
        source = Path(path)
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise EvaluationValidationError("retrieval intents file could not be read") from exc
    values: list[LabeledQuestionIntent] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EvaluationValidationError(
                f"retrieval intents JSONL is invalid at line {line_number}"
            ) from exc
        try:
            values.append(_coerce_intent(payload))
        except Exception as exc:
            raise EvaluationValidationError(
                f"retrieval intent schema is invalid at line {line_number}"
            ) from exc
    validate_labeled_intents(values, records=records, policy=policy, as_of=as_of)
    return values


load_labeled_intents = load_retrieval_intents
load_intents = load_retrieval_intents


def intent_to_runtime_intent(
    intent: LabeledQuestionIntent | Mapping[str, Any],
    *,
    records: Any = None,
    difficulty: str | None = None,
    excluded_question_ids: Sequence[str] = (),
) -> QuestionRetrievalIntent:
    """Project a label to only the fields accepted by runtime retrieval.

    Gold/acceptable/hard-negative IDs, label notes, provenance, and internal
    evaluation metadata never enter ``QuestionRetrievalIntent`` or its query
    embedding text.
    """

    normalized = _coerce_intent(intent)
    selected_difficulty = difficulty
    if selected_difficulty is None and records is not None:
        by_id, _invalid_count, _duplicate_count = _normalise_records(records)
        record = by_id.get(normalized.gold_question_id)
        if record is not None:
            selected_difficulty = record.difficulty
    if selected_difficulty is None:
        selected_difficulty = _DIFFICULTY_BY_MODE.get(normalized.requested_mode)
    if selected_difficulty not in {"foundation", "intermediate", "advanced"}:
        raise EvaluationValidationError("runtime intent difficulty is invalid")
    exclusions = list(excluded_question_ids)
    if any(not isinstance(value, str) or not value.strip() for value in exclusions):
        raise EvaluationValidationError("runtime intent exclusions are invalid")
    try:
        return QuestionRetrievalIntent(
            query_text=normalized.query_text,
            role=normalized.role,
            dimension_id=normalized.dimension_id,
            question_mode=normalized.requested_mode,
            difficulty=selected_difficulty,
            excluded_question_ids=exclusions,
        )
    except Exception as exc:
        raise EvaluationValidationError("runtime intent projection is invalid") from exc


@dataclass
class IntentEvaluationResult:
    """Metrics and candidate-safe diagnostics for one labeled intent."""

    intent_id: str
    dimension_id: str
    requested_mode: str
    gold_question_id: str
    top3: list[dict[str, Any]] = field(default_factory=list)
    gold_rank: int | None = None
    acceptable_ranks: list[int] = field(default_factory=list)
    hard_negative_hits: list[str] = field(default_factory=list)
    match_tier: str | None = None
    trace: dict[str, Any] | None = None
    trace_valid: bool = False
    invalid_count: int = 0
    duplicate_count: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def recall_at_3(self) -> float:
        return 1.0 if self.gold_rank is not None and self.gold_rank <= MAX_EVALUATION_K else 0.0

    @property
    def mrr_at_3(self) -> float:
        if self.gold_rank is None or self.gold_rank > MAX_EVALUATION_K:
            return 0.0
        return 1.0 / self.gold_rank

    @property
    def acceptable_hit(self) -> bool:
        return bool(self.acceptable_ranks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "dimension_id": self.dimension_id,
            "requested_mode": self.requested_mode,
            "gold_question_id": self.gold_question_id,
            "top3": [dict(item) for item in self.top3],
            "gold_rank": self.gold_rank,
            "acceptable_ranks": list(self.acceptable_ranks),
            "acceptable_hit": self.acceptable_hit,
            "hard_negative_hits": list(self.hard_negative_hits),
            "match_tier": self.match_tier,
            "trace": dict(self.trace) if self.trace is not None else None,
            "trace_valid": self.trace_valid,
            "invalid_count": self.invalid_count,
            "duplicate_count": self.duplicate_count,
            "errors": list(self.errors),
        }

    as_dict = to_dict


@dataclass
class CorpusEvaluationReport:
    """Deterministic corpus-level evaluation report."""

    intent_results: list[IntentEvaluationResult] = field(default_factory=list)
    recall_at_3: float = 0.0
    mrr_at_3: float = 0.0
    dimension_recall_at_3: dict[str, float] = field(default_factory=dict)
    acceptable_recall_at_3: float = 0.0
    hard_negative_hits: int = 0
    invalid_count: int = 0
    duplicate_count: int = 0
    trace_coverage: float = 0.0
    passed: bool = False
    errors: list[str] = field(default_factory=list)
    as_of: date | None = None
    backend: str | None = None

    @property
    def report_hash(self) -> str:
        """Stable content hash excluding the hash field itself."""

        return stable_json_hash(self.to_dict())

    @property
    def pass_predicate(self) -> bool:
        return self.passed

    @property
    def pass_(self) -> bool:
        return self.passed

    # Descriptive aliases keep the report convenient for callers that use the
    # wording from the acceptance checklist while the canonical fields remain
    # compact and stable.
    @property
    def gold_recall_at_3(self) -> float:
        return self.recall_at_3

    @property
    def dimension_recall(self) -> dict[str, float]:
        return dict(self.dimension_recall_at_3)

    @property
    def hard_negative_hit_count(self) -> int:
        return self.hard_negative_hits

    @property
    def invalid_records(self) -> int:
        return self.invalid_count

    @property
    def duplicate_records(self) -> int:
        return self.duplicate_count

    @property
    def trace_coverage_ratio(self) -> float:
        return self.trace_coverage

    @property
    def results(self) -> list[IntentEvaluationResult]:
        return self.intent_results

    @property
    def metrics(self) -> dict[str, Any]:
        return {
            "recall_at_3": self.recall_at_3,
            "mrr_at_3": self.mrr_at_3,
            "dimension_recall_at_3": dict(self.dimension_recall_at_3),
            "acceptable_recall_at_3": self.acceptable_recall_at_3,
            "hard_negative_hits": self.hard_negative_hits,
            "invalid_count": self.invalid_count,
            "duplicate_count": self.duplicate_count,
            "trace_coverage": self.trace_coverage,
        }

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "status": "passed" if self.passed else "failed",
            "passed": self.passed,
            "as_of": self.as_of.isoformat() if self.as_of is not None else None,
            "backend": self.backend,
            "metrics": self.metrics,
            "intent_results": [item.to_dict() for item in self.intent_results],
            "errors": list(self.errors),
        }
        return payload

    as_dict = to_dict
    model_dump = to_dict

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=indent)

    def json(self, *, indent: int = 2) -> str:
        return self.to_json(indent=indent)


def serialize_evaluation_report(report: CorpusEvaluationReport, *, indent: int = 2) -> str:
    if not isinstance(report, CorpusEvaluationReport):
        raise TypeError("report must be CorpusEvaluationReport")
    return report.to_json(indent=indent)


report_to_json = serialize_evaluation_report
serialize_report = serialize_evaluation_report
evaluation_report_to_json = serialize_evaluation_report


def _extract_payload(raw: Any) -> tuple[str | None, list[Any], Any, Any, bool]:
    """Extract status, candidates, trace, rank trace, and status presence."""

    if isinstance(raw, (str, bytes, bytearray)):
        return None, [], None, None, False
    status = _field(raw, "status", default=None)
    status_present = status is not None
    trace = _field(raw, "trace", "retrieval_trace", default=None)
    rank_trace = _field(raw, "rank_trace", "ranking_trace", default=None)
    candidates = _field(
        raw,
        "hits",
        "results",
        "candidates",
        "top3",
        "records",
        default=None,
    )
    candidates = _as_list(candidates)
    if candidates is None:
        selected = _field(
            raw,
            "selected_question",
            "retrieved_question",
            "selected_record",
            default=None,
        )
        if selected is not None:
            candidates = [selected]
    if candidates is None and isinstance(raw, Sequence):
        candidates = list(raw)
    if candidates is None:
        candidates = []
    return (
        str(status) if status is not None else None,
        candidates,
        trace,
        rank_trace,
        status_present,
    )


def _normalise_candidate(
    raw: Any,
    records_by_id: Mapping[str, InterviewQuestionRecord],
    *,
    default_index_version: str | None = None,
) -> tuple[dict[str, Any], InterviewQuestionRecord | None, str | None, bool, str | None]:
    """Return candidate-safe data, record, explicit tier, invalid flag, error."""

    nested = _field(raw, "record", "question", default=None)
    embedded_record: InterviewQuestionRecord | None = None
    if isinstance(nested, InterviewQuestionRecord):
        embedded_record = nested
    elif isinstance(nested, Mapping):
        try:
            embedded_record = _coerce_record(nested)
        except EvaluationValidationError:
            embedded_record = None
    elif isinstance(raw, InterviewQuestionRecord):
        embedded_record = raw
    question_id = _field(raw, "question_id", "id", default=None)
    if question_id is None and isinstance(raw, str):
        question_id = raw
    if question_id is None and embedded_record is not None:
        question_id = embedded_record.question_id
    if not isinstance(question_id, str) or not question_id.strip():
        data = {"question_id": None, "rank": None}
        return data, None, None, True, "candidate_missing_question_id"
    question_id = question_id.strip()
    record = records_by_id.get(question_id) or embedded_record
    invalid = False
    error: str | None = None
    if record is None:
        invalid = True
        error = "candidate_question_missing"
    elif embedded_record is not None and embedded_record.source_id != record.source_id:
        invalid = True
        error = "candidate_record_mismatch"
    score = _finite_score(
        _field(raw, "score", "similarity", "vector_score", "total_score", default=None)
    )
    raw_score = _field(
        raw, "score", "similarity", "vector_score", "total_score", default=None
    )
    if raw_score is not None and score is None:
        invalid = True
        error = error or "candidate_score_invalid"
    index_version = _field(raw, "index_version", "version", default=default_index_version)
    if index_version is not None and not isinstance(index_version, str):
        invalid = True
        error = error or "candidate_index_version_invalid"
        index_version = None
    source_id = _field(raw, "source_id", default=None)
    if source_id is None and record is not None:
        source_id = record.source_id
    if record is not None and source_id != record.source_id:
        invalid = True
        error = error or "candidate_source_mismatch"
    explicit_tier = _normalise_tier(
        _field(raw, "match_tier", "mode_match_tier", "tier", default=None)
    )
    raw_tier = _field(raw, "match_tier", "mode_match_tier", "tier", default=None)
    if raw_tier is not None and explicit_tier is None:
        invalid = True
        error = error or "candidate_tier_invalid"
    output = {
        "question_id": question_id,
        "source_id": source_id,
        "score": score,
        "index_version": index_version,
        "match_tier": explicit_tier,
    }
    if record is not None:
        output["dimension_id"] = record.dimension_id
        output["question_mode"] = record.primary_mode or record.question_mode
    return output, record, explicit_tier, invalid, error


def _trace_mapping(trace: Any, *, fallback_tier: str | None = None) -> dict[str, Any] | None:
    if trace is None:
        return None
    status = _field(trace, "status", default=None)
    status = getattr(status, "value", status)
    question_id = _field(trace, "question_id", "selected_question_id", default=None)
    source_id = _field(trace, "source_id", "selected_source_id", default=None)
    score = _finite_score(
        _field(trace, "score", "selected_score", "total_score", default=None)
    )
    raw_score = _field(trace, "score", "selected_score", "total_score", default=None)
    index_version = _field(trace, "index_version", "version", default=None)
    # ``fallback_tier`` is kept as a compatibility argument for callers that
    # used the first draft of this helper, but a typed trace must carry its
    # tier explicitly.  Inferring it here would hide a broken adapter.
    del fallback_tier
    tier = _normalise_tier(
        _field(trace, "match_tier", "mode_match_tier", "tier", default=None)
    )
    result: dict[str, Any] = {
        "status": status,
        "question_id": question_id,
        "source_id": source_id,
        "score": score,
        "index_version": index_version,
        "match_tier": tier,
    }
    if raw_score is not None and score is None:
        result["_invalid_score"] = True
    return result


def _rank_trace_candidates(rank_trace: Any) -> list[Any]:
    values = _as_list(rank_trace)
    if not values:
        return []
    return sorted(
        values,
        key=lambda value: (
            int(_field(value, "rank", default=10**6) or 10**6),
            str(_field(value, "question_id", default="")),
        ),
    )


def _call_with_supported_kwargs(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    parameters = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return fn(*args, **kwargs)
    accepted = {name: value for name, value in kwargs.items() if name in parameters}
    return fn(*args, **accepted)


def _embed_for_query(
    runtime_intent: QuestionRetrievalIntent,
    embedding: Any,
) -> list[float]:
    if embedding is None:
        return [0.0]
    text = build_query_embedding_text(runtime_intent, ())
    embed = getattr(embedding, "embed", None)
    if not callable(embed) and callable(embedding):
        embed = embedding
    if not callable(embed):
        raise EvaluationValidationError("embedding dependency is invalid")
    raw = embed([text])
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        values = list(raw)
    else:
        raise EvaluationValidationError("embedding result is invalid")
    if values and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        values = [values]
    if len(values) != 1:
        raise EvaluationValidationError("embedding result must contain one vector")
    vector: list[float] = []
    for value in values[0]:
        if isinstance(value, bool):
            raise EvaluationValidationError("embedding vector is invalid")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise EvaluationValidationError("embedding vector is invalid") from exc
        if not math.isfinite(number):
            raise EvaluationValidationError("embedding vector is invalid")
        vector.append(number)
    if not vector:
        raise EvaluationValidationError("embedding vector is empty")
    return vector


def _lookup_result(results: Any, index: int, intent: LabeledQuestionIntent) -> Any:
    if isinstance(results, Mapping):
        for key in (intent.intent_id, intent.gold_question_id, str(index)):
            if key in results:
                return results[key]
        return None
    values = _as_list(results)
    if values is None or index >= len(values):
        return None
    return values[index]


def _run_backend(
    *,
    intent: LabeledQuestionIntent,
    runtime_intent: QuestionRetrievalIntent,
    index: int,
    result_provider: Callable[..., Any] | None,
    results: Any,
    retriever: Any,
    store: Any,
    embedding: Any,
    query_vectors: Mapping[str, Sequence[float]] | Sequence[Sequence[float]] | None,
    as_of: date,
) -> tuple[Any, str | None]:
    if result_provider is not None:
        try:
            signature = inspect.signature(result_provider)
            positional = [
                parameter
                for parameter in signature.parameters.values()
                if parameter.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            if len(positional) <= 1:
                return result_provider(runtime_intent), "provider"
        except (TypeError, ValueError):
            pass
        return result_provider(intent, runtime_intent), "provider"
    if results is not None:
        return _lookup_result(results, index, intent), "results"
    if retriever is not None:
        retrieve = getattr(retriever, "retrieve", retriever if callable(retriever) else None)
        if not callable(retrieve):
            raise EvaluationValidationError("retriever dependency is invalid")
        raw = _call_with_supported_kwargs(
            retrieve,
            (runtime_intent,),
            {"today": as_of, "limit": MAX_EVALUATION_K},
        )
        return raw, "retriever"
    if store is None:
        raise EvaluationValidationError("evaluation requires a result provider or store")
    search = getattr(store, "search", store if callable(store) else None)
    if not callable(search):
        retrieve = getattr(store, "retrieve", None)
        if callable(retrieve):
            raw = _call_with_supported_kwargs(
                retrieve,
                (runtime_intent,),
                {"today": as_of, "limit": MAX_EVALUATION_K},
            )
            return raw, "store"
        raise EvaluationValidationError("store dependency is invalid")
    vector: Sequence[float]
    if isinstance(query_vectors, Mapping):
        vector = query_vectors.get(intent.intent_id) or query_vectors.get(intent.gold_question_id) or [0.0]
    elif query_vectors is not None:
        values = _as_list(query_vectors)
        vector = values[index] if values is not None and index < len(values) else [0.0]
    else:
        vector = _embed_for_query(runtime_intent, embedding)
    raw = _call_with_supported_kwargs(
        search,
        (),
        {
            "intent": runtime_intent,
            "query_vector": vector,
            "today": as_of,
            "limit": MAX_EVALUATION_K,
        },
    )
    return raw, "store"


def _evaluate_one(
    intent: LabeledQuestionIntent,
    runtime_intent: QuestionRetrievalIntent,
    raw: Any,
    records_by_id: Mapping[str, InterviewQuestionRecord],
    *,
    policy: QuestionModePolicy,
) -> IntentEvaluationResult:
    result = IntentEvaluationResult(
        intent_id=intent.intent_id,
        dimension_id=intent.dimension_id,
        requested_mode=intent.requested_mode,
        gold_question_id=intent.gold_question_id,
    )
    status, raw_candidates, raw_trace, rank_trace, status_present = _extract_payload(raw)
    default_index_version = _field(raw, "index_version", default=None)
    if not status_present or status not in {"hit", "no_match", "unavailable", "index_mismatch"}:
        result.invalid_count += 1
        result.errors.append("result_status_invalid")
    rank_values = _rank_trace_candidates(rank_trace)
    # A QuestionRetriever exposes its full top-k diagnostic as rank_trace.  If
    # no low-level hits were attached, it is a safe source for preserving top3.
    if rank_values and len(rank_values) > len(raw_candidates):
        raw_candidates = rank_values[:MAX_EVALUATION_K]
    seen_ids: set[str] = set()
    candidate_records: list[InterviewQuestionRecord | None] = []
    for raw_candidate in raw_candidates[:MAX_EVALUATION_K]:
        candidate, record, explicit_tier, invalid, error = _normalise_candidate(
            raw_candidate,
            records_by_id,
            default_index_version=default_index_version,
        )
        candidate["rank"] = len(result.top3) + 1
        if candidate.get("question_id") in seen_ids:
            result.duplicate_count += 1
            result.errors.append("duplicate_returned_question")
        question_id = candidate.get("question_id")
        if isinstance(question_id, str):
            seen_ids.add(question_id)
        if invalid:
            result.invalid_count += 1
            if error:
                result.errors.append(error)
        expected_tier = (
            _expected_match_tier(runtime_intent, record, policy)
            if record is not None
            else None
        )
        if explicit_tier is None and expected_tier is not None:
            candidate["match_tier"] = expected_tier
        elif expected_tier is None and record is not None:
            result.invalid_count += 1
            result.errors.append("candidate_tier_unroutable")
        elif explicit_tier != expected_tier:
            result.invalid_count += 1
            result.errors.append("candidate_tier_mismatch")
        candidate_records.append(record)
        result.top3.append(candidate)

    # The result trace is a selected-hit trace.  A rank-trace entry is accepted
    # as a fallback only when a backend intentionally exposes rank diagnostics
    # instead of a typed QuestionRetrievalResult.
    trace_value = raw_trace
    if trace_value is None and rank_values:
        trace_value = rank_values[0]
    fallback_tier = result.top3[0].get("match_tier") if result.top3 else None
    # Compact fake stores sometimes return only question IDs in ``top3`` and
    # put score/index metadata solely in the selected trace.  Promote missing
    # first-candidate metadata from that trace while still rejecting any
    # conflicting values.
    if status == "hit" and result.top3 and trace_value is not None:
        selected_trace = _trace_mapping(trace_value)
        if selected_trace is not None:
            first = result.top3[0]
            for key in ("score", "index_version"):
                if first.get(key) is None and selected_trace.get(key) is not None:
                    first[key] = selected_trace[key]
    trace = _trace_mapping(trace_value, fallback_tier=fallback_tier)
    result.trace = trace
    if trace is None:
        result.invalid_count += 1
        result.errors.append("trace_missing")
    else:
        trace_valid = True
        if trace.get("status") != status:
            trace_valid = False
            result.errors.append("trace_status_mismatch")
        if status == "hit":
            if not result.top3:
                trace_valid = False
                result.errors.append("hit_without_candidates")
            else:
                first = result.top3[0]
                if trace.get("question_id") != first.get("question_id"):
                    trace_valid = False
                    result.errors.append("trace_question_mismatch")
                if trace.get("source_id") != first.get("source_id"):
                    trace_valid = False
                    result.errors.append("trace_source_mismatch")
                if trace.get("score") != first.get("score"):
                    trace_valid = False
                    result.errors.append("trace_score_mismatch")
                if trace.get("index_version") != first.get("index_version"):
                    trace_valid = False
                    result.errors.append("trace_index_version_mismatch")
                first_record = candidate_records[0] if candidate_records else None
                expected_tier = (
                    _expected_match_tier(runtime_intent, first_record, policy)
                    if first_record is not None
                    else None
                )
                if trace.get("match_tier") is None:
                    trace_valid = False
                    result.errors.append("trace_tier_missing")
                elif trace.get("match_tier") != expected_tier:
                    trace_valid = False
                    result.errors.append("trace_tier_mismatch")
        else:
            if any(trace.get(key) is not None for key in ("question_id", "source_id", "score")):
                trace_valid = False
                result.errors.append("non_hit_trace_claims_selection")
        if trace.get("_invalid_score"):
            trace_valid = False
            result.errors.append("trace_score_invalid")
        result.trace_valid = trace_valid
        if not trace_valid:
            result.invalid_count += 1

    result.match_tier = (
        result.top3[0].get("match_tier") if result.top3 else None
    )
    id_to_rank: dict[str, int] = {}
    for rank, candidate in enumerate(result.top3, start=1):
        question_id = candidate.get("question_id")
        if isinstance(question_id, str) and question_id not in id_to_rank:
            id_to_rank[question_id] = rank
    result.gold_rank = id_to_rank.get(intent.gold_question_id)
    result.acceptable_ranks = sorted(
        rank
        for question_id, rank in id_to_rank.items()
        if question_id in set(intent.acceptable_question_ids)
    )
    result.hard_negative_hits = sorted(
        question_id
        for question_id in id_to_rank
        if question_id in set(intent.hard_negative_ids)
    )
    if result.gold_rank is None:
        result.invalid_count += 1
        result.errors.append("gold_missing_from_top3")
    if result.hard_negative_hits:
        result.errors.append("hard_negative_in_top3")
    result.errors = sorted(set(result.errors))
    return result


def evaluate_question_corpus(
    intents: Sequence[LabeledQuestionIntent | Mapping[str, Any]] | str | Path,
    records: Any = None,
    *,
    result_provider: Callable[..., Any] | None = None,
    results: Any = None,
    retrieval_results: Any = None,
    retriever: Any = None,
    store: Any = None,
    question_store: Any = None,
    fake_store: Any = None,
    embedding: Any = None,
    embedding_client: Any = None,
    query_vectors: Mapping[str, Sequence[float]] | Sequence[Sequence[float]] | None = None,
    as_of: date = CORPUS_AS_OF,
    policy: QuestionModePolicy | Mapping[str, Any] | None = None,
    top_k: int = MAX_EVALUATION_K,
    backend: str | None = None,
) -> CorpusEvaluationReport:
    """Evaluate labels against deterministic candidate results.

    ``result_provider`` is the preferred test seam and receives either
    ``(runtime_intent)`` or ``(labeled_intent, runtime_intent)``.  A fake store
    may instead implement the ordinary ``search(intent, query_vector, today,
    limit)`` contract.  No default provider is constructed implicitly.
    """

    if isinstance(intents, (str, Path)):
        normalized_intents = load_retrieval_intents(intents, records=records, policy=policy, as_of=as_of)
    else:
        try:
            normalized_intents = [_coerce_intent(value) for value in intents]
        except TypeError as exc:
            raise EvaluationValidationError("retrieval intents must be a sequence") from exc
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k != MAX_EVALUATION_K:
        raise EvaluationValidationError("evaluation top_k is fixed at 3")
    if isinstance(as_of, datetime) or not isinstance(as_of, date):
        raise EvaluationValidationError("evaluation as_of must be a date")
    try:
        selected_policy = (
            QuestionModePolicy.default()
            if policy is None
            else QuestionModePolicy.model_validate(policy)
        )
    except Exception as exc:
        raise EvaluationValidationError("evaluation mode policy is invalid") from exc
    validate_labeled_intents(normalized_intents, records=records, policy=selected_policy, as_of=as_of)
    records_by_id, corpus_invalid, corpus_duplicates = _normalise_records(records)
    selected_store = store or question_store or fake_store
    selected_embedding = embedding if embedding is not None else embedding_client
    selected_results = results if results is not None else retrieval_results
    report = CorpusEvaluationReport(as_of=as_of, backend=backend)
    if not normalized_intents:
        report.errors.append("no_intents")
        return report
    for index, intent in enumerate(normalized_intents):
        try:
            runtime_intent = intent_to_runtime_intent(intent, records=records)
            raw, inferred_backend = _run_backend(
                intent=intent,
                runtime_intent=runtime_intent,
                index=index,
                result_provider=result_provider,
                results=selected_results,
                retriever=retriever,
                store=selected_store,
                embedding=selected_embedding,
                query_vectors=query_vectors,
                as_of=as_of,
            )
            if report.backend is None:
                report.backend = backend or inferred_backend
            one = _evaluate_one(
                intent,
                runtime_intent,
                raw,
                records_by_id,
                policy=selected_policy,
            )
        except (EvaluationValidationError, TypeError, ValueError, OSError) as exc:
            one = IntentEvaluationResult(
                intent_id=intent.intent_id,
                dimension_id=intent.dimension_id,
                requested_mode=intent.requested_mode,
                gold_question_id=intent.gold_question_id,
                invalid_count=1,
                errors=["evaluation_backend_error"],
            )
        report.intent_results.append(one)
    total = len(report.intent_results)
    report.recall_at_3 = _round_metric(
        sum(item.recall_at_3 for item in report.intent_results) / total
    )
    report.mrr_at_3 = _round_metric(
        sum(item.mrr_at_3 for item in report.intent_results) / total
    )
    dimensions = sorted({item.dimension_id for item in report.intent_results})
    report.dimension_recall_at_3 = {
        dimension: _round_metric(
            sum(item.recall_at_3 for item in report.intent_results if item.dimension_id == dimension)
            / sum(1 for item in report.intent_results if item.dimension_id == dimension)
        )
        for dimension in dimensions
    }
    report.acceptable_recall_at_3 = _round_metric(
        sum(1.0 if item.acceptable_hit else 0.0 for item in report.intent_results) / total
    )
    report.hard_negative_hits = sum(
        len(item.hard_negative_hits) for item in report.intent_results
    )
    report.invalid_count = corpus_invalid + sum(
        item.invalid_count for item in report.intent_results
    )
    report.duplicate_count = corpus_duplicates + sum(
        item.duplicate_count for item in report.intent_results
    )
    report.trace_coverage = _round_metric(
        sum(1.0 if item.trace_valid else 0.0 for item in report.intent_results) / total
    )
    report.errors = sorted(
        {
            error
            for item in report.intent_results
            for error in item.errors
        }
    )
    dimensions_pass = all(value >= 0.80 for value in report.dimension_recall_at_3.values())
    report.passed = bool(
        report.recall_at_3 >= 0.90
        and report.mrr_at_3 >= 0.90
        and dimensions_pass
        and report.hard_negative_hits == 0
        and report.invalid_count == 0
        and report.duplicate_count == 0
        and report.trace_coverage >= 1.0
    )
    return report


__all__ = [
    "DEFAULT_RETRIEVAL_INTENTS_PATH",
    "EVALUATION_SCHEMA_VERSION",
    "EvaluationValidationError",
    "HARD_NEGATIVE_CATEGORIES",
    "MAX_EVALUATION_K",
    "METRIC_DECIMAL_PLACES",
    "CorpusEvaluationReport",
    "IntentEvaluationResult",
    "QuestionCorpusEvaluationError",
    "SYNTHETIC_HARD_NEGATIVE_CATALOG",
    "compare_manifest_preview",
    "evaluate_question_corpus",
    "intent_to_runtime_intent",
    "evaluation_report_to_json",
    "load_intents",
    "load_labeled_intents",
    "load_retrieval_intents",
    "report_to_json",
    "serialize_report",
    "serialize_evaluation_report",
    "stable_json_hash",
    "validate_intents",
    "validate_labeled_intents",
    "validate_retrieval_intents",
]
