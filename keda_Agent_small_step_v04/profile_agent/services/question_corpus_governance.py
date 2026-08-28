"""Offline governance loader and validator for the versioned question corpus.

The question bank is deliberately split into a canonical record file and a
small set of audit sidecars.  This module joins those files for validation;
it never performs network access, creates an embedding client, or writes the
Qdrant index.  Validation is also useful for draft corpora, so findings are
returned as deterministic :class:`CorpusIssue` values instead of mutating or
silently repairing input data.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import ValidationError

from profile_agent.schemas.question_rag_schema import (
    CORPUS_AS_OF,
    CORPUS_ROLE,
    CORPUS_ROLE_VERSION,
    DEFAULT_DIMENSION_QUOTAS,
    DEFAULT_PRIMARY_MODE_QUOTAS,
    MODE_POLICY_VERSION,
    QUESTION_DIMENSIONS,
    QUESTION_MODES,
    InterviewQuestionRecord,
    QuestionBankManifest,
    QuestionApprovalReceipt,
    QuestionCorpusSnapshot,
    QuestionDedupeRecord,
    QuestionDedupeSidecar,
    QuestionLocatorRecord,
    QuestionLocatorSidecar,
    QuestionModePolicy,
    QuestionReviewRecord,
    QuestionReviewSidecar,
    QuestionRightsRecord,
    QuestionRightsSidecar,
    QuestionSourceRegistry,
    QuestionSourceRegistryEntry,
)
from profile_agent.services.question_bank_service import (
    EMBEDDING_TEXT_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    build_question_content_hash,
    compute_question_bank_manifest_hash,
    compute_question_content_hash,
    load_question_bank,
)


DEFAULT_CORPUS_DIR = Path(
    "profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2"
)
MANIFEST_SCHEMA_VERSION = "2"
EMBEDDING_CONTRACT_VERSION = EMBEDDING_TEXT_VERSION


def compute_question_semantic_hash(record: InterviewQuestionRecord | Mapping[str, Any]) -> str:
    """Return the stable semantic fingerprint of normalized question text."""
    text = record.question_text if isinstance(record, InterviewQuestionRecord) else record["question_text"]
    normalized = re.sub(r"\s+", " ", str(text)).strip()
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
V2_MANIFEST_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "embedding_contract_version",
        "corpus_as_of",
        "bank_id",
        "role",
        "role_version",
        "manifest_version",
        "question_count",
        "question_ids",
        "dimension_quotas",
        "primary_mode_quotas",
        "mode_policy_version",
        "min_independent_urls",
        "max_questions_per_url",
        "signal_near_180_min_count",
        "signal_near_365_min_count",
        "signal_fallback_start",
        "signal_fallback_max_count",
        "dynamic_review_days",
        "evergreen_review_days",
        "evergreen_revalidation_days",
        "current_jd_validation_days",
        "active_count",
        "active_trust_levels",
        "generated_at",
        "reviewed_at",
        "published_at",
        "publication_status",
        "question_set_hash",
        "sidecar_set_hash",
    }
)
_LEGACY_V1_MANIFEST_SCHEMA_VALUES = frozenset(
    {1, "1", "v1", "question_bank.v1", "question_bank/v1"}
)
SOURCE_TYPES = frozenset(
    {
        "public_interview_experience",
        "official_technical_doc",
        "current_enterprise_jd",
    }
)
ACTIVE_TRUST_LEVELS = frozenset({"medium", "high"})
FALLBACK_START = date(2025, 1, 1)
NEAR_180_DAYS = 180
NEAR_365_DAYS = 365
MIN_NEAR_180 = 18
MIN_NEAR_365 = 27
MAX_FALLBACK = 3
MIN_INDEPENDENT_URLS = 12
MAX_QUESTIONS_PER_URL = 3

_QUESTION_ROOT_FIELDS = frozenset(
    {"schema_version", "version", "role", "role_version", "test_only", "questions", "records"}
)
_TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "ref",
        "ref_",
        "source",
        "campaign",
        "utm_campaign",
        "utm_content",
        "utm_medium",
        "utm_source",
        "utm_term",
    }
)
_UNACCEPTABLE_SOURCE_RE = re.compile(
    r"(?ix)"
    r"(?:^|[/?=&._-])(?:"
    r"search|searchresult|search-results|login|signin|sign-in|captcha|verify|"
    r"paywall|premium|subscribe|subscription|转载|转载页|登录|验证码|付费|"
    r"不可访问|无法访问"
    r")(?:$|[/?&#._=-])"
)
_UNACCEPTABLE_SOURCE_TEXT_RE = re.compile(
    r"(?ix)(?:"
    r"search\s*result|login\s*required|sign\s*in\s*required|captcha|"
    r"paywall|paid\s*content|premium\s*only|inaccessible|unreachable|"
    r"搜索结果|需登录|登录后|验证码|付费墙|付费内容|转载|repost(?:ed)?|不可访问|无法访问"
    r")"
)
_GAP_REASON_RE = re.compile(
    r"(?ix)(?:gap|coverage|quota|缺口|覆盖|配额|能力\s*覆盖|能力缺口)"
)
_FORBIDDEN_FALLBACK_REASON_RE = re.compile(
    r"(?ix)(?:"
    r"\b(?:not\s+applicable|not\s+enough|insufficient|without\s+(?:a\s+|the\s+)?(?:source|evidence)|"
    r"no\s+(?:data|source|evidence|usable\s+signal|qualifying\s+signal)|"
    r"lack\s+of|not\s+available|cannot|can't|unable|"
    r"unavailable|convenience|shortcut|temporary|easy)\b|"
    r"否定|不(?:足|可|能|需要|用)|无需|不用|无须|没有(?:数据|来源|证据|可用)|"
    r"没有(?:最新|近期|可用)?(?:面试)?(?:信号|来源|数据|证据)|"
    r"方便|便利|省事|凑数|临时|无法获取|不可得"
    r")"
)
@dataclass(frozen=True, slots=True)
class CorpusIssue:
    """One stable, non-mutating governance finding.

    The four fields are intentionally fixed so CLI and downstream reports can
    serialize findings without exposing arbitrary source or question values.
    """

    code: str
    path: str
    message: str
    severity: Literal["error", "warning"] = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True, slots=True)
class ApprovalReceipt:
    """Immutable approval fact handed to an external trusted verifier."""

    question_id: str
    actor_id: str
    approved_at: date | datetime
    corpus_hash: str | None = None
    sidecar_hash: str | None = None
    nonce: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class ApprovalVerifier(Protocol):
    """Trusted boundary for validating immutable approval receipts."""

    def verify(self, receipt: ApprovalReceipt) -> bool:
        """Return true only for a receipt issued by the trusted registry."""


class ExternalApprovalRegistry:
    """Independent, immutable allowlist for a version-level release receipt."""

    def __init__(self, payload: Mapping[str, Any], *, expected_question_set_hash: str | None = None,
                 expected_sidecar_set_hash: str | None = None,
                 expected_question_ids: Sequence[str] | None = None) -> None:
        if payload.get("scope") != "corpus_release" or payload.get("actor_id") != "workspace_owner":
            raise ValueError("approval registry scope or actor is invalid")
        receipts = payload.get("receipts")
        if not isinstance(receipts, list) or not receipts:
            raise ValueError("approval registry receipts are required")
        unsigned = {key: value for key, value in payload.items() if key != "registry_hash"}
        actual_hash = "sha256:" + hashlib.sha256(
            json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if payload.get("registry_hash") != actual_hash:
            raise ValueError("approval registry hash is invalid")
        question_ids = payload.get("question_ids")
        if not isinstance(question_ids, list) or len(question_ids) != len(receipts) or len(set(question_ids)) != len(question_ids):
            raise ValueError("approval registry question set is invalid")
        self._receipts = {}
        nonces = set()
        for item in receipts:
            receipt = QuestionApprovalReceipt.model_validate(item)
            if receipt.actor_id != "workspace_owner":
                raise ValueError("approval receipt actor is invalid")
            if expected_question_set_hash and receipt.corpus_hash != expected_question_set_hash:
                raise ValueError("approval receipt corpus hash is invalid")
            if expected_sidecar_set_hash and receipt.sidecar_hash != expected_sidecar_set_hash:
                raise ValueError("approval receipt sidecar hash is invalid")
            if receipt.question_id in self._receipts or not receipt.nonce or receipt.nonce in nonces:
                raise ValueError("approval receipt nonce or question is invalid")
            self._receipts[receipt.question_id] = receipt
            nonces.add(receipt.nonce)
        if set(question_ids) != set(self._receipts):
            raise ValueError("approval registry question set does not match receipts")
        if expected_question_ids is not None and set(question_ids) != set(expected_question_ids):
            raise ValueError("approval registry question set does not match corpus")

    def verify(self, receipt: ApprovalReceipt) -> bool:
        expected = self._receipts.get(receipt.question_id)
        return expected is not None and receipt == ApprovalReceipt(
            question_id=expected.question_id, actor_id=expected.actor_id,
            approved_at=expected.approved_at, corpus_hash=expected.corpus_hash,
            sidecar_hash=expected.sidecar_hash, nonce=expected.nonce)


def load_approval_registry(path: Path, *, expected_question_set_hash: str | None = None,
                           expected_sidecar_set_hash: str | None = None,
                           expected_question_ids: Sequence[str] | None = None) -> ExternalApprovalRegistry:
    payload = _read_object(Path(path))
    return ExternalApprovalRegistry(payload, expected_question_set_hash=expected_question_set_hash,
                                    expected_sidecar_set_hash=expected_sidecar_set_hash,
                                    expected_question_ids=expected_question_ids)


def _ensure_date(value: Any, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{field_name} must be a date")
    return value


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"question corpus file could not be read: {path.name}") from exc


def _read_object(path: Path) -> Mapping[str, Any]:
    payload = _read_json(path)
    if not isinstance(payload, Mapping):
        raise ValueError(f"question corpus sidecar root must be an object: {path.name}")
    return payload


def _validate_manifest_raw(root: Mapping[str, Any]) -> None:
    """Reject omitted v2 contract fields before model defaults can apply."""

    schema_version = root.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, (int, str)):
        raise ValueError("question corpus manifest schema_version is required")
    if schema_version in _LEGACY_V1_MANIFEST_SCHEMA_VALUES:
        raise ValueError(
            "legacy v1 manifest requires an explicit compatibility branch"
        )
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise ValueError("question corpus manifest schema_version is invalid")
    missing = sorted(
        field_name
        for field_name in V2_MANIFEST_REQUIRED_FIELDS
        if field_name not in root
    )
    if missing:
        raise ValueError(
            "question corpus manifest required fields are missing: "
            + ", ".join(missing)
        )


def _find_file(root: Path, *names: str) -> Path:
    for name in names:
        candidate = root / name
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    raise ValueError(f"question corpus sidecar is missing: {names[0]}")


def _validate_question_root(root: Mapping[str, Any]) -> None:
    unknown = set(root).difference(_QUESTION_ROOT_FIELDS)
    if unknown:
        raise ValueError("questions.json contains unknown fields")
    schema_version = root.get("schema_version", root.get("version"))
    if (
        isinstance(schema_version, bool)
        or type(schema_version) not in {int, str}
        or schema_version not in SUPPORTED_SCHEMA_VERSIONS
        or str(schema_version) != MANIFEST_SCHEMA_VERSION
    ):
        raise ValueError("questions.json schema_version is invalid")
    if root.get("role") != CORPUS_ROLE:
        raise ValueError("questions.json role is invalid")
    if root.get("role_version") != CORPUS_ROLE_VERSION:
        raise ValueError("questions.json role_version is invalid")
    if root.get("test_only", False) is not False:
        raise ValueError("question corpus cannot be test-only")
    questions = root.get("questions")
    if questions is None:
        questions = root.get("records")
    if not isinstance(questions, list):
        raise ValueError("questions.json questions must be a list")


def load_question_corpus_snapshot(
    corpus_dir: str | Path,
    as_of: date,
) -> QuestionCorpusSnapshot:
    """Load the complete canonical corpus and all strict governance sidecars.

    ``as_of`` is validated at the boundary to make callers explicit about the
    audit clock.  The date is used by :func:`validate_question_corpus`; it is
    not written back to the snapshot and therefore cannot mutate the source.
    """

    _ensure_date(as_of, "as_of")
    root = Path(corpus_dir)
    if not root.exists() or not root.is_dir():
        raise ValueError("question corpus directory is unavailable")

    questions_path = _find_file(
        root,
        "questions.json",
        "QuestionBank.json",
        "question_bank.json",
    )
    questions_root = _read_object(questions_path)
    _validate_question_root(questions_root)

    try:
        records = load_question_bank(
            questions_path,
            expected_role=CORPUS_ROLE,
            expected_role_version=CORPUS_ROLE_VERSION,
            allow_test_only=False,
        )
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError("question corpus questions are invalid") from exc

    try:
        manifest_root = _read_object(
            _find_file(
                root,
                "QuestionBankManifest.json",
                "question_bank_manifest.json",
                "manifest.json",
            )
        )
        _validate_manifest_raw(manifest_root)
        manifest = QuestionBankManifest.model_validate(manifest_root)
        source_registry = QuestionSourceRegistry.model_validate(
            _read_object(_find_file(root, "QuestionSourceRegistry.json", "question_source_registry.json", "sources.json"))
        )
        review = _model_validate_sidecar(
            _find_file(
                root,
                "review.json",
                "QuestionReview.json",
                "QuestionReviewSidecar.json",
                "question_review.json",
            ),
            QuestionReviewRecord,
        )
        dedupe = _model_validate_sidecar(
            _find_file(
                root,
                "dedupe.json",
                "QuestionDedupe.json",
                "QuestionDedupeSidecar.json",
                "question_dedupe.json",
            ),
            QuestionDedupeRecord,
        )
        rights = _model_validate_sidecar(
            _find_file(
                root,
                "rights.json",
                "QuestionRights.json",
                "QuestionRightsSidecar.json",
                "question_rights.json",
            ),
            QuestionRightsRecord,
        )
        locator = _model_validate_sidecar(
            _find_file(
                root,
                "locator.json",
                "QuestionLocator.json",
                "QuestionLocatorSidecar.json",
                "question_locator.json",
            ),
            QuestionLocatorRecord,
        )
        return QuestionCorpusSnapshot(
            records=records,
            manifest=manifest,
            source_registry=source_registry,
            review=review,
            dedupe=dedupe,
            rights=rights,
            locator=locator,
        )
    except (ValidationError, TypeError, ValueError, OSError) as exc:
        raise ValueError("question corpus sidecars are invalid") from exc


def _model_validate_sidecar(
    path: Path,
    item_type: type[Any],
) -> Any:
    payload = _read_object(path)
    # Let the sidecar model enforce extra="forbid".  The aliases are already
    # part of the schema models; this helper only emits a consistent error.
    sidecar_type = {
        QuestionReviewRecord: QuestionReviewSidecar,
        QuestionDedupeRecord: QuestionDedupeSidecar,
        QuestionRightsRecord: QuestionRightsSidecar,
        QuestionLocatorRecord: QuestionLocatorSidecar,
    }.get(item_type)
    if sidecar_type is None:
        raise TypeError("unsupported question corpus sidecar")
    try:
        return sidecar_type.model_validate(payload)
    except (KeyError, ValidationError, TypeError, ValueError) as exc:
        raise ValueError(f"question corpus sidecar is invalid: {path.name}") from exc


def canonicalize_source_url(value: str) -> str:
    """Normalize an HTTP(S) URL for identity and association checks.

    Fragments and common tracking parameters do not create a new source.  The
    function is pure and does not resolve or fetch the URL.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("canonical URL must be a non-blank string")
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("canonical URL must be an absolute http(s) URL")
    if parsed.fragment:
        raise ValueError("canonical URL must not contain a fragment")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("canonical URL host is invalid")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("canonical URL port is invalid") from exc
    host = hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    if port is not None and not default_port:
        host = f"{host}:{port}"
    userinfo = ""
    if parsed.username is not None or parsed.password is not None:
        # Userinfo is not a valid public evidence identity.  Reject rather
        # than accidentally serializing credentials into an artifact.
        raise ValueError("canonical URL must not contain user information")
    query_items = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_QUERY_KEYS
        and not key.casefold().startswith("utm_")
    ]
    query_items.sort()
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), host, path, urlencode(query_items), ""))


def _json_ready(value: Any) -> Any:
    """Convert model/sidecar values to deterministic JSON primitives."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump(mode="json", warnings=False)
        except Exception:
            return None
    if isinstance(value, Mapping):
        return {
            str(key): _json_ready(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _sha256_payload(value: Any) -> str:
    encoded = json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def compute_question_set_hash(records: Sequence[Any]) -> str:
    """Compute the release hash for canonical question IDs and identities."""

    entries: list[dict[str, str]] = []
    for record in records:
        question_id = _record_id(record)
        if question_id is None:
            raise ValueError("question record has no question_id")
        entries.append(
            {
                "question_id": question_id,
                "content_hash": compute_question_content_hash(record),
            }
        )
    entries.sort(key=lambda item: item["question_id"])
    return _sha256_payload({"version": "question-set-v1", "questions": entries})


def _sidecar_entries(value: Any, *names: str) -> list[Any]:
    entries = _entries(value, *names)
    return sorted(
        entries,
        key=lambda item: (
            _record_id(item) or "",
            _source_id(item) or "",
            str(_value(item, "source_id", "")),
        ),
    )


def compute_sidecar_set_hash(snapshot: Any) -> str:
    """Compute the release hash for source and governance sidecars."""

    _, _, source_values, review_values, dedupe_values, rights_values, locator_values = _snapshot_parts(snapshot)
    review_entries = []
    for entry in _sidecar_entries({"records": review_values}, "records", "reviews"):
        mapping = _safe_mapping(entry)
        if mapping is None:
            review_entries.append(entry)
        else:
            review_entries.append({key: value for key, value in mapping.items()
                                   if key not in {"approval_receipt", "human_approval_receipt"}})
    payload = {
        "version": "sidecar-set-v1",
        "source_registry": _sidecar_entries(
            {"entries": source_values}, "entries", "sources"
        ),
        "review": review_entries,
        "dedupe": _sidecar_entries(
            {"records": dedupe_values}, "records", "dedupe_records"
        ),
        "rights": _sidecar_entries(
            {"records": rights_values}, "records", "rights_records"
        ),
        "locator": _sidecar_entries(
            {"records": locator_values}, "records", "locator_records"
        ),
    }
    return _sha256_payload(payload)


def _safe_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(mode="python", warnings=False)
        except Exception:
            return None
        return dumped if isinstance(dumped, Mapping) else None
    return None


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _entries(value: Any, *names: str) -> list[Any]:
    if value is None:
        return []
    for name in names:
        candidate = _value(value, name, None)
        if candidate is not None:
            if isinstance(candidate, (str, bytes, bytearray)):
                return []
            try:
                return list(candidate)
            except TypeError:
                return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _record_id(record: Any) -> str | None:
    value = _value(record, "question_id")
    return value if isinstance(value, str) and value.strip() else None


def _safe_counter(values: Iterable[Any]) -> Counter[Any]:
    counts: Counter[Any] = Counter()
    for value in values:
        try:
            hash(value)
        except TypeError:
            value = "<invalid>"
        counts[value] += 1
    return counts


def _allowed(value: Any, choices: set[str] | frozenset[str]) -> bool:
    return isinstance(value, str) and value in choices


def _source_id(entry: Any) -> str | None:
    value = _value(entry, "source_id", _value(entry, "id"))
    return value if isinstance(value, str) and value.strip() else None


def _append_issue(
    issues: list[CorpusIssue],
    seen: set[tuple[str, str, str, str]],
    code: str,
    path: str,
    message: str,
    severity: Literal["error", "warning"] = "error",
) -> None:
    key = (code, path, message, severity)
    if key not in seen:
        seen.add(key)
        issues.append(CorpusIssue(code, path, message, severity))


def _role_pack_dimensions(role_pack: Any) -> set[str]:
    values = _value(role_pack, "dimensions")
    if values is None:
        values = _value(role_pack, "dimension_ids")
    if values is None and isinstance(role_pack, (list, tuple)):
        values = role_pack
    if values is None:
        return set()
    result: set[str] = set()
    if isinstance(values, Mapping):
        # Accept both ``{"dimensions": [...]}`` and a compact mapping whose
        # keys are the authoritative dimension IDs.
        if set(values).intersection(QUESTION_DIMENSIONS):
            values = list(values)
        else:
            values = list(values.values())
    try:
        iterator = list(values)
    except TypeError:
        return set()
    for item in iterator:
        if isinstance(item, str):
            result.add(item)
        else:
            value = _value(item, "id", _value(item, "dimension_id"))
            if isinstance(value, str) and value.strip():
                result.add(value)
    return result


def _snapshot_parts(snapshot: Any) -> tuple[list[Any], Any, list[Any], list[Any], list[Any], list[Any], list[Any]]:
    return (
        _entries(snapshot, "records"),
        _value(snapshot, "manifest"),
        _entries(_value(snapshot, "source_registry"), "entries", "sources"),
        _entries(_value(snapshot, "review"), "records", "reviews"),
        _entries(_value(snapshot, "dedupe"), "records", "dedupe_records"),
        _entries(_value(snapshot, "rights"), "records", "rights_records"),
        _entries(_value(snapshot, "locator"), "records", "locator_records"),
    )


def _record_source_ids(record: Any) -> list[str]:
    values = _value(record, "source_ids", None)
    if values is None:
        values = []
    if isinstance(values, (str, bytes, bytearray)):
        values = []
    try:
        result = [item for item in values if isinstance(item, str) and item.strip()]
    except TypeError:
        result = []
    primary = _value(record, "source_id")
    if isinstance(primary, str) and primary.strip() and primary not in result:
        result.insert(0, primary)
    return result


def _date(value: Any) -> date | None:
    return value if isinstance(value, date) and not isinstance(value, datetime) else None


def _source_url(entry: Any) -> str | None:
    value = _value(entry, "canonical_url", _value(entry, "source_url", _value(entry, "url")))
    return value if isinstance(value, str) and value.strip() else None


def _source_type(entry: Any) -> str | None:
    value = _value(entry, "source_type", _value(entry, "source_class"))
    return value if isinstance(value, str) else None


def _source_trust(entry: Any) -> Any:
    return _value(entry, "trust", _value(entry, "trust_level"))


def _is_active_record(record: Any) -> bool:
    return _value(record, "status") == "active"


def _primary_mode(record: Any) -> Any:
    value = _value(record, "primary_mode")
    return value if value is not None else _value(record, "question_mode")


def _is_release(manifest: Any, records: Sequence[Any]) -> bool:
    publication_status = _value(manifest, "publication_status", _value(manifest, "release_status"))
    return (
        isinstance(publication_status, str)
        and publication_status in {"ready", "published"}
    ) or any(_is_active_record(record) for record in records)


def _is_unacceptable_source(entry: Any) -> bool:
    values = (
        _source_url(entry) or "",
        str(_value(entry, "title", "")),
        str(_value(entry, "publisher", "")),
        str(_value(entry, "notes", "")),
        str(_value(entry, "human_summary", "")),
    )
    return bool(
        _UNACCEPTABLE_SOURCE_RE.search(values[0])
        or any(_UNACCEPTABLE_SOURCE_TEXT_RE.search(value) for value in values[1:])
    )


def _source_freshness(entry: Any, as_of: date) -> tuple[bool, str]:
    source_type = _source_type(entry)
    verified_at = _date(_value(entry, "verified_at"))
    accessed_at = _date(_value(entry, "accessed_at", _value(entry, "retrieved_at")))
    if verified_at is None or accessed_at is None:
        return False, "source verification dates are missing"
    if accessed_at > as_of or verified_at > as_of:
        return False, "source verification date is after audit date"
    if source_type in {"official_technical_doc", "current_enterprise_jd"}:
        if (as_of - accessed_at).days > NEAR_180_DAYS or (as_of - verified_at).days > NEAR_180_DAYS:
            return False, "technical or JD source is outside the 180-day verification window"
    return True, ""


def _source_published_date(entry: Any) -> date | None:
    published_at = _date(_value(entry, "published_at"))
    if published_at is not None:
        return published_at
    return _date(_value(entry, "accessed_at", _value(entry, "retrieved_at")))


def _approval_receipt_from_review(
    review: Any,
    question_id: str,
) -> ApprovalReceipt | None:
    raw_receipt = _value(
        review,
        "approval_receipt",
        _value(review, "human_approval_receipt"),
    )
    try:
        if isinstance(raw_receipt, ApprovalReceipt):
            receipt = raw_receipt
        elif isinstance(raw_receipt, QuestionApprovalReceipt):
            receipt = ApprovalReceipt(
                question_id=raw_receipt.question_id,
                actor_id=raw_receipt.actor_id,
                approved_at=raw_receipt.approved_at,
                corpus_hash=raw_receipt.corpus_hash,
                sidecar_hash=raw_receipt.sidecar_hash,
                nonce=raw_receipt.nonce,
            )
        elif isinstance(raw_receipt, Mapping):
            parsed = QuestionApprovalReceipt.model_validate(raw_receipt)
            receipt = ApprovalReceipt(
                question_id=parsed.question_id,
                actor_id=parsed.actor_id,
                approved_at=parsed.approved_at,
                corpus_hash=parsed.corpus_hash,
                sidecar_hash=parsed.sidecar_hash,
                nonce=parsed.nonce,
            )
        else:
            return None
    except (TypeError, ValueError, ValidationError):
        return None
    if receipt.question_id != question_id:
        return None
    if not isinstance(receipt.actor_id, str) or not receipt.actor_id.strip():
        return None
    if not isinstance(receipt.approved_at, (date, datetime)):
        return None
    if not any((receipt.corpus_hash, receipt.sidecar_hash, receipt.nonce)):
        return None
    return receipt


def _call_approval_verifier(
    approval_verifier: ApprovalVerifier | Callable[[ApprovalReceipt], bool],
    receipt: ApprovalReceipt,
) -> bool:
    verifier = getattr(approval_verifier, "verify", None)
    if not callable(verifier) and callable(approval_verifier):
        verifier = approval_verifier
    if not callable(verifier):
        return False
    try:
        return verifier(receipt) is True
    except Exception:
        return False


def _has_human_approval(
    review: Any,
    question_id: str,
    as_of: date,
    approval_verifier: ApprovalVerifier | Callable[[ApprovalReceipt], bool] | None,
    expected_question_set_hash: str | None,
    expected_sidecar_set_hash: str | None,
) -> bool:
    """Accept only a receipt verified outside the corpus data boundary."""

    if approval_verifier is None:
        return False
    receipt = _approval_receipt_from_review(review, question_id)
    if receipt is None:
        return False
    approval_date = (
        receipt.approved_at.date()
        if isinstance(receipt.approved_at, datetime)
        else receipt.approved_at
    )
    if approval_date > as_of:
        return False
    if receipt.corpus_hash is not None and receipt.corpus_hash != expected_question_set_hash:
        return False
    if receipt.sidecar_hash is not None and receipt.sidecar_hash != expected_sidecar_set_hash:
        return False
    return _call_approval_verifier(approval_verifier, receipt)


def _non_blank_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _review_lookup(reviews: Sequence[Any]) -> dict[str, Any]:
    return {
        question_id: review
        for review in reviews
        if (question_id := _record_id(review)) is not None
    }


def _source_lookup(sources: Sequence[Any]) -> dict[str, Any]:
    return {
        source_id: source
        for source in sources
        if (source_id := _source_id(source)) is not None
    }


def _association_map(
    records: Sequence[Any],
    reviews: Sequence[Any],
    sources: Mapping[str, Any],
) -> dict[str, set[str]]:
    association: dict[str, set[str]] = defaultdict(set)
    for source_id, source in sources.items():
        values = _value(source, "question_ids", ()) or ()
        if isinstance(values, (str, bytes, bytearray)):
            continue
        try:
            for question_id in values:
                if isinstance(question_id, str) and question_id.strip():
                    association[source_id].add(question_id)
        except TypeError:
            continue
    for record in records:
        question_id = _record_id(record)
        if question_id is None:
            continue
        for source_id in _record_source_ids(record):
            if source_id in sources:
                association[source_id].add(question_id)
    for review in reviews:
        question_id = _record_id(review)
        if question_id is None:
            continue
        for key in ("signal_source_ids", "cross_validation_source_ids"):
            values = _value(review, key, ())
            if isinstance(values, (str, bytes, bytearray)):
                continue
            try:
                source_ids = list(values)
            except TypeError:
                continue
            for source_id in source_ids:
                if source_id in sources:
                    association[source_id].add(question_id)
    return association


def validate_question_corpus(
    snapshot: QuestionCorpusSnapshot,
    role_pack: Any,
    as_of: date,
    *,
    approval_verifier: ApprovalVerifier
    | Callable[[ApprovalReceipt], bool]
    | None = None,
) -> Sequence[CorpusIssue]:
    """Validate structure, evidence, lifecycle and quotas without side effects.

    All checks are independent where possible: malformed draft data produces a
    useful collection of findings instead of failing at the first field.  The
    returned sequence is sorted by stable path/code/message ordering.
    """

    _ensure_date(as_of, "as_of")
    records, manifest, source_values, review_values, dedupe_values, rights_values, locator_values = _snapshot_parts(snapshot)
    issues: list[CorpusIssue] = []
    seen: set[tuple[str, str, str, str]] = set()

    if not isinstance(manifest, (QuestionBankManifest, Mapping)) and manifest is None:
        _append_issue(issues, seen, "manifest_missing", "QuestionBankManifest.json", "manifest is missing")

    # Snapshot-level record identity and strict primary keys.
    if len(records) != 30:
        _append_issue(issues, seen, "question_count", "questions.json", "question count must be exactly 30")
    record_ids = [_record_id(record) for record in records]
    if any(question_id is None for question_id in record_ids):
        _append_issue(issues, seen, "question_id", "questions.json", "every record must have a non-blank question_id")
    id_counts = Counter(question_id for question_id in record_ids if question_id is not None)
    if any(count > 1 for count in id_counts.values()):
        _append_issue(issues, seen, "duplicate_question_id", "questions.json", "question_id values must be unique")
    record_id_set = {question_id for question_id in record_ids if question_id is not None}

    manifest_ids = _value(manifest, "question_ids", ())
    if isinstance(manifest_ids, (str, bytes, bytearray)):
        manifest_ids = []
    try:
        manifest_id_list = list(manifest_ids)
    except TypeError:
        manifest_id_list = []
    manifest_id_strings = [
        value for value in manifest_id_list if isinstance(value, str) and value.strip()
    ]
    if (
        len(manifest_id_list) != 30
        or len(manifest_id_strings) != len(manifest_id_list)
        or len(set(manifest_id_strings)) != len(manifest_id_strings)
    ):
        _append_issue(issues, seen, "manifest_question_ids", "QuestionBankManifest.json.question_ids", "manifest must list 30 unique question IDs")
    elif set(manifest_id_strings) != record_id_set:
        _append_issue(issues, seen, "question_id_fk", "QuestionBankManifest.json.question_ids", "manifest question IDs must match records")

    # Role/version and manifest identity.
    if _value(manifest, "schema_version") != MANIFEST_SCHEMA_VERSION:
        _append_issue(
            issues,
            seen,
            "manifest_schema_version",
            "QuestionBankManifest.json.schema_version",
            "manifest schema_version must be exactly 2",
        )
    if _value(manifest, "role") != CORPUS_ROLE:
        _append_issue(issues, seen, "role_mismatch", "QuestionBankManifest.json.role", "role must match the corpus role")
    if _value(manifest, "role_version") != CORPUS_ROLE_VERSION:
        _append_issue(issues, seen, "role_version_mismatch", "QuestionBankManifest.json.role_version", "role_version must match the corpus version")
    if _value(manifest, "mode_policy_version") != MODE_POLICY_VERSION:
        _append_issue(issues, seen, "mode_policy_version", "QuestionBankManifest.json.mode_policy_version", "manifest mode policy version must match the frozen policy")
    if _value(manifest, "corpus_as_of") != CORPUS_AS_OF:
        _append_issue(issues, seen, "corpus_as_of_mismatch", "QuestionBankManifest.json.corpus_as_of", "manifest corpus_as_of must match the frozen date")
    if _value(manifest, "question_count") != 30:
        _append_issue(issues, seen, "manifest_question_count", "QuestionBankManifest.json.question_count", "manifest question_count must be exactly 30")
    if _value(manifest, "embedding_contract_version") != EMBEDDING_CONTRACT_VERSION:
        _append_issue(
            issues,
            seen,
            "embedding_contract_version",
            "QuestionBankManifest.json.embedding_contract_version",
            "manifest must declare the fixed six-section embedding contract",
        )
    try:
        active_trust_levels = set(_value(manifest, "active_trust_levels", ()) or ())
    except TypeError:
        active_trust_levels = set()
    if active_trust_levels != ACTIVE_TRUST_LEVELS:
        _append_issue(issues, seen, "active_trust_levels", "QuestionBankManifest.json.active_trust_levels", "active trust levels must be medium and high")

    manifest_contract = (
        ("min_independent_urls", MIN_INDEPENDENT_URLS),
        ("max_questions_per_url", MAX_QUESTIONS_PER_URL),
        ("signal_near_180_min_count", MIN_NEAR_180),
        ("signal_near_365_min_count", MIN_NEAR_365),
        ("signal_fallback_start", FALLBACK_START),
        ("signal_fallback_max_count", MAX_FALLBACK),
        ("dynamic_review_days", 180),
        ("evergreen_review_days", 365),
        ("evergreen_revalidation_days", 180),
        ("current_jd_validation_days", 180),
    )
    for field_name, expected_value in manifest_contract:
        if _value(manifest, field_name) != expected_value:
            _append_issue(
                issues,
                seen,
                f"manifest_{field_name}",
                f"QuestionBankManifest.json.{field_name}",
                f"manifest {field_name} does not match the frozen governance contract",
            )

    publication_status = _value(manifest, "publication_status", _value(manifest, "release_status"))
    generated_at = _date(_value(manifest, "generated_at", _value(manifest, "created_at")))
    reviewed_at = _date(_value(manifest, "reviewed_at", _value(manifest, "approved_at")))
    published_at = _date(_value(manifest, "published_at"))
    for field_name, field_value in (
        ("generated_at", generated_at),
        ("reviewed_at", reviewed_at),
        ("published_at", published_at),
    ):
        if field_value is not None and field_value > as_of:
            _append_issue(
                issues,
                seen,
                "manifest_dates",
                f"QuestionBankManifest.json.{field_name}",
                "manifest dates must not be after the audit date",
            )
    if generated_at is None:
        _append_issue(
            issues,
            seen,
            "manifest_dates",
            "QuestionBankManifest.json.generated_at",
            "every manifest requires generated_at",
        )
    if generated_at is not None and reviewed_at is not None and generated_at > reviewed_at:
        _append_issue(
            issues,
            seen,
            "manifest_dates",
            "QuestionBankManifest.json.reviewed_at",
            "manifest generated_at must not be after reviewed_at",
        )
    if reviewed_at is not None and published_at is not None and reviewed_at > published_at:
        _append_issue(
            issues,
            seen,
            "manifest_dates",
            "QuestionBankManifest.json.published_at",
            "manifest reviewed_at must not be after published_at",
        )
    if publication_status in {"ready", "published"}:
        if generated_at is None or reviewed_at is None:
            _append_issue(
                issues,
                seen,
                "manifest_dates",
                "QuestionBankManifest.json",
                "ready or published manifests require generated_at and reviewed_at",
            )
    if publication_status == "published" and published_at is None:
        _append_issue(
            issues,
            seen,
            "manifest_dates",
            "QuestionBankManifest.json.published_at",
            "published manifests require published_at",
        )
    if publication_status in {"draft", "ready"} and published_at is not None:
        _append_issue(
            issues,
            seen,
            "manifest_dates",
            "QuestionBankManifest.json.published_at",
            "draft or ready manifests must not declare published_at",
        )

    role_pack_dimensions = _role_pack_dimensions(role_pack)
    if not role_pack_dimensions:
        _append_issue(issues, seen, "role_pack_missing", "role_pack", "role pack must expose role dimensions")
    elif role_pack_dimensions != set(QUESTION_DIMENSIONS):
        _append_issue(issues, seen, "role_pack_dimensions", "role_pack.dimensions", "role pack dimensions must match the six corpus dimensions")
    role_pack_version = _value(role_pack, "version", _value(role_pack, "role_version"))
    if role_pack_version is not None and role_pack_version != CORPUS_ROLE_VERSION:
        _append_issue(issues, seen, "role_pack_version", "role_pack.version", "role pack version must match the corpus version")

    # Recompute quotas instead of trusting manifest declarations.
    dimension_counts = _safe_counter(_value(record, "dimension_id") for record in records)
    mode_counts = _safe_counter(_primary_mode(record) for record in records)
    if dict(dimension_counts) != {key: dimension_counts[key] for key in DEFAULT_DIMENSION_QUOTAS} or any(
        dimension_counts.get(key, 0) != expected for key, expected in DEFAULT_DIMENSION_QUOTAS.items()
    ):
        _append_issue(issues, seen, "dimension_quota", "questions.json", "dimension quotas must be exactly 6/5/6/4/6/3")
    if any(mode_counts.get(key, 0) != expected for key, expected in DEFAULT_PRIMARY_MODE_QUOTAS.items()) or set(mode_counts).difference(DEFAULT_PRIMARY_MODE_QUOTAS):
        _append_issue(issues, seen, "primary_mode_quota", "questions.json", "primary mode quotas must be exactly 4/5/8/4/3/6")
    manifest_dimensions = _value(manifest, "dimension_quotas", _value(manifest, "dimension_counts", {})) or {}
    manifest_modes = _value(manifest, "primary_mode_quotas", _value(manifest, "mode_quotas", {})) or {}
    manifest_dimensions = dict(manifest_dimensions) if isinstance(manifest_dimensions, Mapping) else {}
    manifest_modes = dict(manifest_modes) if isinstance(manifest_modes, Mapping) else {}
    if manifest_dimensions != DEFAULT_DIMENSION_QUOTAS:
        _append_issue(issues, seen, "manifest_dimension_quota", "QuestionBankManifest.json.dimension_quotas", "manifest dimension quotas do not match the frozen quotas")
    if dict(manifest_modes) != DEFAULT_PRIMARY_MODE_QUOTAS:
        _append_issue(issues, seen, "manifest_primary_mode_quota", "QuestionBankManifest.json.primary_mode_quotas", "manifest primary mode quotas do not match the frozen quotas")

    policy = QuestionModePolicy.default()
    canonical_hashes: list[str] = []
    for index, record in enumerate(records):
        path = f"questions.json.questions[{index}]"
        if _value(record, "role") != CORPUS_ROLE:
            _append_issue(issues, seen, "role_mismatch", f"{path}.role", "record role does not match the corpus role")
        if _value(record, "role_version") != CORPUS_ROLE_VERSION:
            _append_issue(issues, seen, "role_version_mismatch", f"{path}.role_version", "record role_version does not match the corpus version")
        record_source_type = _value(record, "source_type")
        if not isinstance(record_source_type, str) or record_source_type not in SOURCE_TYPES:
            _append_issue(issues, seen, "source_type", f"{path}.source_type", "record source type is not in the allowed taxonomy")
        dimension_id = _value(record, "dimension_id")
        if dimension_id not in QUESTION_DIMENSIONS or (role_pack_dimensions and dimension_id not in role_pack_dimensions):
            _append_issue(issues, seen, "dimension_invalid", f"{path}.dimension_id", "record dimension is not in the role pack")
        question_mode = _value(record, "question_mode")
        primary_mode = _primary_mode(record)
        if question_mode is not None and primary_mode is not None and question_mode != primary_mode:
            _append_issue(issues, seen, "mode_invalid", f"{path}.primary_mode", "question_mode and primary_mode must agree")
        compatible_modes = _value(record, "compatible_modes", ()) or ()
        try:
            policy.validate_mode_assignment(dimension_id, primary_mode, compatible_modes)
        except (TypeError, ValueError):
            _append_issue(issues, seen, "mode_invalid", f"{path}.primary_mode", "record mode assignment violates the frozen policy")

        # Content identity is checked against both the current full v2 hash and
        # the explicit Task1-3 compatibility hash.  No values are echoed.
        stored_hash = _value(record, "content_hash")
        try:
            expected_hash = compute_question_content_hash(record)
            compatibility_hash = build_question_content_hash(record)
        except (AttributeError, TypeError, ValueError):
            expected_hash = compatibility_hash = None
        if not isinstance(stored_hash, str) or stored_hash not in {expected_hash, compatibility_hash}:
            _append_issue(issues, seen, "content_hash_mismatch", f"{path}.content_hash", "content_hash does not match the canonical record")
        if isinstance(expected_hash, str):
            canonical_hashes.append(expected_hash)

        source_ids = _record_source_ids(record)
        if not source_ids:
            _append_issue(issues, seen, "source_fk", f"{path}.source_ids", "record must reference at least one source")
        if _value(record, "source_id") not in source_ids:
            _append_issue(issues, seen, "source_fk", f"{path}.source_id", "legacy source_id must be included in source_ids")
        status = _value(record, "status")
        trust = _value(record, "trust_level", _value(record, "trust"))
        if not _allowed(status, {"active", "needs_review", "retired"}):
            _append_issue(issues, seen, "state_invalid", f"{path}.status", "record lifecycle state is invalid")
        if not _allowed(trust, {"low", "medium", "high"}):
            _append_issue(issues, seen, "trust_invalid", f"{path}.trust_level", "record trust level is invalid")
        valid_until = _date(_value(record, "valid_until"))
        verified_at = _date(_value(record, "verified_at"))
        if valid_until is None or verified_at is None:
            _append_issue(issues, seen, "lifecycle_date", f"{path}.valid_until", "record lifecycle dates are required")
        elif _is_active_record(record) and valid_until < as_of:
            _append_issue(issues, seen, "active_expired", f"{path}.valid_until", "expired records cannot remain active")
        published_at = _date(_value(record, "published_at"))
        if published_at is not None and published_at > as_of:
            _append_issue(issues, seen, "future_record_date", f"{path}.published_at", "record published_at cannot be after the audit date")
        if verified_at is not None and verified_at > as_of:
            _append_issue(issues, seen, "future_record_date", f"{path}.verified_at", "record verified_at cannot be after the audit date")
        if _is_active_record(record) and trust not in ACTIVE_TRUST_LEVELS:
            _append_issue(issues, seen, "active_low_trust", f"{path}.trust_level", "active records require medium or high trust")

    # Sidecar primary keys and question/source foreign keys.
    if len(canonical_hashes) != len(set(canonical_hashes)):
        _append_issue(issues, seen, "duplicate_content_hash", "questions.json", "canonical content hashes must be unique")
    source_lookup = _source_lookup(source_values)
    referenced_source_ids = {
        source_id
        for record in records
        for source_id in _record_source_ids(record)
    }
    review_lookup = _review_lookup(review_values)
    dedupe_lookup = _review_lookup(dedupe_values)
    rights_keys = [(_record_id(item), _source_id(item)) for item in rights_values]
    locator_keys = [(_record_id(item), _source_id(item)) for item in locator_values]
    relation_keys = {
        (question_id, source_id)
        for record in records
        if (question_id := _record_id(record)) is not None
        for source_id in _record_source_ids(record)
    }
    if len([key for key in rights_keys if key[0] is not None and key[1] is not None]) != len(set(key for key in rights_keys if key[0] is not None and key[1] is not None)):
        _append_issue(issues, seen, "duplicate_rights_key", "rights.json", "rights question/source keys must be unique")
    if len([key for key in locator_keys if key[0] is not None and key[1] is not None]) != len(set(key for key in locator_keys if key[0] is not None and key[1] is not None)):
        _append_issue(issues, seen, "duplicate_locator_key", "locator.json", "locator question/source keys must be unique")
    for index, (question_id, source_id) in enumerate(rights_keys):
        if (question_id, source_id) not in relation_keys:
            _append_issue(
                issues,
                seen,
                "rights_fk",
                f"rights.json.records[{index}]",
                "rights relation must reference a corpus question/source pair",
            )
    for index, (question_id, source_id) in enumerate(locator_keys):
        if (question_id, source_id) not in relation_keys:
            _append_issue(
                issues,
                seen,
                "locator_fk",
                f"locator.json.records[{index}]",
                "locator relation must reference a corpus question/source pair",
            )
    source_id_counts = _safe_counter(_source_id(item) for item in source_values)
    if any(count > 1 for count in source_id_counts.values() if count is not None):
        _append_issue(issues, seen, "duplicate_source_id", "QuestionSourceRegistry.json", "source_id values must be unique")

    for name, values, lookup in (("review", review_values, review_lookup), ("dedupe", dedupe_values, dedupe_lookup)):
        ids = [_record_id(item) for item in values]
        if set(ids).difference(record_id_set) or record_id_set.difference(set(ids)) or len(ids) != len(record_id_set):
            _append_issue(issues, seen, f"{name}_fk", f"{name}.json", f"{name} sidecar must contain exactly one record per question")

    for index, source in enumerate(source_values):
        source_path = f"QuestionSourceRegistry.json.entries[{index}]"
        source_id = _source_id(source)
        source_type = _source_type(source)
        if source_id is None or source_id not in source_lookup:
            _append_issue(issues, seen, "source_id", f"{source_path}.source_id", "source_id must be non-blank and unique")
        if source_type not in SOURCE_TYPES:
            _append_issue(issues, seen, "source_type", f"{source_path}.source_type", "source type is not in the allowed taxonomy")
        url = _source_url(source)
        try:
            normalized_url = canonicalize_source_url(url or "")
        except (TypeError, ValueError):
            normalized_url = None
            _append_issue(issues, seen, "canonical_url", f"{source_path}.canonical_url", "source canonical URL is invalid")
        if _is_unacceptable_source(source):
            _append_issue(issues, seen, "source_access", f"{source_path}.canonical_url", "search, login, captcha, paywall, or inaccessible repost sources are not accepted")
        if _value(source, "access_status") == "inaccessible":
            _append_issue(issues, seen, "source_access", f"{source_path}.access_status", "inaccessible sources are not accepted")
        source_question_ids = _value(source, "question_ids", ()) or ()
        try:
            source_question_ids = list(source_question_ids)
        except TypeError:
            source_question_ids = []
        source_question_id_strings = [
            value for value in source_question_ids if isinstance(value, str) and value.strip()
        ]
        if (
            len(source_question_id_strings) != len(source_question_ids)
            or len(source_question_id_strings) != len(set(source_question_id_strings))
            or set(source_question_id_strings).difference(record_id_set)
        ):
            _append_issue(issues, seen, "source_question_fk", f"{source_path}.question_ids", "source question_ids must reference corpus records")
        expected_source_question_ids = {
            question_id
            for question_id, record in (
                (_record_id(record), record) for record in records
            )
            if question_id is not None and source_id in _record_source_ids(record)
        }
        if source_question_id_strings and set(source_question_id_strings) != expected_source_question_ids:
            _append_issue(issues, seen, "source_question_fk", f"{source_path}.question_ids", "source question_ids must match record source associations")
        lifecycle = _value(source, "lifecycle")
        if not _allowed(lifecycle, {"draft", "active", "needs_review", "retired"}):
            _append_issue(issues, seen, "source_state", f"{source_path}.lifecycle", "source lifecycle state is invalid")
        if not _allowed(_source_trust(source), {"low", "medium", "high"}):
            _append_issue(issues, seen, "source_trust", f"{source_path}.trust", "source trust level is invalid")
        if not _allowed(_value(source, "review_class"), {"dynamic", "evergreen"}):
            _append_issue(issues, seen, "source_review_class", f"{source_path}.review_class", "source review class is invalid")
        if _source_type(source) == "public_interview_experience":
            published_at = _date(_value(source, "published_at"))
            if published_at is None or not (FALLBACK_START <= published_at <= as_of):
                _append_issue(issues, seen, "signal_date", f"{source_path}.published_at", "public interview published_at must be within the corpus date window")
        elif _value(source, "published_at") is None and _value(source, "date_basis") != "retrieved_at":
            _append_issue(issues, seen, "source_date_basis", f"{source_path}.date_basis", "sources without a publication date must declare retrieved_at as the date basis")
        published_at = _date(_value(source, "published_at"))
        accessed_at = _date(_value(source, "accessed_at", _value(source, "retrieved_at")))
        verified_at = _date(_value(source, "verified_at"))
        if accessed_at is None or verified_at is None:
            _append_issue(issues, seen, "source_date", f"{source_path}.verified_at", "source accessed_at and verified_at are required")
        if published_at is not None and published_at > as_of:
            _append_issue(issues, seen, "source_date", f"{source_path}.published_at", "source published_at must not be after the audit date")
        if accessed_at is not None and accessed_at > as_of:
            _append_issue(issues, seen, "source_date", f"{source_path}.accessed_at", "source accessed_at must not be after the audit date")
        if verified_at is not None and verified_at > as_of:
            _append_issue(issues, seen, "source_date", f"{source_path}.verified_at", "source verified_at must not be after the audit date")
        if published_at is not None and accessed_at is not None and published_at > accessed_at:
            _append_issue(issues, seen, "source_date_order", f"{source_path}.accessed_at", "source accessed_at must not precede published_at")
        if accessed_at is not None and verified_at is not None and accessed_at > verified_at:
            _append_issue(issues, seen, "source_date_order", f"{source_path}.verified_at", "source accessed_at must not be after verified_at")
        source_ok, source_reason = _source_freshness(source, as_of)
        source_is_in_use = source_id in referenced_source_ids or lifecycle == "active"
        if _is_release(manifest, records) and source_is_in_use and not source_ok:
            _append_issue(issues, seen, "source_freshness", f"{source_path}.verified_at", source_reason)
        if _is_release(manifest, records) and source_is_in_use and _source_trust(source) not in ACTIVE_TRUST_LEVELS:
            _append_issue(issues, seen, "source_trust", f"{source_path}.trust", "sources used by active records require medium or high trust")
        if _is_release(manifest, records) and source_is_in_use and _value(source, "access_status") != "accessible":
            _append_issue(issues, seen, "source_access", f"{source_path}.access_status", "active sources must be directly accessible")
        if _is_release(manifest, records) and source_is_in_use and _value(source, "rights_status") != "approved":
            _append_issue(issues, seen, "source_rights", f"{source_path}.rights_status", "sources used by active records require approved rights status")
        if _is_release(manifest, records) and any(
            source_id in _record_source_ids(record)
            and _is_active_record(record)
            and lifecycle != "active"
            for record in records
        ):
            _append_issue(issues, seen, "active_source_state", f"{source_path}.lifecycle", "active questions require active source lifecycle")
        # Draft sources may intentionally have question_ids=[] before Task 7;
        # once a release/active record uses them, that empty association is a gate.
        if _is_release(manifest, records) and not source_question_ids:
            _append_issue(issues, seen, "source_question_fk", f"{source_path}.question_ids", "active or release sources must declare question associations")
        next_review_at = _date(_value(source, "next_review_at"))
        verified_at = _date(_value(source, "verified_at"))
        source_requires_lifecycle = source_is_in_use and (
            _is_release(manifest, records) or lifecycle == "active"
        )
        review_days = 365 if _value(source, "review_class") == "evergreen" else 180
        if source_requires_lifecycle and next_review_at is None:
            _append_issue(
                issues,
                seen,
                "source_next_review",
                f"{source_path}.next_review_at",
                "active or release sources require next_review_at",
            )
        if next_review_at is not None and verified_at is not None:
            if next_review_at != verified_at + timedelta(days=review_days):
                _append_issue(issues, seen, "source_review_window", f"{source_path}.next_review_at", "source next_review_at does not match its review class window")
            if source_requires_lifecycle and next_review_at <= as_of:
                _append_issue(
                    issues,
                    seen,
                    "source_review_due",
                    f"{source_path}.next_review_at",
                    "active or release sources must not be past their review deadline",
                )
        if normalized_url is not None:
            for record_index, record in enumerate(records):
                if source_id not in _record_source_ids(record):
                    continue
                if source_requires_lifecycle and _is_active_record(record):
                    record_valid_until = _date(_value(record, "valid_until"))
                    if next_review_at is None or (
                        record_valid_until is not None and record_valid_until > next_review_at
                    ):
                        _append_issue(
                            issues,
                            seen,
                            "record_source_lifecycle",
                            f"questions.json.questions[{record_index}].valid_until",
                            "active question valid_until must not outlive its source review lifecycle",
                        )
                # ``source_url`` is the legacy primary-source projection.  A
                # v2 record can reference independent secondary sources, whose
                # concrete URLs are represented by locator/registry relations.
                if source_id != _value(record, "source_id"):
                    continue
                if _value(record, "source_type") != _source_type(source):
                    _append_issue(issues, seen, "source_type_mismatch", f"questions.json.questions[{record_index}].source_type", "record source type must match the primary registry source")
                record_url = _value(record, "source_url")
                try:
                    record_normalized_url = canonicalize_source_url(record_url or "")
                except (TypeError, ValueError):
                    record_normalized_url = None
                if record_normalized_url != normalized_url:
                    _append_issue(issues, seen, "canonical_url_mismatch", f"questions.json.questions[{record_index}].source_url", "record source URL must match the registry canonical URL")
        if source_requires_lifecycle:
            for record_index, record in enumerate(records):
                if source_id not in _record_source_ids(record) or not _is_active_record(record):
                    continue
                record_valid_until = _date(_value(record, "valid_until"))
                if next_review_at is None or (
                    record_valid_until is not None and record_valid_until > next_review_at
                ):
                    _append_issue(
                        issues,
                        seen,
                        "record_source_lifecycle",
                        f"questions.json.questions[{record_index}].valid_until",
                        "active question valid_until must not outlive its source review lifecycle",
                    )

    # Record-level evidence, lifecycle and cross-sidecar checks.
    rights_lookup = {(key[0], key[1]): item for key, item in zip(rights_keys, rights_values) if key[0] is not None and key[1] is not None}
    locator_lookup = {(key[0], key[1]): item for key, item in zip(locator_keys, locator_values) if key[0] is not None and key[1] is not None}
    try:
        expected_question_set_hash = compute_question_set_hash(records)
    except (AttributeError, TypeError, ValueError):
        expected_question_set_hash = None
    if _value(manifest, "question_set_hash") != expected_question_set_hash:
        _append_issue(
            issues,
            seen,
            "question_set_hash",
            "QuestionBankManifest.json.question_set_hash",
            "manifest question_set_hash does not match canonical records",
        )
    try:
        expected_sidecar_set_hash = compute_sidecar_set_hash(snapshot)
    except (AttributeError, TypeError, ValueError):
        expected_sidecar_set_hash = None
    if _value(manifest, "sidecar_set_hash") != expected_sidecar_set_hash:
        _append_issue(
            issues,
            seen,
            "sidecar_set_hash",
            "QuestionBankManifest.json.sidecar_set_hash",
            "manifest sidecar_set_hash does not match governance sidecars",
        )
    association = _association_map(records, review_values, source_lookup)
    record_by_id = {
        question_id: record
        for record in records
        if (question_id := _record_id(record)) is not None
    }
    canonical_hash_groups: dict[str, set[str]] = defaultdict(set)
    for question_id, record in record_by_id.items():
        try:
            canonical_record_hash = compute_question_content_hash(record)
        except (AttributeError, TypeError, ValueError):
            continue
        canonical_hash_groups[canonical_record_hash].add(question_id)
    semantic_hash_groups: dict[str, set[str]] = defaultdict(set)
    for question_id, dedupe in dedupe_lookup.items():
        semantic_hash = _value(dedupe, "semantic_hash", _value(dedupe, "normalized_semantic_hash"))
        if isinstance(semantic_hash, str) and semantic_hash.strip():
            semantic_hash_groups[semantic_hash].add(question_id)
        record = record_by_id.get(question_id)
        if record is not None:
            try:
                expected_semantic_hash = compute_question_semantic_hash(record)
            except (AttributeError, TypeError, ValueError):
                expected_semantic_hash = None
            if semantic_hash != expected_semantic_hash:
                _append_issue(
                    issues,
                    seen,
                    "dedupe_semantic_hash",
                    f"dedupe.json[{question_id}].semantic_hash",
                    "semantic_hash must equal SHA-256 of normalized question_text",
                )
        candidates = _value(
            dedupe,
            "candidate_duplicate_group",
            _value(dedupe, "candidate_duplicate_ids", ()),
        ) or ()
        if isinstance(candidates, (str, bytes, bytearray)):
            candidates = ()
        try:
            candidates = list(candidates)
        except TypeError:
            candidates = []
        unknown_candidates = [
            candidate
            for candidate in candidates
            if not isinstance(candidate, str) or candidate not in record_by_id
        ]
        if unknown_candidates:
            _append_issue(
                issues,
                seen,
                "dedupe_candidate_fk",
                f"dedupe.json[{question_id}].candidate_duplicate_group",
                "candidate duplicate group must reference corpus question IDs",
            )
        if question_id in candidates:
            _append_issue(
                issues,
                seen,
                "dedupe_candidate_fk",
                f"dedupe.json[{question_id}].candidate_duplicate_group",
                "candidate duplicate group must not include its own question ID",
            )
        if candidates:
            decision = _value(dedupe, "decision")
            near_decision = _value(dedupe, "near_duplicate_decision")
            if decision in {"pending", "needs_review"} or near_decision in {"pending"}:
                _append_issue(
                    issues,
                    seen,
                    "dedupe_group_decision",
                    f"dedupe.json[{question_id}].candidate_duplicate_group",
                    "candidate duplicate groups require a completed decision",
                )
            for candidate in candidates:
                candidate_dedupe = (
                    dedupe_lookup.get(candidate)
                    if isinstance(candidate, str)
                    else None
                )
                if candidate_dedupe is None:
                    continue
                candidate_decision = _value(candidate_dedupe, "decision")
                candidate_near_decision = _value(
                    candidate_dedupe,
                    "near_duplicate_decision",
                )
                if candidate_decision in {"pending", "needs_review"} or candidate_near_decision in {"pending"}:
                    _append_issue(
                        issues,
                        seen,
                        "dedupe_group_decision",
                        f"dedupe.json[{question_id}].candidate_duplicate_group",
                        "all candidate duplicate group decisions must be completed",
                    )
    for semantic_hash, question_ids in semantic_hash_groups.items():
        if len(question_ids) < 2:
            continue
        if any(_is_active_record(record_by_id[question_id]) for question_id in question_ids if question_id in record_by_id):
            _append_issue(
                issues,
                seen,
                "active_duplicate_semantic_hash",
                "dedupe.json",
                "active questions must not share a semantic hash",
            )
    for canonical_record_hash, question_ids in canonical_hash_groups.items():
        if len(question_ids) < 2:
            continue
        if any(_is_active_record(record_by_id[question_id]) for question_id in question_ids):
            _append_issue(
                issues,
                seen,
                "active_duplicate_semantic_hash",
                "questions.json",
                "active questions must not share a canonical semantic hash",
            )
    fallback_questions: set[str] = set()
    near180_questions: set[str] = set()
    near365_questions: set[str] = set()

    for index, record in enumerate(records):
        path = f"questions.json.questions[{index}]"
        question_id = _record_id(record)
        if question_id is None:
            continue
        source_ids = _record_source_ids(record)
        review = review_lookup.get(question_id)
        if review is None:
            _append_issue(issues, seen, "review_missing", f"review.json[{question_id}]", "every question requires a review record")
            signal_ids: list[str] = []
            cross_ids: list[str] = []
        else:
            signal_ids = [value for value in (_value(review, "signal_source_ids", ()) or ()) if isinstance(value, str)]
            cross_ids = [value for value in (_value(review, "cross_validation_source_ids", ()) or ()) if isinstance(value, str)]
            if not signal_ids:
                _append_issue(issues, seen, "signal_evidence", f"review.json[{question_id}].signal_source_ids", "every question requires an interview signal")
            if not cross_ids:
                _append_issue(issues, seen, "cross_validation", f"review.json[{question_id}].cross_validation_source_ids", "every question requires official or current JD cross-validation")
            if set(signal_ids).intersection(cross_ids):
                _append_issue(issues, seen, "evidence_independence", f"review.json[{question_id}]", "signal and cross-validation evidence must be independent")
            decision = _value(review, "decision", _value(review, "review_status"))
            review_is_gated = _is_active_record(record) or _is_release(manifest, records)
            release_receipt_ok = bool(
                _is_release(manifest, records)
                and decision == "pending_human"
                and _has_human_approval(
                    review, question_id, as_of, approval_verifier,
                    expected_question_set_hash, expected_sidecar_set_hash,
                )
            )
            effective_approved = decision == "approved" or release_receipt_ok
            if review_is_gated and not effective_approved:
                _append_issue(issues, seen, "review_decision", f"review.json[{question_id}].decision", "active questions require an approved review")
            if review_is_gated and effective_approved and not _has_human_approval(
                review,
                question_id,
                as_of,
                approval_verifier,
                expected_question_set_hash,
                expected_sidecar_set_hash,
            ):
                _append_issue(
                    issues,
                    seen,
                    "human_approval_required",
                    f"review.json[{question_id}]",
                    "approved active reviews require an externally verified immutable approval receipt",
                )
            if review_is_gated and any(
                not _non_blank_text(_value(review, field_name))
                for field_name in (
                    "capability_summary",
                    "business_constraint_summary",
                    "dimension_summary",
                    "mode_rationale",
                )
            ):
                _append_issue(
                    issues,
                    seen,
                    "review_summary",
                    f"review.json[{question_id}]",
                    "active reviews require capability, business constraint, dimension, and mode summaries",
                )
            reviewer_ids = _value(review, "reviewer_ids", _value(review, "reviewers", ())) or ()
            if isinstance(reviewer_ids, (str, bytes, bytearray)):
                reviewer_ids = []
            try:
                has_reviewer = bool(list(reviewer_ids)) or bool(
                    isinstance(_value(review, "reviewer"), str)
                    and _value(review, "reviewer").strip()
                )
            except TypeError:
                has_reviewer = bool(
                    isinstance(_value(review, "reviewer"), str)
                    and _value(review, "reviewer").strip()
                )
            safety_passed = (
                has_reviewer
                and bool(signal_ids)
                and bool(cross_ids)
                and _value(review, "originality_confirmed") is True
                and (
                    _value(review, "pii_scan_passed") is True
                    or _value(review, "pii_scan") == "passed"
                )
                and (
                    _value(review, "rights_review_passed") is True
                    or _allowed(_value(review, "rights_conclusion"), {"approved", "clear"})
                )
                and _value(review, "difficulty_consistent") is True
            )
            if (_is_active_record(record) or _is_release(manifest, records)) and effective_approved and not safety_passed:
                _append_issue(issues, seen, "review_safety", f"review.json[{question_id}]", "approved active reviews require all safety checks")
            if _is_release(manifest, records) and decision == "pending_human" and not release_receipt_ok:
                _append_issue(issues, seen, "review_pending", f"review.json[{question_id}].decision", "pending_human is not an approval")
            if decision in {"rejected", "needs_revision"} and not _non_blank_text(_value(review, "rejection_reason")):
                _append_issue(
                    issues,
                    seen,
                    "rejection_reason",
                    f"review.json[{question_id}].rejection_reason",
                    "rejected reviews require a rejection reason",
                )
            if (
                decision == "retired"
                or _value(record, "status") == "retired"
            ) and not _non_blank_text(_value(review, "retirement_reason")):
                _append_issue(
                    issues,
                    seen,
                    "retirement_reason",
                    f"review.json[{question_id}].retirement_reason",
                    "retired questions require a retirement reason",
                )
            reviewed_at = _date(_value(review, "reviewed_at"))
            if reviewed_at is None:
                _append_issue(issues, seen, "review_date", f"review.json[{question_id}].reviewed_at", "reviewed_at is required")
            elif reviewed_at > as_of:
                _append_issue(issues, seen, "review_date", f"review.json[{question_id}].reviewed_at", "reviewed_at must not be after the audit date")
            review_due_at = _date(_value(review, "review_due_at"))
            if _is_active_record(record) and review_due_at is None:
                _append_issue(
                    issues,
                    seen,
                    "review_due",
                    f"review.json[{question_id}].review_due_at",
                    "active reviews require review_due_at",
                )
            elif review_due_at is not None and review_due_at <= as_of and _is_active_record(record):
                _append_issue(issues, seen, "review_due", f"review.json[{question_id}].review_due_at", "active review is due or past due")

        interview_dates: list[date] = []
        for source_id in signal_ids:
            source = source_lookup.get(source_id)
            if source is None:
                _append_issue(issues, seen, "source_fk", f"review.json[{question_id}].signal_source_ids", "review references an unknown source")
                continue
            if source_id not in source_ids:
                _append_issue(issues, seen, "source_fk", f"review.json[{question_id}].signal_source_ids", "review source must be included in record source_ids")
            if _source_type(source) != "public_interview_experience":
                _append_issue(issues, seen, "signal_source_type", f"review.json[{question_id}].signal_source_ids", "signal evidence must be public interview experience")
                continue
            published_at = _date(_value(source, "published_at"))
            if published_at is None:
                continue
            interview_dates.append(published_at)
            age = (as_of - published_at).days
            if age < 0:
                _append_issue(issues, seen, "future_signal_date", f"review.json[{question_id}].signal_source_ids", "signal published_at cannot be after corpus_as_of")
            elif published_at < FALLBACK_START:
                _append_issue(issues, seen, "signal_date", f"review.json[{question_id}].signal_source_ids", "signal is earlier than the fallback lower bound")
        if len(interview_dates) > 1 and interview_dates[0] != max(interview_dates):
            _append_issue(issues, seen, "signal_not_recent", f"review.json[{question_id}].signal_source_ids", "interview signals must be ordered by most recent published_at")
        # A question is classified by the most recent signal it supplies.  An
        # older fallback source kept for traceability must not consume the
        # fallback quota when a newer interview signal is present.
        if interview_dates:
            latest_signal = max(interview_dates)
            latest_age = (as_of - latest_signal).days
            if 0 <= latest_age <= NEAR_180_DAYS:
                near180_questions.add(question_id)
                near365_questions.add(question_id)
            elif 0 <= latest_age <= NEAR_365_DAYS:
                near365_questions.add(question_id)
            elif latest_signal >= FALLBACK_START:
                fallback_questions.add(question_id)

        cross_sources: list[Any] = []
        for source_id in cross_ids:
            source = source_lookup.get(source_id)
            if source is None:
                _append_issue(issues, seen, "source_fk", f"review.json[{question_id}].cross_validation_source_ids", "review references an unknown source")
                continue
            cross_sources.append(source)
            if source_id not in source_ids:
                _append_issue(issues, seen, "source_fk", f"review.json[{question_id}].cross_validation_source_ids", "review source must be included in record source_ids")
            if _source_type(source) not in {"official_technical_doc", "current_enterprise_jd"}:
                _append_issue(issues, seen, "cross_validation_type", f"review.json[{question_id}].cross_validation_source_ids", "cross-validation must be official technical documentation or a current enterprise JD")
            source_ok, source_reason = _source_freshness(source, as_of)
            if not source_ok:
                _append_issue(issues, seen, "cross_validation_freshness", f"review.json[{question_id}].cross_validation_source_ids", source_reason)
            if _source_id(source) in signal_ids:
                _append_issue(issues, seen, "evidence_independence", f"review.json[{question_id}]", "cross-validation source must be independent")
        if cross_sources and signal_ids:
            signal_urls = set()
            for source_id in signal_ids:
                source = source_lookup.get(source_id)
                if source is None:
                    continue
                try:
                    signal_urls.add(canonicalize_source_url(_source_url(source) or ""))
                except ValueError:
                    continue
            cross_urls = set()
            for source in cross_sources:
                try:
                    cross_urls.add(canonicalize_source_url(_source_url(source) or ""))
                except ValueError:
                    continue
            if signal_urls.intersection(cross_urls):
                _append_issue(issues, seen, "evidence_independence", f"review.json[{question_id}]", "signal and cross-validation URLs must be independent")

        if question_id in fallback_questions:
            exception_reason_code = _value(review, "exception_reason_code") if review is not None else None
            exception_reason = _value(review, "exception_reason") if review is not None else None
            if (
                exception_reason_code != "coverage_gap"
                or not isinstance(exception_reason, str)
                or not exception_reason.strip()
                or not _GAP_REASON_RE.search(exception_reason)
                or _FORBIDDEN_FALLBACK_REASON_RE.search(exception_reason)
            ):
                _append_issue(issues, seen, "fallback_exception", f"review.json[{question_id}].exception_reason", "fallback signals require a structured coverage_gap reason without negation or convenience wording")
            extra_recent = False
            for source in cross_sources:
                if _source_type(source) not in {"official_technical_doc", "current_enterprise_jd"}:
                    continue
                verified_at = _date(_value(source, "verified_at"))
                accessed_at = _date(_value(source, "accessed_at", _value(source, "retrieved_at")))
                if (
                    verified_at is not None
                    and accessed_at is not None
                    and 0 <= (as_of - verified_at).days <= NEAR_180_DAYS
                    and 0 <= (as_of - accessed_at).days <= NEAR_180_DAYS
                ):
                    extra_recent = True
                    break
            if not extra_recent:
                _append_issue(issues, seen, "fallback_cross_validation", f"review.json[{question_id}].cross_validation_source_ids", "fallback signals require an additional recent official or JD source")

        # Every source relation needs rights and locator facts.  Draft records
        # are still checked for completeness; only active relations are gated
        # on approved rights/access decisions.
        for source_id in source_ids:
            source = source_lookup.get(source_id)
            if source is None:
                _append_issue(issues, seen, "source_fk", f"{path}.source_ids", "record references an unknown source")
                continue
            key = (question_id, source_id)
            rights = rights_lookup.get(key)
            locator = locator_lookup.get(key)
            if rights is None:
                _append_issue(issues, seen, "rights_missing", f"rights.json[{question_id},{source_id}]", "every question/source relation requires rights facts")
            elif _is_active_record(record) or _is_release(manifest, records):
                public_access = _value(rights, "public_access")
                allowed = public_access is True or _allowed(public_access, {"allowed", "public"})
                decision = _value(rights, "decision", _value(rights, "rights_status"))
                original_text_present = _value(
                    rights,
                    "original_text_present",
                    _value(rights, "contains_original_text", False),
                )
                answer_present = _value(
                    rights,
                    "answer_present",
                    _value(rights, "contains_answer", False),
                )
                no_pii = _value(rights, "no_pii", _value(rights, "pii_scan_passed", False))
                no_paid_content = _value(
                    rights,
                    "no_paid_content",
                    _value(rights, "paid_content_scan_passed", False),
                )
                if decision != "approved" or not allowed or _value(rights, "paraphrase_only") is not True or original_text_present is not False or answer_present is not False or no_pii is not True or no_paid_content is not True or _value(rights, "contains_pii") is True or _value(rights, "contains_paid_content") is True or _value(rights, "originality_confirmed") is not True:
                    _append_issue(issues, seen, "rights_decision", f"rights.json[{question_id},{source_id}]", "active relations require approved public paraphrase-only rights with no PII or paid content")
            if locator is None:
                _append_issue(issues, seen, "locator_missing", f"locator.json[{question_id},{source_id}]", "every question/source relation requires a locator")
            else:
                try:
                    locator_url = canonicalize_source_url(_value(locator, "canonical_url", ""))
                    source_url = canonicalize_source_url(_source_url(source) or "")
                except (TypeError, ValueError):
                    locator_url = source_url = None
                if locator_url != source_url:
                    _append_issue(issues, seen, "locator_url", f"locator.json[{question_id},{source_id}].canonical_url", "locator URL must match the source registry")
                viewed_at = _date(_value(locator, "viewed_at", _value(locator, "accessed_at", _value(locator, "retrieved_at"))))
                if viewed_at is None or viewed_at > as_of:
                    _append_issue(issues, seen, "locator_date", f"locator.json[{question_id},{source_id}].viewed_at", "locator viewed_at must not be after the audit date")
                locator_hash = _value(locator, "locator_hash", _value(locator, "locator_digest"))
                if not isinstance(locator_hash, str) or not locator_hash.strip():
                    _append_issue(issues, seen, "locator_hash", f"locator.json[{question_id},{source_id}].locator_hash", "locator hash is required")
            reviewed_at = _date(_value(rights, "reviewed_at")) if rights is not None else None
            if reviewed_at is None and rights is not None:
                _append_issue(issues, seen, "rights_date", f"rights.json[{question_id},{source_id}].reviewed_at", "rights reviewed_at is required")
            elif reviewed_at is not None and reviewed_at > as_of:
                _append_issue(issues, seen, "rights_date", f"rights.json[{question_id},{source_id}].reviewed_at", "rights review date must not be after the audit date")

        dedupe = dedupe_lookup.get(question_id)
        if dedupe is None:
            _append_issue(issues, seen, "dedupe_missing", f"dedupe.json[{question_id}]", "every question requires a dedupe record")
        elif _is_active_record(record) or _is_release(manifest, records):
            if not _allowed(_value(dedupe, "decision"), {"unique"}) or not _allowed(_value(dedupe, "near_duplicate_decision"), {"clear", "unique"}):
                _append_issue(issues, seen, "dedupe_decision", f"dedupe.json[{question_id}]", "active questions require a resolved unique dedupe decision")
            dedupe_reviewed_at = _date(_value(dedupe, "reviewed_at"))
            if dedupe_reviewed_at is None:
                _append_issue(issues, seen, "dedupe_date", f"dedupe.json[{question_id}].reviewed_at", "dedupe reviewed_at is required")
            elif dedupe_reviewed_at > as_of:
                _append_issue(issues, seen, "dedupe_date", f"dedupe.json[{question_id}].reviewed_at", "dedupe reviewed_at must not be after the audit date")

    if len(fallback_questions) > MAX_FALLBACK:
        _append_issue(issues, seen, "fallback_count", "review.json", "fallback interview signals may cover at most three questions")
    if len(near180_questions) < MIN_NEAR_180:
        _append_issue(issues, seen, "near_180_count", "review.json", "at least 18 questions require a recent 180-day interview signal")
    if len(near365_questions) < MIN_NEAR_365:
        _append_issue(issues, seen, "near_365_count", "review.json", "at least 27 questions require a recent 365-day interview signal")

    # URL identity and association caps count unique question references, so a
    # source cannot evade the cap by appearing in multiple sidecar lists.
    canonical_url_questions: dict[str, set[str]] = defaultdict(set)
    canonical_url_sources: dict[str, set[str]] = defaultdict(set)
    for source_id, source in source_lookup.items():
        try:
            canonical_url_sources[canonicalize_source_url(_source_url(source) or "")].add(source_id)
        except ValueError:
            continue
    if any(len(source_ids) > 1 for source_ids in canonical_url_sources.values()):
        _append_issue(issues, seen, "duplicate_canonical_url", "QuestionSourceRegistry.json", "each canonical URL must have one normalized source identity")
    for source_id, question_ids in association.items():
        source = source_lookup.get(source_id)
        if source is None:
            continue
        try:
            canonical_url = canonicalize_source_url(_source_url(source) or "")
        except ValueError:
            continue
        canonical_url_questions[canonical_url].update(question_ids)
    if len(canonical_url_questions) < MIN_INDEPENDENT_URLS:
        _append_issue(issues, seen, "independent_url_count", "QuestionSourceRegistry.json", "the corpus requires at least 12 independent canonical URLs")
    if any(len(question_ids) > MAX_QUESTIONS_PER_URL for question_ids in canonical_url_questions.values()):
        _append_issue(issues, seen, "url_association_cap", "QuestionSourceRegistry.json", "a canonical URL may support at most three questions")

    # Recalculate active count; hand-written manifest counts are not trusted.
    active_count = sum(1 for record in records if _is_active_record(record))
    if _value(manifest, "active_count") != active_count:
        _append_issue(issues, seen, "active_count", "QuestionBankManifest.json.active_count", "manifest active_count does not match records")

    publication_status = _value(manifest, "publication_status", _value(manifest, "release_status"))
    if _allowed(publication_status, {"ready", "published"}) and any(
        _value(record, "status") != "active" for record in records
    ):
        _append_issue(issues, seen, "publication_state", "QuestionBankManifest.json.publication_status", "ready or published corpora may contain only active questions")

    return sorted(issues, key=lambda issue: (issue.path, issue.code, issue.message, issue.severity))


def build_manifest_preview(
    snapshot: QuestionCorpusSnapshot,
    issues: Sequence[CorpusIssue] = (),
) -> dict[str, Any]:
    """Build a candidate-safe manifest/report preview for the read-only CLI."""

    records, manifest, source_values, *_ = _snapshot_parts(snapshot)
    dimension_counts = _safe_counter(_value(record, "dimension_id") for record in records)
    mode_counts = _safe_counter(_primary_mode(record) for record in records)
    preview: dict[str, Any] = {
        "status": "valid" if not any(issue.severity == "error" for issue in issues) else "invalid",
        "stage": "validation",
        "structure_valid": True,
        "question_count": len(records),
        "question_ids": sorted(
            question_id for question_id in (_record_id(record) for record in records) if question_id is not None
        ),
        "role": _value(manifest, "role"),
        "role_version": _value(manifest, "role_version"),
        "manifest_version": _value(manifest, "manifest_version"),
        "schema_version": _value(manifest, "schema_version"),
        "embedding_contract_version": _value(manifest, "embedding_contract_version"),
        "question_set_hash": _value(manifest, "question_set_hash"),
        "sidecar_set_hash": _value(manifest, "sidecar_set_hash"),
        "publication_status": _value(manifest, "publication_status"),
        "active_count": sum(1 for record in records if _is_active_record(record)),
        "dimension_counts": {key: dimension_counts.get(key, 0) for key in QUESTION_DIMENSIONS},
        "primary_mode_counts": {key: mode_counts.get(key, 0) for key in QUESTION_MODES},
        "source_count": len(source_values),
        "validation_issue_count": len(issues),
    }
    try:
        preview["manifest_hash"] = compute_question_bank_manifest_hash(
            records,
            _value(snapshot, "source_registry"),
            QuestionModePolicy.default(),
        )
    except (TypeError, ValueError, AttributeError):
        # A malformed snapshot is still useful in an audit artifact; the
        # validator's structured issues carry the reason and no placeholder
        # hash should be invented here.
        preview["manifest_hash"] = None
    return preview


__all__ = [
    "ACTIVE_TRUST_LEVELS",
    "ApprovalReceipt",
    "ApprovalVerifier",
    "DEFAULT_CORPUS_DIR",
    "EMBEDDING_CONTRACT_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "V2_MANIFEST_REQUIRED_FIELDS",
    "CorpusIssue",
    "SOURCE_TYPES",
    "build_manifest_preview",
    "canonicalize_source_url",
    "compute_question_set_hash",
    "compute_sidecar_set_hash",
    "load_question_corpus_snapshot",
    "validate_question_corpus",
]
