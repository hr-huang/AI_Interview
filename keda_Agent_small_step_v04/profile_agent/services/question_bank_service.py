"""Load and audit the versioned, canonical JSON interview question bank.

The JSON bank is the source of truth.  This module deliberately contains no
indexing or network code: it validates source records, computes their stable
content identity, and reports lifecycle boundaries for callers that build a
disposable retrieval index later.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from profile_agent.schemas.question_rag_schema import InterviewQuestionRecord


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
    }
)
DEFAULT_EXPIRING_WITHIN_DAYS = 30


def normalize_question_text(value: str) -> str:
    """Collapse runs of Unicode whitespace into one ordinary space.

    Question text and skill labels are kept case-sensitive; normalization only
    removes formatting noise so content identity is stable across line wraps
    and indentation changes.
    """

    if not isinstance(value, str):
        raise TypeError("question text must be a string")
    return " ".join(value.split())


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

    parsed_url = urlparse(record.source_url.strip())
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return "invalid"
    if record.source_type not in SUPPORTED_SOURCE_TYPES:
        return "invalid"
    return None


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
        computed_hash = build_question_content_hash(record)
    except (TypeError, ValueError, AttributeError) as exc:
        issues.append(f"content_hash: unable to compute canonical hash ({exc})")
    else:
        if record.content_hash != computed_hash:
            issues.append(
                "content_hash mismatch: stored value does not match canonical hash"
            )
    return tuple(dict.fromkeys(issues))


def build_question_content_hash(
    record: InterviewQuestionRecord | Mapping[str, Any],
) -> str:
    """Build the deterministic identity hash for semantic question fields.

    Provenance, lifecycle, version and question id are intentionally omitted:
    changing any of those fields must not make the same semantic question look
    like a new question.  Whitespace in the question text and skill labels is
    normalized, and skill order is not significant.
    """

    record = _as_question_record(record)
    payload = {
        "question_text": normalize_question_text(record.question_text),
        "role": record.role,
        "dimension_id": record.dimension_id,
        "skills": sorted(normalize_question_text(value) for value in record.skills),
        "question_mode": record.question_mode,
        "difficulty": record.difficulty,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


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
            record = InterviewQuestionRecord.model_validate(question)
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

        computed_hash = build_question_content_hash(record)
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
    "QuestionBankAudit",
    "SUPPORTED_DIMENSION_IDS",
    "SUPPORTED_ROLE",
    "SUPPORTED_SOURCE_TYPES",
    "audit_question_bank",
    "build_question_content_hash",
    "load_question_bank",
    "normalize_question_text",
]
