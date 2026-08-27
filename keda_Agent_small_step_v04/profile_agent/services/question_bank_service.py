"""Load and audit the versioned, canonical JSON interview question bank.

The JSON bank is the source of truth.  This module deliberately contains no
indexing or network code: it validates source records, computes their stable
content identity, and reports lifecycle boundaries for callers that build a
disposable retrieval index later.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    QuestionEmbeddingProjection,
    QuestionBankManifest,
    QuestionModePolicy,
    QuestionSourceRegistry,
    QuestionRetrievalResult,
    RetrievedQuestion,
    validate_embedding_text_value,
)


SUPPORTED_ROLE = "ai_agent_engineer"
SUPPORTED_DIMENSION_IDS = frozenset(
    f"role_dim_{index:02d}" for index in range(1, 7)
)
SUPPORTED_SOURCE_TYPES = frozenset(
    {
        "public_interview_experience",
        "test_only_synthetic",
    }
)
SUPPORTED_SCHEMA_VERSIONS = frozenset(
    {
        1,
        "1",
        "v1",
        "question_bank.v1",
        "question_bank/v1",
        2,
        "2",
        "v2",
        "question_bank.v2",
        "question_bank/v2",
    }
)
DEFAULT_EXPIRING_WITHIN_DAYS = 30
EMBEDDING_TEXT_VERSION = "six-section-v1"
QUESTION_CONTENT_HASH_VERSION = "question-content-v2"
UNAVAILABLE_QUESTION_BANK_MANIFEST_HASH = "unavailable:canonical-question-bank"

_V1_RECORD_FIELDS: tuple[str, ...] = (
    "question_id",
    "question_text",
    "role",
    "role_version",
    "dimension_id",
    "skills",
    "question_mode",
    "difficulty",
    "expected_signals",
    "critical_errors",
    "follow_up_seeds",
    "company_tags",
    "source_id",
    "source_url",
    "source_title",
    "source_type",
    "published_at",
    "verified_at",
    "valid_until",
    "trust_level",
    "status",
    "version",
    "content_hash",
)


class _V1Projection(dict[str, Any]):
    """Mapping that also supports the common Pydantic serialization calls.

    Projection callers frequently pass records through code that expects a
    ``model_dump``/``model_dump_json`` pair.  Keeping this tiny mapping wrapper
    lets the result remain usable by strict v1 ``model_validate`` consumers
    without reintroducing any v2 fields into the payload.
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        mode = kwargs.get("mode", "python")
        payload = dict(self)
        if mode == "json":
            return _json_safe(payload)
        return payload

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        indent = kwargs.get("indent")
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            indent=indent,
            separators=None if indent is not None else (",", ":"),
        )


def _json_safe(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def normalize_question_text(value: str) -> str:
    """Collapse runs of Unicode whitespace into one ordinary space.

    Question text and skill labels are kept case-sensitive; normalization only
    removes formatting noise so content identity is stable across line wraps
    and indentation changes.
    """

    if not isinstance(value, str):
        raise TypeError("question text must be a string")
    return " ".join(value.split())


def normalize_project_mode(value: Any) -> str:
    """Normalize the legacy ``project`` input alias to the v2 wire value.

    ``project`` is accepted only at the explicit migration boundary.  The
    schema and all runtime outputs continue to use the canonical
    ``project_deep_dive`` value; no other aliases are guessed.
    """

    if not isinstance(value, str):
        raise TypeError("question mode must be a string")
    normalized = value.strip()
    if normalized == "project":
        return "project_deep_dive"
    if normalized in {
        "foundation",
        "project_deep_dive",
        "scenario",
        "system_design",
        "coding",
        "follow_up",
    }:
        return normalized
    raise ValueError(f"unsupported question mode: {value!r}")


def classify_question_record(
    record: InterviewQuestionRecord | Mapping[str, Any],
) -> str:
    """Classify a record by semantic shape, independent of serializer history.

    A v1 record acquires v2 defaults when it passes through ``model_dump`` or
    a checkpoint/JSON round trip.  Those defaults are not evidence of a v2
    record: the compatibility shape is the empty constraint/terms/compatible
    set plus ``source_ids == [source_id]`` and a primary equal to the legacy
    question mode.  Non-empty additive semantics (or a primary-only record
    with no legacy mode) are genuine v2 input.  When the additive shape is
    empty, the stored content hash deterministically disambiguates a v1 hash
    from a v2 projection hash; an unknown/stale hash falls back to v1 so audit
    can report the mismatch without changing the legacy classification.
    """

    if isinstance(record, InterviewQuestionRecord):
        get_value = lambda name, default=None: getattr(record, name, default)
    elif isinstance(record, Mapping):
        get_value = lambda name, default=None: record.get(name, default)
    else:
        raise TypeError(
            "question records must be InterviewQuestionRecord instances or mappings"
        )

    question_mode = get_value("question_mode")
    primary_mode = get_value("primary_mode")
    business_constraint = get_value("business_constraint", "")
    dimension_terms = get_value("dimension_terms", ())
    compatible_modes = get_value("compatible_modes", ())
    source_ids = get_value("source_ids")
    source_id = get_value("source_id")

    def comparable_mode(value: Any) -> Any:
        if value is None:
            return None
        try:
            return normalize_project_mode(value)
        except (TypeError, ValueError):
            # The projection/validation boundary reports malformed modes.  A
            # classifier should still be able to compare two equally malformed
            # values without turning an old record into a false v2 hit.
            return value

    if (
        isinstance(business_constraint, str)
        and business_constraint.strip()
    ):
        return "v2"
    if dimension_terms not in (None, [], ()):
        return "v2"
    if compatible_modes not in (None, [], ()):
        return "v2"
    if source_ids not in (None, [], ()):
        if isinstance(source_ids, (str, bytes)):
            return "v2"
        try:
            if list(source_ids) != [source_id]:
                return "v2"
        except TypeError:
            return "v2"
    # A primary-only payload is not a valid v1 record.  If both modes exist,
    # their equality (including the derived value after a round trip) keeps
    # legacy records classified as v1.
    if primary_mode is not None and question_mode is None:
        return "v2"
    if (
        primary_mode is not None
        and question_mode is not None
        and comparable_mode(primary_mode) != comparable_mode(question_mode)
    ):
        return "v2"

    stored_hash = get_value("content_hash")
    if isinstance(stored_hash, str):
        try:
            normalized = (
                record
                if isinstance(record, InterviewQuestionRecord)
                else _as_question_record(record)
            )
            if stored_hash == _build_v2_full_hash(normalized):
                return "v2"
            if stored_hash == _build_v2_hash(normalized):
                return "v2"
            if stored_hash == _build_v1_hash(normalized):
                return "v1"
        except (AttributeError, KeyError, TypeError, ValueError):
            # Schema/hash validation is performed by the caller's boundary.
            # Classification remains conservative for malformed/stale input.
            pass
    return "v1"


def _normalize_mode_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a record mapping while normalizing only the migration mode fields."""

    normalized = dict(payload)
    for field_name in ("question_mode", "primary_mode"):
        if field_name in normalized and normalized[field_name] is not None:
            normalized[field_name] = normalize_project_mode(normalized[field_name])
    if "compatible_modes" in normalized and normalized["compatible_modes"] is not None:
        values = normalized["compatible_modes"]
        if isinstance(values, (str, bytes)):
            raise TypeError("compatible_modes must be a sequence of modes")
        try:
            normalized["compatible_modes"] = [
                normalize_project_mode(value) for value in values
            ]
        except TypeError:
            raise TypeError("compatible_modes must be a sequence of modes") from None
    return normalized


def _validated_record(
    record: InterviewQuestionRecord | Mapping[str, Any],
    *,
    normalize_modes: bool = False,
) -> InterviewQuestionRecord:
    """Revalidate both model and mapping inputs at the projection boundary."""

    if isinstance(record, InterviewQuestionRecord):
        payload = record.model_dump(mode="python", warnings=False)
    elif isinstance(record, Mapping):
        payload = dict(record)
    else:
        raise TypeError(
            "question records must be InterviewQuestionRecord instances or mappings"
        )
    if normalize_modes:
        payload = _normalize_mode_fields(payload)
    try:
        return InterviewQuestionRecord.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid interview question record: {exc}") from exc


def _canonical_embedding_list(values: Sequence[Any], field_name: str) -> list[str]:
    """Normalize and casefold-sort one list used by the embedding contract."""

    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError(f"{field_name} must be a sequence")
    try:
        normalized = [normalize_question_text(value) for value in values]
    except TypeError:
        raise TypeError(f"{field_name} must be a sequence") from None
    if any(not value for value in normalized):
        raise ValueError(f"{field_name} must not contain blank values")
    # Include the canonical spelling as a deterministic tie-breaker.  This
    # keeps values such as ``A`` and ``a`` independent of source list order.
    return sorted(normalized, key=lambda value: (value.casefold(), value))


def _validate_embedding_projection_inputs(
    record: InterviewQuestionRecord | Mapping[str, Any],
) -> None:
    """Apply the projection safety policy before any mode/schema normalization."""

    if isinstance(record, InterviewQuestionRecord):
        get_value = lambda name, default=None: getattr(record, name, default)
    elif isinstance(record, Mapping):
        get_value = lambda name, default=None: record.get(name, default)
    else:
        return

    validate_embedding_text_value(get_value("question_text"), "question")
    validate_embedding_text_value(
        get_value("business_constraint", ""),
        "business_constraint",
    )

    def validate_list(values: Any, field_name: str) -> None:
        if values is None:
            return
        if isinstance(values, (str, bytes, bytearray)):
            if isinstance(values, str):
                validate_embedding_text_value(values, field_name)
            return
        try:
            iterator = enumerate(values)
        except TypeError:
            return
        for index, value in iterator:
            validate_embedding_text_value(value, f"{field_name}[{index}]")

    validate_list(get_value("skills", ()), "skills")
    validate_list(get_value("dimension_terms", ()), "dimension_terms")
    primary_mode = get_value("primary_mode")
    if primary_mode is None:
        primary_mode = get_value("question_mode")
    validate_embedding_text_value(primary_mode, "primary_mode")
    validate_list(get_value("compatible_modes", ()), "compatible_modes")


def _embedding_projection(
    record: InterviewQuestionRecord | Mapping[str, Any],
) -> QuestionEmbeddingProjection:
    """Build the six-section projection without carrying record metadata."""

    _validate_embedding_projection_inputs(record)
    validated = _validated_record(record, normalize_modes=True)
    primary_mode = normalize_project_mode(validated.primary_mode or validated.question_mode)
    compatible_modes = [
        normalize_project_mode(value) for value in validated.compatible_modes
    ]
    policy = QuestionModePolicy.default()
    mode_order = policy.compatible_order_for(validated.dimension_id)
    compatible_modes.sort(key=mode_order.index)
    policy.validate_mode_assignment(
        validated.dimension_id,
        primary_mode,
        compatible_modes,
    )
    return QuestionEmbeddingProjection(
        question=normalize_question_text(validated.question_text),
        business_constraint=normalize_question_text(validated.business_constraint),
        skills=_canonical_embedding_list(validated.skills, "skills"),
        dimension_terms=_canonical_embedding_list(
            validated.dimension_terms,
            "dimension_terms",
        ),
        primary_mode=primary_mode,
        compatible_modes=compatible_modes,
    )


def build_question_embedding_text(
    record: InterviewQuestionRecord | Mapping[str, Any],
) -> str:
    """Render a question as the frozen, candidate-safe six-line text."""

    return _embedding_projection(record).to_text()


def _v1_hash_payload(record: InterviewQuestionRecord | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(record, InterviewQuestionRecord):
        question_text = record.question_text
        role = record.role
        dimension_id = record.dimension_id
        skills = record.skills
        question_mode = record.question_mode
        difficulty = record.difficulty
    else:
        question_text = record.get("question_text")
        role = record.get("role")
        dimension_id = record.get("dimension_id")
        skills = record.get("skills")
        question_mode = record.get("question_mode")
        difficulty = record.get("difficulty")
    return {
        "question_text": normalize_question_text(question_text),
        "role": role,
        "dimension_id": dimension_id,
        "skills": sorted(normalize_question_text(value) for value in skills),
        # Keep malformed values hashable for audit diagnostics.  Strict schema
        # validation and the projection boundary reject them before runtime.
        "question_mode": question_mode,
        "difficulty": difficulty,
    }


def _v2_hash_payload(record: InterviewQuestionRecord) -> dict[str, Any]:
    primary_mode = normalize_project_mode(record.primary_mode or record.question_mode)
    compatible_modes = [normalize_project_mode(value) for value in record.compatible_modes]
    # Mode policy controls the order in the wire record; using that same order
    # for the hash makes semantically equivalent input order deterministic.
    policy = QuestionModePolicy.default()
    order = policy.compatible_order_for(record.dimension_id)
    compatible_modes.sort(key=lambda value: order.index(value))
    return {
        "question_text": normalize_question_text(record.question_text),
        "business_constraint": normalize_question_text(record.business_constraint),
        "skills": sorted(normalize_question_text(value) for value in record.skills),
        "dimension_terms": sorted(
            normalize_question_text(value) for value in record.dimension_terms
        ),
        "primary_mode": primary_mode,
        "compatible_modes": compatible_modes,
        "role": record.role,
        "dimension_id": record.dimension_id,
        "difficulty": record.difficulty,
    }


def _hash_payload(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _build_v1_hash(record: InterviewQuestionRecord | Mapping[str, Any]) -> str:
    return _hash_payload(_v1_hash_payload(record))


def _build_v2_hash(record: InterviewQuestionRecord) -> str:
    """Build the historical v2 semantic hash for compatibility reads only."""

    return _hash_payload(_v2_hash_payload(record))


_V2_SET_LIKE_FIELDS = frozenset(
    {"skills", "dimension_terms", "compatible_modes", "source_ids", "company_tags"}
)


def _validate_mode_assignment(record: InterviewQuestionRecord) -> None:
    QuestionModePolicy.default().validate_mode_assignment(
        record.dimension_id,
        record.primary_mode or record.question_mode,
        record.compatible_modes,
    )


def project_v1_record_to_v2(
    record: InterviewQuestionRecord | Mapping[str, Any],
) -> InterviewQuestionRecord:
    """Explicitly project a legacy v1 record into the additive v2 shape."""

    if classify_question_record(record) != "v1":
        raise ValueError("project_v1_record_to_v2 expects a v1 record")
    validated = _validated_record(record, normalize_modes=True)
    primary_mode = normalize_project_mode(validated.question_mode)
    payload = {
        field_name: getattr(validated, field_name)
        for field_name in _V1_RECORD_FIELDS
    }
    payload.update(
        {
            "question_mode": primary_mode,
            "business_constraint": "",
            "dimension_terms": [],
            "primary_mode": primary_mode,
            "compatible_modes": [],
            "source_ids": [validated.source_id],
            # A v2 projection has a v2 semantic identity, even when the
            # source record was hashed with the v1 payload.
            "content_hash": "sha256:" + ("0" * 64),
        }
    )
    projected = InterviewQuestionRecord.model_validate(payload)
    try:
        _validate_mode_assignment(projected)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid v2 mode assignment: {exc}") from exc
    payload["content_hash"] = _build_v2_full_hash(projected)
    return InterviewQuestionRecord.model_validate(payload)


def _project_v2_record_mapping(
    record: InterviewQuestionRecord | Mapping[str, Any],
) -> _V1Projection:
    validated = _validated_record(record)
    primary_mode = normalize_project_mode(validated.primary_mode or validated.question_mode)
    _validate_mode_assignment(validated)
    payload = {
        field_name: getattr(validated, field_name)
        for field_name in _V1_RECORD_FIELDS
    }
    payload["question_mode"] = primary_mode
    # A strict v1 reader may recalculate the legacy identity hash.  Recompute
    # it from the projected fields instead of leaking the v2 semantic hash.
    payload["content_hash"] = _build_v1_hash(payload)
    return _V1Projection(payload)


def project_v2_record_to_v1(
    record: InterviewQuestionRecord | RetrievedQuestion | Mapping[str, Any],
) -> _V1Projection:
    """Project a v2 record (or nested retrieval hit) to strict v1 fields.

    Supporting ``RetrievedQuestion`` here keeps the migration boundary
    explicit when an old caller serializes a nested retrieval payload.
    """

    if isinstance(record, RetrievedQuestion):
        return _V1Projection(
            {
                "record": _project_v2_record_mapping(record.record),
                "score": record.score,
                "index_version": record.index_version,
            }
        )
    if isinstance(record, Mapping) and (
        "record" in record or "question" in record
    ) and "question_id" not in record:
        try:
            nested = RetrievedQuestion.model_validate(record)
        except ValidationError as exc:
            raise ValueError(f"invalid retrieved question: {exc}") from exc
        return project_v2_record_to_v1(nested)
    return _project_v2_record_mapping(record)


def project_retrieved_question_to_v1(
    value: RetrievedQuestion | Mapping[str, Any],
) -> _V1Projection:
    """Named nested alias for callers migrating retrieval envelopes."""

    return project_v2_record_to_v1(value)


def project_question_retrieval_result_to_v1(
    value: QuestionRetrievalResult | Mapping[str, Any],
) -> _V1Projection:
    """Project a retrieval result while stripping additive nested fields."""

    if isinstance(value, QuestionRetrievalResult):
        result = value
    elif isinstance(value, Mapping):
        try:
            result = QuestionRetrievalResult.model_validate(value)
        except ValidationError as exc:
            raise ValueError(f"invalid question retrieval result: {exc}") from exc
    else:
        raise TypeError("retrieval result must be a QuestionRetrievalResult or mapping")

    payload = result.model_dump(mode="python", warnings=False)
    if result.selected_question is not None:
        payload["selected_question"] = project_retrieved_question_to_v1(
            result.selected_question
        )
    return _V1Projection(payload)


def _as_question_record(
    record: InterviewQuestionRecord | Mapping[str, Any],
) -> InterviewQuestionRecord:
    if isinstance(record, InterviewQuestionRecord):
        return record
    if isinstance(record, Mapping):
        try:
            return InterviewQuestionRecord.model_validate(record)
        except ValidationError as exc:
            raise ValueError(f"invalid interview question record: {exc}") from exc
    raise TypeError(
        "question records must be InterviewQuestionRecord instances or mappings"
    )


def _source_issue(record: Any) -> str | None:
    """Return ``missing`` or ``invalid`` for a record's source metadata."""

    for field_name in ("source_id", "source_url", "source_title", "source_type"):
        value = getattr(record, field_name, None)
        if not isinstance(value, str) or not value.strip():
            return "missing"

    try:
        parsed_url = urlparse(record.source_url.strip())
    except (ValueError, UnicodeError):
        # ``urlparse`` rejects malformed bracketed hosts and a few malformed
        # Unicode inputs.  Audit is a diagnostic boundary: classify those
        # values as invalid source metadata instead of aborting the report.
        return "invalid"
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return "invalid"
    if record.source_type not in SUPPORTED_SOURCE_TYPES:
        return "invalid"
    return None


def _canonical_hash(record: InterviewQuestionRecord | Mapping[str, Any]) -> str:
    """Use the shared semantic classifier for every hash/load boundary."""

    normalized = _as_question_record(record)
    if classify_question_record(normalized) == "v2":
        return _build_v2_hash(normalized)
    return _build_v1_hash(normalized)


@dataclass(frozen=True)
class _AuditRecord:
    """Small fallback view so audit can report incomplete source mappings."""

    question_id: str
    valid_until: date
    status: str | None
    source_id: Any = None
    source_url: Any = None
    source_title: Any = None
    source_type: Any = None
    invalid_reasons: tuple[str, ...] = ()


_SOURCE_FIELDS = frozenset(
    {"source_id", "source_url", "source_title", "source_type"}
)


def _validation_error_reasons(exc: ValidationError) -> tuple[str, ...]:
    """Keep non-source schema failures as auditable diagnostics."""

    reasons: list[str] = []
    for error in exc.errors():
        location = error.get("loc", ())
        field_name = str(location[0]) if location else "<root>"
        if field_name in _SOURCE_FIELDS:
            continue
        path = ".".join(str(part) for part in location) or "<root>"
        reasons.append(f"{path}: {error.get('msg', 'validation error')}")
    return tuple(reasons)


def _coerce_audit_record(
    record: InterviewQuestionRecord | Mapping[str, Any],
    index: int,
) -> InterviewQuestionRecord | _AuditRecord:
    if isinstance(record, InterviewQuestionRecord):
        return record
    if not isinstance(record, Mapping):
        raise TypeError(
            "question records must be InterviewQuestionRecord instances or mappings"
        )

    try:
        return InterviewQuestionRecord.model_validate(record)
    except ValidationError as exc:
        validation_error = exc
        question_id = record.get("question_id")
        if not isinstance(question_id, str) or not question_id.strip():
            question_id = f"<record:{index}>"
        valid_until = record.get("valid_until")
        if isinstance(valid_until, str):
            try:
                valid_until = date.fromisoformat(valid_until)
            except ValueError:
                valid_until = date.max
        if not isinstance(valid_until, date):
            valid_until = date.max
        status = record.get("status")
        if not isinstance(status, str):
            status = None
        return _AuditRecord(
            question_id=question_id,
            valid_until=valid_until,
            status=status,
            source_id=record.get("source_id"),
            source_url=record.get("source_url"),
            source_title=record.get("source_title"),
            source_type=record.get("source_type"),
            invalid_reasons=_validation_error_reasons(validation_error),
        )


def _record_validation_issues(
    record: InterviewQuestionRecord | _AuditRecord,
    *,
    supported_dimension_ids: Collection[str] | None = None,
) -> tuple[str, ...]:
    """Re-run the complete record schema before an audit can mark eligibility."""

    existing = tuple(getattr(record, "invalid_reasons", ()))
    if isinstance(record, _AuditRecord):
        return existing

    issues = list(existing)
    try:
        InterviewQuestionRecord.model_validate(record.model_dump(mode="python"))
    except ValidationError as exc:
        issues.extend(_validation_error_reasons(exc))
    except (TypeError, ValueError) as exc:
        issues.append(f"record: unable to validate schema ({exc})")

    dimensions = (
        frozenset(supported_dimension_ids)
        if supported_dimension_ids is not None
        else SUPPORTED_DIMENSION_IDS
    )
    if record.dimension_id not in dimensions:
        issues.append(f"dimension_id: unsupported value {record.dimension_id!r}")
    try:
        record_kind = classify_question_record(record)
        legacy_hash = (
            _build_v2_hash(record)
            if record_kind == "v2"
            else _build_v1_hash(record)
        )
        computed_hash = compute_question_content_hash(record)
    except (TypeError, ValueError, AttributeError) as exc:
        issues.append(f"content_hash: unable to compute canonical hash ({exc})")
    else:
        if record.content_hash not in {legacy_hash, computed_hash}:
            issues.append(
                "content_hash mismatch: stored value does not match canonical hash"
            )
    try:
        _validate_mode_assignment(record)
    except (TypeError, ValueError, AttributeError) as exc:
        issues.append(f"mode policy: {exc}")
    return tuple(dict.fromkeys(issues))


def build_question_content_hash(
    record: InterviewQuestionRecord | Mapping[str, Any],
) -> str:
    """Build the v1-compatible or canonical v2 question identity.

    v1 records retain their historical semantic hash.  A normal v2 record
    hashes every canonical record field except ``content_hash`` itself, while
    the historical v2 hash is returned only when the stored value exactly
    matches that legacy payload for an explicit compatibility read.
    """

    try:
        normalized = _validated_record(record, normalize_modes=True)
    except (TypeError, ValueError):
        # ``model_copy(update=...)`` is intentionally allowed by the audit
        # tests to create malformed typed probes.  Preserve the historical
        # best-effort hash helper for those probes; load/compute validation
        # remains strict and cannot accept their result as a valid record.
        if isinstance(record, InterviewQuestionRecord):
            return _canonical_hash(record)
        raise
    if classify_question_record(record) == "v1":
        return _build_v1_hash(normalized)

    # Keep old v2 fixture/bank values readable through this compatibility
    # helper, but never use that legacy payload for a newly projected v2
    # record.  The canonical v2 path below includes every record field.
    if normalized.content_hash == _build_v2_hash(normalized):
        return normalized.content_hash
    return _build_v2_full_hash(normalized)


def _canonical_hash_value(value: Any, *, sort_lists: bool = False) -> Any:
    """Normalize nested JSON values before hashing a complete record."""

    if isinstance(value, str):
        return normalize_question_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_hash_value(item, sort_lists=sort_lists)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        normalized = [
            _canonical_hash_value(item, sort_lists=sort_lists) for item in value
        ]
        if not sort_lists:
            return normalized
        # Callers opt into sorting only for a field whose schema semantics are
        # set-like.  Ordered rubric/metadata lists must retain source order so
        # reversing them changes the v2 content identity.
        return sorted(
            normalized,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
    return value


def _complete_v2_hash_payload(record: InterviewQuestionRecord) -> dict[str, Any]:
    """Return every v2 record field except its self-referential content hash."""

    payload = record.model_dump(mode="json", warnings=False)
    payload.pop("content_hash", None)
    payload["hash_version"] = QUESTION_CONTENT_HASH_VERSION
    canonical = _canonical_hash_value(payload, sort_lists=False)
    for field_name in _V2_SET_LIKE_FIELDS:
        values = canonical.get(field_name)
        if isinstance(values, list):
            canonical[field_name] = sorted(
                values,
                key=lambda item: json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
    return canonical


def _build_v2_full_hash(record: InterviewQuestionRecord) -> str:
    """Build the canonical full-record v2 identity used for new artifacts."""

    return _hash_payload(_complete_v2_hash_payload(record))


def compute_question_content_hash(
    record: InterviewQuestionRecord | Mapping[str, Any],
) -> str:
    """Compute the versioned full record identity used by corpus manifests.

    v1 records retain the historical hash payload so old banks, checkpoints
    and public projections continue to validate.  A v2 record hashes all
    canonical fields (including non-embedding metadata) except ``content_hash``
    itself; its embedding text remains the narrower six-section projection.
    """

    normalized = _validated_record(record, normalize_modes=True)
    if classify_question_record(record) == "v1":
        return _build_v1_hash(normalized)
    return _build_v2_full_hash(normalized)


def compute_question_bank_manifest_hash(
    records: Sequence[InterviewQuestionRecord | Mapping[str, Any]],
    source_registry: QuestionSourceRegistry | Mapping[str, Any],
    policy: QuestionModePolicy | Mapping[str, Any],
) -> str:
    """Compute a stable hash for the records, source summaries and mode policy."""

    try:
        normalized_records = [
            _validated_record(record, normalize_modes=True) for record in records
        ]
    except TypeError as exc:
        raise TypeError("records must be a sequence") from exc
    if isinstance(source_registry, QuestionSourceRegistry):
        normalized_registry = source_registry
    elif isinstance(source_registry, Mapping):
        try:
            normalized_registry = QuestionSourceRegistry.model_validate(source_registry)
        except Exception as exc:
            raise ValueError("invalid question source registry") from exc
    else:
        raise TypeError("source_registry must be a QuestionSourceRegistry or mapping")
    if isinstance(policy, QuestionModePolicy):
        normalized_policy = policy
    elif isinstance(policy, Mapping):
        try:
            normalized_policy = QuestionModePolicy.model_validate(policy)
        except Exception as exc:
            raise ValueError("invalid question mode policy") from exc
    else:
        raise TypeError("policy must be a QuestionModePolicy or mapping")

    record_payload = [
        {
            "question_id": record.question_id,
            "content_hash": compute_question_content_hash(record),
            "embedding_text": build_question_embedding_text(record),
        }
        for record in normalized_records
    ]
    record_payload.sort(key=lambda value: value["question_id"])
    source_payload = [
        _canonical_hash_value(entry.model_dump(mode="json", warnings=False))
        for entry in normalized_registry.entries
    ]
    source_payload.sort(key=lambda value: value.get("source_id", ""))
    payload = {
        "records": record_payload,
        "sources": source_payload,
        "mode_policy": _canonical_hash_value(
            normalized_policy.model_dump(mode="json", warnings=False),
            sort_lists=False,
        ),
    }
    return _hash_payload(payload)


def _read_question_bank(path: str | Path) -> Mapping[str, Any]:
    bank_path = Path(path)
    try:
        raw = bank_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"unable to read question bank: {bank_path}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid question bank JSON: {bank_path}") from exc

    if not isinstance(payload, Mapping):
        raise ValueError("question bank JSON root must be an object")
    return payload


def _validate_bank_root(
    payload: Mapping[str, Any],
    *,
    allow_test_only: bool,
    expected_role: str,
    expected_role_version: str | None,
) -> list[Mapping[str, Any]]:
    schema_version = payload.get("schema_version", payload.get("version"))
    if (
        isinstance(schema_version, bool)
        or type(schema_version) not in {int, str}
        or schema_version not in SUPPORTED_SCHEMA_VERSIONS
    ):
        raise ValueError(
            "question bank schema_version must be one of "
            + ", ".join(sorted(map(str, SUPPORTED_SCHEMA_VERSIONS)))
        )

    test_only = payload.get("test_only", False)
    if not isinstance(test_only, bool):
        raise ValueError("question bank test_only must be a boolean")
    if test_only and not allow_test_only:
        raise ValueError(
            "test-only question bank requires an explicit test dependency"
        )

    root_role = payload.get("role")
    if root_role != expected_role:
        raise ValueError(f"unsupported question bank role: {root_role!r}")

    root_role_version = payload.get("role_version")
    if not isinstance(root_role_version, str) or not root_role_version.strip():
        raise ValueError("question bank role_version must be a non-blank string")
    if (
        expected_role_version is not None
        and root_role_version != expected_role_version
    ):
        raise ValueError(
            "question bank role_version does not match "
            f"{expected_role_version!r}"
        )

    questions = payload.get("questions")
    if questions is None:
        questions = payload.get("records")
    if not isinstance(questions, list):
        raise ValueError("question bank questions must be a list")
    if not all(isinstance(question, Mapping) for question in questions):
        raise ValueError("each question bank item must be an object")
    return questions


def load_question_bank(
    path: str | Path,
    *,
    allow_test_only: bool = False,
    test_dependency: object | None = None,
    expected_role: str = SUPPORTED_ROLE,
    expected_role_version: str | None = None,
    supported_dimension_ids: Collection[str] | None = None,
) -> list[InterviewQuestionRecord]:
    """Load, validate and de-duplicate canonical records from a JSON bank.

    ``allow_test_only`` is intentionally opt-in.  A test dependency can be
    supplied by callers that need the synthetic ``example.com`` fixture; a
    production command should leave both options at their safe defaults.
    """

    if expected_role != SUPPORTED_ROLE:
        raise ValueError(f"unsupported question bank role: {expected_role!r}")

    payload = _read_question_bank(path)
    questions = _validate_bank_root(
        payload,
        allow_test_only=allow_test_only or test_dependency is not None,
        expected_role=expected_role,
        expected_role_version=expected_role_version,
    )
    dimensions = (
        frozenset(supported_dimension_ids)
        if supported_dimension_ids is not None
        else SUPPORTED_DIMENSION_IDS
    )

    records: list[InterviewQuestionRecord] = []
    seen_question_ids: set[str] = set()
    seen_content_hashes: set[str] = set()
    bank_role_version = payload["role_version"]
    bank_is_test_only = payload.get("test_only", False)
    test_only_allowed = allow_test_only or test_dependency is not None

    for index, question in enumerate(questions):
        try:
            record = InterviewQuestionRecord.model_validate(
                _normalize_mode_fields(question)
            )
        except ValidationError as exc:
            raise ValueError(f"invalid question bank record at index {index}: {exc}") from exc

        if record.role != expected_role:
            raise ValueError(
                f"unsupported question role at index {index}: {record.role!r}"
            )
        if expected_role_version is not None and record.role_version != expected_role_version:
            raise ValueError(
                f"question role_version mismatch at index {index}: "
                f"{record.role_version!r}"
            )
        if record.role_version != bank_role_version:
            raise ValueError(
                f"question role_version does not match bank root at index {index}: "
                f"{record.role_version!r}"
            )
        if record.source_type == "test_only_synthetic":
            if not test_only_allowed:
                raise ValueError(
                    "test_only_synthetic records require an explicit test dependency"
                )
            if bank_is_test_only is not True:
                raise ValueError(
                    "test_only_synthetic records require root test_only=true"
                )
        record_issues = _record_validation_issues(
            record,
            supported_dimension_ids=dimensions,
        )
        if record_issues:
            raise ValueError(
                f"invalid question bank record at index {index}: "
                + "; ".join(record_issues)
            )
        source_issue = _source_issue(record)
        if source_issue is not None:
            raise ValueError(
                f"invalid source for question_id {record.question_id}: "
                f"{source_issue}"
            )
        if record.question_id in seen_question_ids:
            raise ValueError(f"duplicate question_id: {record.question_id}")

        # New v2 records include the full canonical record identity in their
        # content hash.  The historical v1 hash remains accepted above for
        # migration fixtures and old checkpoint/public-output round trips.
        computed_hash = compute_question_content_hash(record)
        if computed_hash in seen_content_hashes:
            raise ValueError(
                "duplicate content_hash: "
                f"{computed_hash} (question_id {record.question_id})"
            )

        seen_question_ids.add(record.question_id)
        seen_content_hashes.add(computed_hash)
        records.append(record)

    return records


@dataclass(frozen=True)
class QuestionBankRuntimeIdentity:
    """Verified local inputs shared by index writers and persistent readers.

    The vector index is disposable, but its fingerprint must be derived from
    the same validated records, source registry and frozen mode policy on both
    sides of the boundary.  ``catalog`` is intentionally the complete
    canonical record map; Qdrant payloads are only a retrieval projection.
    """

    catalog: dict[str, InterviewQuestionRecord]
    source_registry: QuestionSourceRegistry
    policy: QuestionModePolicy
    manifest_hash: str
    bank_manifest: QuestionBankManifest | None = None


_RUNTIME_SOURCE_REGISTRY_ENV_NAMES: tuple[str, ...] = (
    "QUESTION_RAG_SOURCE_REGISTRY_PATH",
    "QUESTION_RAG_SOURCE_REGISTRY",
    "QUESTION_RAG_QUESTION_SOURCE_REGISTRY_PATH",
    "QUESTION_SOURCE_REGISTRY_PATH",
)
_RUNTIME_MANIFEST_ENV_NAMES: tuple[str, ...] = (
    "QUESTION_RAG_MANIFEST_PATH",
    "QUESTION_RAG_MANIFEST",
    "QUESTION_RAG_BANK_MANIFEST_PATH",
    "QUESTION_RAG_QUESTION_BANK_MANIFEST_PATH",
    "QUESTION_BANK_MANIFEST_PATH",
)


def _runtime_environment_path(
    environment: Mapping[str, Any] | None,
    names: Sequence[str],
) -> Path | None:
    if environment is None:
        return None
    for name in names:
        value = environment.get(name)
        if value is None:
            continue
        text = value if isinstance(value, str) else str(value)
        if text.strip():
            return Path(text.strip())
    return None


def _runtime_source_entries(raw: Any) -> list[Mapping[str, Any]]:
    if isinstance(raw, Mapping):
        if "entries" in raw:
            raw = raw["entries"]
        elif "sources" in raw:
            raw = raw["sources"]
        elif any(
            key in raw
            for key in ("source_id", "id", "canonical_url", "url")
        ):
            raw = [raw]
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise ValueError("question source registry must contain entries")
    entries: list[Mapping[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise ValueError("question source registry entries must be objects")
        entries.append(entry)
    if not entries:
        raise ValueError("question source registry must contain entries")
    return entries


def _runtime_registry_from_records(
    records: Sequence[InterviewQuestionRecord],
) -> QuestionSourceRegistry:
    """Build an explicit compatibility registry from validated v1 records.

    Older banks predate a source-registry sidecar.  Their source URL/title and
    verification dates are already validated by ``load_question_bank``; using
    those fields as a grouped registry is a deterministic compatibility path,
    not a fabricated manifest.  Unsupported/ambiguous source metadata fails
    closed so production readers cannot silently bless it.
    """

    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.source_type == "test_only_synthetic":
            raise ValueError("test-only records cannot build a production source registry")
        if record.source_type not in {
            "public_interview_experience",
            "official_technical_doc",
            "current_enterprise_jd",
        }:
            raise ValueError("question source type is not supported by the runtime registry")
        source_id = record.source_id
        source_ids = record.source_ids or [record.source_id]
        for referenced_id in source_ids:
            if referenced_id != source_id:
                # A sidecar is required to resolve additional v2 sources.  Do
                # not guess their metadata from the primary source.
                raise ValueError("additional source_ids require a source registry sidecar")
        entry = grouped.get(source_id)
        values = {
            "source_id": source_id,
            "source_type": record.source_type,
            "canonical_url": record.source_url,
            "title": record.source_title,
            "publisher": "",
            "published_at": record.published_at,
            "verified_at": record.verified_at,
            "accessed_at": record.verified_at,
            "trust": record.trust_level,
            "lifecycle": "active" if record.status == "active" else record.status,
            "question_ids": [record.question_id],
        }
        if entry is None:
            grouped[source_id] = values
            continue
        for field_name in (
            "source_type",
            "canonical_url",
            "title",
            "published_at",
            "verified_at",
            "trust",
        ):
            if entry[field_name] != values[field_name]:
                raise ValueError("records disagree on source registry metadata")
        entry["question_ids"].append(record.question_id)
        if record.status == "active":
            entry["lifecycle"] = "active"
    return QuestionSourceRegistry.model_validate({"entries": list(grouped.values())})


def _runtime_normalize_registry(
    raw: Any,
    records: Sequence[InterviewQuestionRecord],
) -> QuestionSourceRegistry:
    entries: list[dict[str, Any]] = []
    record_ids_by_source: dict[str, list[str]] = {}
    for record in records:
        for source_id in (record.source_ids or [record.source_id]):
            record_ids_by_source.setdefault(source_id, []).append(record.question_id)

    for raw_entry in _runtime_source_entries(raw):
        entry = dict(raw_entry)
        accepted_keys = {
            "source_id",
            "id",
            "source_type",
            "source_class",
            "canonical_url",
            "url",
            "source_url",
            "title",
            "source_title",
            "publisher",
            "published_at",
            "verified_at",
            "accessed_at",
            "retrieved_at",
            "trust",
            "trust_level",
            "lifecycle",
            "question_ids",
            "human_summary",
            "review_class",
            "date_basis",
            "role_level",
            "dimension_ids",
            "supports_dimension_ids",
            "access_status",
            "next_review_at",
            "rights_status",
            "notes",
        }
        if set(entry).difference(accepted_keys):
            raise ValueError("question source registry contains unknown fields")
        source_id = entry.get("source_id", entry.get("id"))
        canonical_url = entry.get(
            "canonical_url",
            entry.get("url", entry.get("source_url")),
        )
        title = entry.get("title", entry.get("source_title"))
        published_at = entry.get("published_at")
        verified_at = entry.get("verified_at")
        accessed_at = entry.get("accessed_at", entry.get("retrieved_at"))
        date_fallback = date(2026, 8, 27)
        if verified_at is None:
            verified_at = accessed_at or published_at or date_fallback
        if accessed_at is None:
            accessed_at = verified_at
        entries.append(
            {
                "source_id": source_id,
                "canonical_url": canonical_url,
                "title": title,
                "publisher": entry.get("publisher", ""),
                "published_at": published_at,
                "verified_at": verified_at,
                "accessed_at": accessed_at,
                "source_type": entry.get(
                    "source_type", entry.get("source_class", "public_interview_experience")
                ),
                "trust": entry.get("trust", entry.get("trust_level", "medium")),
                "lifecycle": entry.get("lifecycle", "draft"),
                "question_ids": entry.get(
                    "question_ids", record_ids_by_source.get(source_id, [])
                ),
                "human_summary": entry.get("human_summary", ""),
                "review_class": entry.get("review_class", "dynamic"),
                "date_basis": entry.get(
                    "date_basis",
                    "published_at" if published_at is not None else "retrieved_at",
                ),
                "role_level": entry.get("role_level", ""),
                "dimension_ids": entry.get(
                    "dimension_ids", entry.get("supports_dimension_ids", [])
                ),
                "access_status": entry.get("access_status", "accessible"),
                "next_review_at": entry.get("next_review_at"),
                "rights_status": entry.get("rights_status", "pending"),
                "notes": entry.get("notes", ""),
            }
        )
    registry = QuestionSourceRegistry.model_validate({"entries": entries})
    by_id = {entry.source_id: entry for entry in registry.entries}
    for record in records:
        for source_id in (record.source_ids or [record.source_id]):
            entry = by_id.get(source_id)
            if entry is None:
                raise ValueError("question source registry is missing a referenced source")
            if entry.question_ids and record.question_id not in entry.question_ids:
                raise ValueError("question source registry question_ids do not match records")
    return registry


def _runtime_read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("question source registry could not be read") from exc


def _runtime_source_registry_payload(
    bank_path: str | Path | None,
    source_registry_path: str | Path | None,
    environment: Mapping[str, Any] | None,
) -> tuple[Any | None, bool]:
    explicit_path: Path | None
    if source_registry_path is not None:
        explicit_path = Path(source_registry_path)
    else:
        explicit_path = _runtime_environment_path(
            environment,
            _RUNTIME_SOURCE_REGISTRY_ENV_NAMES,
        )
    if explicit_path is not None:
        return _runtime_read_json(explicit_path), True

    if bank_path is None:
        return None, False
    path = Path(bank_path)
    try:
        root = _runtime_read_json(path)
    except ValueError:
        root = None
    if isinstance(root, Mapping):
        for key in ("source_registry", "sources"):
            if key in root:
                return root[key], True

    candidates = (
        path.with_name("QuestionSourceRegistry.json"),
        path.with_name("question_source_registry.json"),
        path.with_name("sources.json"),
        path.with_name(f"{path.stem}.sources.json"),
        path.with_name(f"{path.stem}_sources.json"),
    )
    for candidate in candidates:
        if candidate == path:
            continue
        try:
            if candidate.is_file():
                return _runtime_read_json(candidate), True
        except OSError:
            continue
    return None, False


def _runtime_bank_manifest_payload(
    bank_path: str | Path | None,
    manifest_path: str | Path | None,
    environment: Mapping[str, Any] | None,
) -> Any | None:
    explicit_path: Path | None
    if manifest_path is not None:
        explicit_path = Path(manifest_path)
    else:
        explicit_path = _runtime_environment_path(
            environment,
            _RUNTIME_MANIFEST_ENV_NAMES,
        )
    if explicit_path is not None:
        return _runtime_read_json(explicit_path)
    if bank_path is None:
        return None
    path = Path(bank_path)
    try:
        root = _runtime_read_json(path)
    except ValueError:
        root = None
    if isinstance(root, Mapping):
        for key in ("manifest", "bank_manifest", "question_bank_manifest"):
            if key in root:
                return root[key]
    candidates = (
        path.with_name("QuestionBankManifest.json"),
        path.with_name("question_bank_manifest.json"),
        path.with_name(f"{path.stem}.manifest.json"),
        path.with_name(f"{path.stem}_manifest.json"),
    )
    for candidate in candidates:
        try:
            if candidate.is_file():
                return _runtime_read_json(candidate)
        except OSError:
            continue
    return None


def load_question_bank_runtime_identity(
    records: Sequence[InterviewQuestionRecord],
    *,
    bank_path: str | Path | None = None,
    source_registry_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    environment: Mapping[str, Any] | None = None,
    policy: QuestionModePolicy | Mapping[str, Any] | None = None,
) -> QuestionBankRuntimeIdentity:
    """Resolve the verified catalog and manifest inputs for a local runtime.

    ``records`` must have already passed ``load_question_bank``.  A missing
    sidecar is accepted only through the explicit v1 compatibility projection
    above; no new hash is invented when the records themselves cannot provide
    a complete, validated source identity.
    """

    if isinstance(records, (str, bytes, bytearray)):
        raise TypeError("records must be a sequence")
    normalized_records = list(records)
    if not normalized_records or not all(
        isinstance(record, InterviewQuestionRecord) for record in normalized_records
    ):
        raise ValueError("records must contain validated interview question records")
    if policy is None:
        normalized_policy = QuestionModePolicy.default()
    elif isinstance(policy, QuestionModePolicy):
        normalized_policy = policy
    elif isinstance(policy, Mapping):
        normalized_policy = QuestionModePolicy.model_validate(policy)
    else:
        raise TypeError("policy must be a QuestionModePolicy or mapping")
    raw_registry, has_sidecar = _runtime_source_registry_payload(
        bank_path,
        source_registry_path,
        environment,
    )
    registry = (
        _runtime_normalize_registry(raw_registry, normalized_records)
        if has_sidecar
        else _runtime_registry_from_records(normalized_records)
    )
    raw_manifest = _runtime_bank_manifest_payload(
        bank_path,
        manifest_path,
        environment,
    )
    bank_manifest: QuestionBankManifest | None = None
    if raw_manifest is not None:
        try:
            bank_manifest = QuestionBankManifest.model_validate(raw_manifest)
        except Exception as exc:
            schema_marker = (
                raw_manifest.get("schema_version", raw_manifest.get("version"))
                if isinstance(raw_manifest, Mapping)
                else None
            )
            legacy_manifest = schema_marker in {1, "1", "v1", "question_bank.v1"}
            if not (
                legacy_manifest
                and all(classify_question_record(record) == "v1" for record in normalized_records)
            ):
                raise ValueError("question bank manifest is invalid") from exc
            # A v1 manifest is an explicit compatibility read.  It is not
            # allowed to populate a new v2 projection or hash field.
            bank_manifest = None
        if bank_manifest is not None:
            if (
                bank_manifest.question_count != len(normalized_records)
                or set(bank_manifest.question_ids)
                != {record.question_id for record in normalized_records}
            ):
                raise ValueError("question bank manifest does not match records")
            if bank_manifest.mode_policy_version != normalized_policy.mode_policy_version:
                raise ValueError("question bank manifest mode policy does not match")
    manifest_hash = compute_question_bank_manifest_hash(
        normalized_records,
        registry,
        normalized_policy,
    )
    catalog = {record.question_id: record for record in normalized_records}
    return QuestionBankRuntimeIdentity(
        catalog=catalog,
        source_registry=registry,
        policy=normalized_policy,
        manifest_hash=manifest_hash,
        bank_manifest=bank_manifest,
    )


@dataclass(frozen=True)
class QuestionBankAudit(Mapping[str, object]):
    """Read-only lifecycle findings for a loaded question bank.

    Lists in this report are newly allocated by :func:`audit_question_bank`;
    auditing never changes the source records or their ordering.
    """

    as_of: date
    expiring_within_days: int
    expired_question_ids: list[str]
    expiring_soon_question_ids: list[str]
    needs_review_question_ids: list[str]
    retired_question_ids: list[str]
    missing_source_question_ids: list[str]
    invalid_source_question_ids: list[str]
    invalid_record_question_ids: list[str]
    invalid_record_reasons: dict[str, list[str]]
    inactive_question_ids: list[str]
    eligible_question_ids: list[str]

    @property
    def expiring_question_ids(self) -> list[str]:
        """Compatibility alias for the complete expiring-soon finding."""

        return list(self.expiring_soon_question_ids)

    @property
    def expired_ids(self) -> list[str]:
        return list(self.expired_question_ids)

    @property
    def expiring_ids(self) -> list[str]:
        return list(self.expiring_soon_question_ids)

    @property
    def needs_review_ids(self) -> list[str]:
        return list(self.needs_review_question_ids)

    @property
    def retired_ids(self) -> list[str]:
        return list(self.retired_question_ids)

    @property
    def missing_source_ids(self) -> list[str]:
        return list(self.missing_source_question_ids)

    @property
    def invalid_source_ids(self) -> list[str]:
        return list(self.invalid_source_question_ids)

    @property
    def invalid_record_ids(self) -> list[str]:
        return list(self.invalid_record_question_ids)

    @property
    def active_question_ids(self) -> list[str]:
        return list(self.eligible_question_ids)

    @property
    def has_expired(self) -> bool:
        return bool(self.expired_question_ids)

    @property
    def has_expiring(self) -> bool:
        return bool(self.expiring_soon_question_ids)

    def _as_mapping(self) -> dict[str, object]:
        return {
            "as_of": self.as_of,
            "expiring_within_days": self.expiring_within_days,
            "expired_question_ids": list(self.expired_question_ids),
            "expiring_soon_question_ids": list(self.expiring_soon_question_ids),
            "expiring_question_ids": list(self.expiring_soon_question_ids),
            "needs_review_question_ids": list(self.needs_review_question_ids),
            "retired_question_ids": list(self.retired_question_ids),
            "missing_source_question_ids": list(self.missing_source_question_ids),
            "invalid_source_question_ids": list(self.invalid_source_question_ids),
            "invalid_record_question_ids": list(self.invalid_record_question_ids),
            "invalid_record_reasons": {
                question_id: list(reasons)
                for question_id, reasons in self.invalid_record_reasons.items()
            },
            "inactive_question_ids": list(self.inactive_question_ids),
            "eligible_question_ids": list(self.eligible_question_ids),
            # Short aliases make the report convenient for CLI serializers.
            "expired": list(self.expired_question_ids),
            "expiring": list(self.expiring_soon_question_ids),
            "needs_review": list(self.needs_review_question_ids),
            "retired": list(self.retired_question_ids),
            "missing_source": list(self.missing_source_question_ids),
            "invalid_source": list(self.invalid_source_question_ids),
            "invalid_record": list(self.invalid_record_question_ids),
        }

    def __getitem__(self, key: str) -> object:
        return self._as_mapping()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._as_mapping())

    def __len__(self) -> int:
        return len(self._as_mapping())


def audit_question_bank(
    records: Iterable[InterviewQuestionRecord | Mapping[str, Any]],
    as_of: date | None = None,
    *,
    expiring_within_days: int = DEFAULT_EXPIRING_WITHIN_DAYS,
    today: date | None = None,
    expiry_warning_days: int | None = None,
) -> QuestionBankAudit:
    """Report expired and soon-to-expire records without mutating them.

    A record is expiring when it is not yet expired and its ``valid_until``
    date is within the inclusive warning window, regardless of lifecycle
    status.  Eligibility mirrors runtime retrieval: only active records with
    ``valid_until >= as_of`` and valid source metadata are eligible.
    """

    if as_of is not None and today is not None:
        raise ValueError("pass only one of as_of and today")
    if today is not None:
        as_of = today
    if as_of is None:
        as_of = date.today()
    if not isinstance(as_of, date):
        raise TypeError("as_of must be a date")

    if expiry_warning_days is not None:
        if expiring_within_days != DEFAULT_EXPIRING_WITHIN_DAYS:
            raise ValueError(
                "pass only one of expiring_within_days and expiry_warning_days"
            )
        expiring_within_days = expiry_warning_days
    if not isinstance(expiring_within_days, int) or isinstance(
        expiring_within_days, bool
    ):
        raise TypeError("expiring_within_days must be an integer")
    if expiring_within_days < 0:
        raise ValueError("expiring_within_days must not be negative")

    # Materialize into a tuple so sorting/reporting cannot reorder a caller's
    # list, and validate mappings without modifying the provided records.  The
    # fallback view lets audit preserve source findings even when a raw mapping
    # is missing one of the source fields that the loader would reject.
    snapshot = tuple(
        _coerce_audit_record(record, index)
        for index, record in enumerate(records)
    )
    validation_issues_by_id = {
        record.question_id: _record_validation_issues(record)
        for record in snapshot
    }
    expiring_cutoff = as_of + timedelta(days=expiring_within_days)

    expired_question_ids = sorted(
        record.question_id
        for record in snapshot
        if record.valid_until < as_of
    )
    expiring_soon_question_ids = sorted(
        record.question_id
        for record in snapshot
        if as_of <= record.valid_until <= expiring_cutoff
    )
    needs_review_question_ids = sorted(
        record.question_id
        for record in snapshot
        if record.status == "needs_review"
    )
    retired_question_ids = sorted(
        record.question_id
        for record in snapshot
        if record.status == "retired"
    )
    missing_source_question_ids = sorted(
        record.question_id
        for record in snapshot
        if _source_issue(record) == "missing"
    )
    invalid_source_question_ids = sorted(
        record.question_id
        for record in snapshot
        if _source_issue(record) == "invalid"
    )
    invalid_record_question_ids = sorted(
        question_id
        for question_id, issues in validation_issues_by_id.items()
        if issues
    )
    invalid_record_reasons = {
        question_id: list(issues)
        for question_id, issues in validation_issues_by_id.items()
        if issues
    }
    inactive_question_ids = sorted(
        record.question_id for record in snapshot if record.status != "active"
    )
    eligible_question_ids = sorted(
        record.question_id
        for record in snapshot
        if (
            record.status == "active"
            and record.valid_until >= as_of
            and _source_issue(record) is None
            and not validation_issues_by_id[record.question_id]
        )
    )

    return QuestionBankAudit(
        as_of=as_of,
        expiring_within_days=expiring_within_days,
        expired_question_ids=expired_question_ids,
        expiring_soon_question_ids=expiring_soon_question_ids,
        needs_review_question_ids=needs_review_question_ids,
        retired_question_ids=retired_question_ids,
        missing_source_question_ids=missing_source_question_ids,
        invalid_source_question_ids=invalid_source_question_ids,
        invalid_record_question_ids=invalid_record_question_ids,
        invalid_record_reasons=invalid_record_reasons,
        inactive_question_ids=inactive_question_ids,
        eligible_question_ids=eligible_question_ids,
    )


__all__ = [
    "DEFAULT_EXPIRING_WITHIN_DAYS",
    "EMBEDDING_TEXT_VERSION",
    "UNAVAILABLE_QUESTION_BANK_MANIFEST_HASH",
    "QUESTION_CONTENT_HASH_VERSION",
    "QuestionBankAudit",
    "QuestionBankRuntimeIdentity",
    "SUPPORTED_DIMENSION_IDS",
    "SUPPORTED_ROLE",
    "SUPPORTED_SOURCE_TYPES",
    "audit_question_bank",
    "build_question_content_hash",
    "build_question_embedding_text",
    "classify_question_record",
    "compute_question_bank_manifest_hash",
    "compute_question_content_hash",
    "load_question_bank_runtime_identity",
    "load_question_bank",
    "normalize_project_mode",
    "normalize_question_text",
    "project_question_retrieval_result_to_v1",
    "project_retrieved_question_to_v1",
    "project_v1_record_to_v2",
    "project_v2_record_to_v1",
]
