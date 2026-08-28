"""Disposable local Qdrant index for the canonical interview-question bank.

The JSON question bank remains authoritative.  This adapter only stores a
rebuildable vector index and one manifest point that records the embedding
fingerprint used to create it.  It intentionally has no embedding-provider or
network code; callers pass already-computed vectors (tests can therefore use a
deterministic fake client).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
import logging
import math
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from urllib.parse import urlparse

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    Range,
    VectorParams,
)

from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    MODE_POLICY_VERSION,
    QuestionModePolicy,
    QuestionRetrievalIntent,
    RetrievedQuestion,
)


COLLECTION_NAME = "interview_questions"
_MANIFEST_RECORD_TYPE = "manifest"
_QUESTION_RECORD_TYPE = "question"
_MANIFEST_KEY = "__interview_question_index_manifest__"
_NAMESPACE = uuid5(NAMESPACE_URL, "keda-profile-agent/interview-questions")
_SEARCH_STATUSES = Literal["hit", "no_match", "unavailable", "index_mismatch"]
# Qdrant is a disposable retrieval index, not a second question-bank
# authority.  Keep only fields needed for filtering/ranking and an internal
# trace.  Rubric/provenance/PII-bearing fields remain in the authoritative
# JSON bank and never cross this persistence boundary.
_QUESTION_PAYLOAD_ALLOWLIST = frozenset(
    {
        "record_type",
        "question_id",
        "question_text",
        "role",
        "dimension_id",
        "question_mode",
        "skills",
        "source_id",
        "source_type",
        "published_at",
        "verified_at",
        "valid_until",
        "valid_until_epoch",
        "trust_level",
        "status",
    }
)
_QUESTION_RECORD_PAYLOAD_FIELDS = frozenset(
    _QUESTION_PAYLOAD_ALLOWLIST - {"record_type", "valid_until_epoch"}
)
_SAFE_RECONSTRUCTED_SOURCE_TYPE = "qdrant_index"
_SAFE_SOURCE_TYPES = frozenset(
    {"public_interview_experience", "test_only_synthetic"}
)
DEFAULT_EMBEDDING_TEXT_VERSION = "legacy-v1"
DEFAULT_QUESTION_BANK_MANIFEST_HASH = "legacy-v1"
DEFAULT_MODE_POLICY_VERSION = MODE_POLICY_VERSION
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
logger = logging.getLogger(__name__)


def validate_loopback_url(value: Any) -> str:
    """Validate and return an explicit HTTP(S) loopback Qdrant URL.

    A URL is accepted only for the three host spellings deliberately reserved
    for a local calibration service.  In particular, the entire 127/8 range,
    private-network addresses, hostnames, and URLs carrying credentials are
    rejected.  This check runs before ``QdrantClient`` construction.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("local Qdrant URL must not be blank")
    url = value.strip()
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        _ = parsed.port
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("local Qdrant URL is invalid") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("local Qdrant URL is invalid")
    if hostname is None or hostname.casefold().rstrip(".") not in LOOPBACK_HOSTS:
        raise ValueError("local Qdrant URL must use a loopback host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("local Qdrant URL must not contain credentials")
    return url.rstrip("/")


def is_loopback_url(value: Any) -> bool:
    """Return whether ``value`` passes the explicit local URL allowlist."""

    try:
        validate_loopback_url(value)
    except (TypeError, ValueError, UnicodeError):
        return False
    return True


def _safe_source_id(value: Any, question_id: Any) -> str:
    """Keep internal trace IDs opaque and free of URL/email-shaped text."""

    fallback = f"qdrant:{question_id}" if isinstance(question_id, str) else "qdrant:unknown"
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    if not normalized or "://" in normalized or "@" in normalized:
        return fallback
    return normalized


class IndexFingerprint(BaseModel):
    """Identity of the embedding/index contract stored in the manifest."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    provider: str = Field(
        min_length=1,
        validation_alias=AliasChoices("provider", "embedding_provider"),
    )
    model: str = Field(
        min_length=1,
        validation_alias=AliasChoices("model", "embedding_model"),
    )
    dimension: int = Field(
        gt=0,
        validation_alias=AliasChoices("dimension", "vector_dimension"),
    )
    index_version: str = Field(
        min_length=1,
        validation_alias=AliasChoices("index_version", "version"),
    )
    embedding_text_version: str = Field(
        default=DEFAULT_EMBEDDING_TEXT_VERSION,
        min_length=1,
        validation_alias=AliasChoices(
            "embedding_text_version",
            "text_version",
            "embedding_contract_version",
        ),
    )
    question_bank_manifest_hash: str = Field(
        default=DEFAULT_QUESTION_BANK_MANIFEST_HASH,
        min_length=1,
        validation_alias=AliasChoices(
            "question_bank_manifest_hash",
            "manifest_hash",
        ),
    )
    mode_policy_version: str = Field(
        default=DEFAULT_MODE_POLICY_VERSION,
        min_length=1,
        validation_alias=AliasChoices("mode_policy_version", "policy_version"),
    )

    @field_validator(
        "provider",
        "model",
        "index_version",
        "embedding_text_version",
        "question_bank_manifest_hash",
        "mode_policy_version",
    )
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fingerprint text fields must not be blank")
        return value.strip()

    @field_validator("dimension")
    @classmethod
    def validate_dimension(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("fingerprint dimension must be positive")
        return value

    @property
    def embedding_provider(self) -> str:
        """Compatibility name used by manifest payloads and callers."""

        return self.provider

    @property
    def embedding_model(self) -> str:
        return self.model

    @property
    def vector_dimension(self) -> int:
        """Descriptive alias for the vector size in the fingerprint."""

        return self.dimension

    @property
    def version(self) -> str:
        return self.index_version


@dataclass
class QuestionStoreSearchResult:
    """Low-level store result consumed by the later retrieval service."""

    status: _SEARCH_STATUSES
    hits: list[RetrievedQuestion] = field(default_factory=list)
    index_version: str | None = None

    @property
    def results(self) -> list[RetrievedQuestion]:
        """Alias used by callers that call candidates ``results``."""

        return self.hits

    @property
    def records(self) -> list[RetrievedQuestion]:
        return self.hits

    @property
    def selected_question(self) -> RetrievedQuestion | None:
        return self.hits[0] if self.hits else None

    @property
    def question(self) -> RetrievedQuestion | None:
        return self.selected_question

    @property
    def selected_record(self) -> RetrievedQuestion | None:
        return self.selected_question

    @property
    def candidates(self) -> list[RetrievedQuestion]:
        return self.hits


class DeterministicFakeQuestionStore:
    """In-process candidate-safe store used by zero-cost corpus calibration.

    This adapter deliberately keeps the same ``search`` envelope as the
    Qdrant store, while ``retrieve`` adds a trace envelope for the evaluator.
    It never imports or constructs Qdrant and accepts ``needs_review`` records
    only when the explicit ``candidate_safe`` switch is enabled.  The lexical
    component is merely a deterministic fixture ranking signal; it is not a
    claim about embedding quality.
    """

    backend = "deterministic-fake"

    def __init__(
        self,
        *,
        fingerprint: IndexFingerprint | Mapping[str, Any],
        embedding: Any | None = None,
        candidate_safe: bool = True,
        policy: QuestionModePolicy | Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(candidate_safe, bool):
            raise TypeError("candidate_safe must be a bool")
        normalized = QdrantQuestionStore._coerce_fingerprint(fingerprint)
        if normalized is None:
            raise ValueError("fingerprint is required")
        self._expected_fingerprint = normalized
        self._embedding = embedding
        self.candidate_safe = candidate_safe
        self.policy = (
            QuestionModePolicy.default()
            if policy is None
            else QuestionModePolicy.model_validate(policy)
        )
        self._records: dict[str, InterviewQuestionRecord] = {}
        self._vectors: dict[str, list[float]] = {}
        self._closed = False

    @property
    def fingerprint(self) -> IndexFingerprint:
        return self._expected_fingerprint

    @property
    def model(self) -> str:
        return self._expected_fingerprint.model

    @property
    def index_version(self) -> str:
        return self._expected_fingerprint.index_version

    def close(self) -> None:
        self._closed = True

    def rebuild(
        self,
        records: Sequence[InterviewQuestionRecord | Mapping[str, Any]],
        vectors: Sequence[Sequence[float]],
        fingerprint: IndexFingerprint | Mapping[str, Any],
    ) -> None:
        normalized = QdrantQuestionStore._coerce_fingerprint(fingerprint)
        if normalized is None:
            raise ValueError("fingerprint is required")
        if normalized != self._expected_fingerprint:
            raise ValueError("index fingerprint mismatch")
        try:
            record_values = list(records)
            vector_values = list(vectors)
        except TypeError as exc:
            raise TypeError("records and vectors must be sequences") from exc
        if len(record_values) != len(vector_values):
            raise ValueError("records and vectors must have the same length")
        next_records: dict[str, InterviewQuestionRecord] = {}
        next_vectors: dict[str, list[float]] = {}
        for raw_record, raw_vector in zip(record_values, vector_values):
            record = QdrantQuestionStore._validated_question_record(raw_record)
            if record.question_id in next_records:
                raise ValueError("question_id values must be unique")
            vector = QdrantQuestionStore._validate_vector(
                raw_vector, expected_dimension=normalized.dimension
            )
            next_records[record.question_id] = record
            next_vectors[record.question_id] = vector
        self._records = next_records
        self._vectors = next_vectors

    def sync(
        self,
        records: Sequence[InterviewQuestionRecord | Mapping[str, Any]],
        vectors: Sequence[Sequence[float]],
        fingerprint: IndexFingerprint | Mapping[str, Any],
        *,
        today: date | None = None,
    ) -> None:
        """Refresh the in-process index with the same shape as Qdrant sync."""

        if today is not None and not isinstance(today, date):
            raise TypeError("today must be a date")
        self.rebuild(records, vectors, fingerprint)

    def search(
        self,
        *,
        intent: QuestionRetrievalIntent,
        query_vector: Sequence[float],
        today: date,
        limit: int = 3,
    ) -> QuestionStoreSearchResult:
        if self._closed:
            return QuestionStoreSearchResult(status="unavailable")
        if not isinstance(intent, QuestionRetrievalIntent):
            raise TypeError("intent must be QuestionRetrievalIntent")
        if isinstance(today, date) is False:
            raise TypeError("today must be a date")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        try:
            vector = QdrantQuestionStore._validate_vector(
                query_vector, expected_dimension=self._expected_fingerprint.dimension
            )
        except (TypeError, ValueError):
            return QuestionStoreSearchResult(
                status="index_mismatch", index_version=self.index_version
            )
        ranked = self._rank(intent, vector, today, limit=min(limit, 3))
        hits = [
            RetrievedQuestion(
                record=record,
                score=score,
                index_version=self.index_version,
            )
            for record, score, _tier in ranked
        ]
        return QuestionStoreSearchResult(
            status="hit" if hits else "no_match",
            hits=hits,
            index_version=self.index_version,
        )

    def retrieve(
        self,
        intent: QuestionRetrievalIntent,
        *,
        today: date | None = None,
        limit: int = 3,
    ) -> Mapping[str, Any]:
        """Return candidate-safe top-3 results plus an explicit trace."""

        if self._closed:
            return {"status": "unavailable", "hits": [], "trace": {"status": "unavailable"}}
        if self._embedding is None:
            return {"status": "unavailable", "hits": [], "trace": {"status": "unavailable"}}
        as_of = today if today is not None else date.today()
        if not isinstance(as_of, date):
            raise TypeError("today must be a date")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        from profile_agent.services.question_retrieval_service import build_query_embedding_text

        query_text = build_query_embedding_text(intent, ())
        raw_vectors = self._embedding.embed([query_text])
        if not isinstance(raw_vectors, Sequence) or len(raw_vectors) != 1:
            return {"status": "index_mismatch", "hits": [], "trace": {"status": "index_mismatch"}}
        ranked = self._rank(
            intent,
            QdrantQuestionStore._validate_vector(
                raw_vectors[0], expected_dimension=self._expected_fingerprint.dimension
            ),
            as_of,
            limit=min(limit, 3),
        )
        hits: list[dict[str, Any]] = []
        for rank, (record, score, tier) in enumerate(ranked, start=1):
            hits.append(
                {
                    "question_id": record.question_id,
                    "source_id": record.source_id,
                    "score": score,
                    "index_version": self.index_version,
                    "match_tier": tier,
                    "rank": rank,
                }
            )
        if not hits:
            return {
                "status": "no_match",
                "hits": [],
                "index_version": self.index_version,
                "trace": {"status": "no_match"},
            }
        selected = hits[0]
        return {
            "status": "hit",
            "hits": hits,
            "index_version": self.index_version,
            "candidate_pool": [item["question_id"] for item in hits],
            "trace": {"status": "hit", **selected},
        }

    def _rank(
        self,
        intent: QuestionRetrievalIntent,
        query_vector: Sequence[float],
        today: date,
        *,
        limit: int,
    ) -> list[tuple[InterviewQuestionRecord, float, str]]:
        query_chars = {
            char.casefold()
            for char in intent.query_text
            if char.isalnum() or "\u3400" <= char <= "\u9fff"
        }
        exact: list[tuple[InterviewQuestionRecord, str]] = []
        compatible: list[tuple[InterviewQuestionRecord, str]] = []
        requested_mode = intent.question_mode
        allowed_modes = self.policy.compatible_order_for(intent.dimension_id)
        for record in self._records.values():
            if record.role != intent.role or record.dimension_id != intent.dimension_id:
                continue
            if record.question_id in set(intent.excluded_question_ids):
                continue
            if record.status != "active" and not (
                self.candidate_safe and record.status == "needs_review"
            ):
                continue
            if record.trust_level not in {"medium", "high"} or record.valid_until < today:
                continue
            primary = record.primary_mode or record.question_mode
            if primary == requested_mode or record.question_mode == requested_mode:
                exact.append((record, "exact"))
            elif (
                primary in allowed_modes
                and requested_mode in record.compatible_modes
            ):
                compatible.append((record, "compatible"))
        candidates = exact or [
            item
            for mode in allowed_modes
            for item in compatible
            if (item[0].primary_mode or item[0].question_mode) == mode
        ]

        def score(item: tuple[InterviewQuestionRecord, str]) -> float:
            record, _tier = item
            candidate_chars = {
                char.casefold()
                for char in record.question_text
                if char.isalnum() or "\u3400" <= char <= "\u9fff"
            }
            overlap = (
                len(query_chars & candidate_chars) / len(query_chars)
                if query_chars
                else 0.0
            )
            cosine = self._cosine(query_vector, self._vectors[record.question_id])
            # Lexical overlap keeps the fixture useful for calibration while
            # digest-derived vectors still participate in every ordering.
            return round(0.9 * overlap + 0.1 * ((cosine + 1.0) / 2.0), 9)

        candidates.sort(key=lambda item: (-score(item), item[0].question_id))
        return [
            (record, score((record, tier)), tier)
            for record, tier in candidates[:limit]
        ]

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right) or not left:
            return 0.0
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


# Compact aliases for callers that refer to this as an in-memory/local fake.
FakeQuestionStore = DeterministicFakeQuestionStore
InMemoryQuestionStore = DeterministicFakeQuestionStore


class QdrantQuestionStore:
    """A disposable Qdrant-backed index with strict retrieval filters.

    ``path=':memory:'`` is the default so constructing this class is local and
    side-effect free.  A caller may inject a local ``QdrantClient`` or provide
    a persistence path.  The fingerprint is required for every reader and
    writer; a manifest created with another fingerprint is never queried.  A
    reader also needs the authoritative catalog supplied at construction or
    by a successful rebuild/sync; durable points alone are never promoted to
    canonical records.
    """

    def __init__(
        self,
        *,
        path: str | Path = ":memory:",
        url: str | None = None,
        client: QdrantClient | None = None,
        collection_name: str = COLLECTION_NAME,
        fingerprint: IndexFingerprint | Mapping[str, Any] | None = None,
        expected_fingerprint: IndexFingerprint | Mapping[str, Any] | None = None,
        candidate_safe: bool = False,
        authoritative_catalog: Mapping[
            str, InterviewQuestionRecord | Mapping[str, Any]
        ] | None = None,
    ) -> None:
        if fingerprint is not None and expected_fingerprint is not None:
            raise ValueError("pass only one of fingerprint and expected_fingerprint")
        if url is not None and client is not None:
            raise ValueError("pass only one of url and client")
        if collection_name != COLLECTION_NAME:
            raise ValueError(f"collection_name must be {COLLECTION_NAME!r}")
        if not isinstance(candidate_safe, bool):
            raise TypeError("candidate_safe must be a bool")
        configured_fingerprint = self._coerce_fingerprint(
            expected_fingerprint if expected_fingerprint is not None else fingerprint
        )
        if configured_fingerprint is None:
            raise ValueError("fingerprint is required")
        self.collection_name = COLLECTION_NAME
        self._expected_fingerprint = configured_fingerprint
        self.candidate_safe = candidate_safe
        self._url = validate_loopback_url(url) if url is not None else None
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else QdrantClient(url=self._url)
            if self._url is not None
            else QdrantClient(path=str(path))
        )
        self._question_catalog = self._coerce_authoritative_catalog(
            authoritative_catalog
        )

    @property
    def client(self) -> QdrantClient:
        """Expose the injected/local client for diagnostics and local tests."""

        return self._client

    @property
    def fingerprint(self) -> IndexFingerprint:
        return self._expected_fingerprint

    @property
    def url(self) -> str | None:
        """Return the validated endpoint, if this store uses HTTP Qdrant."""

        return self._url

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "QdrantQuestionStore":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def rebuild(
        self,
        records: Sequence[InterviewQuestionRecord | Mapping[str, Any]],
        vectors: Sequence[Sequence[float]],
        fingerprint: IndexFingerprint | Mapping[str, Any],
    ) -> None:
        """Replace the disposable collection after validating all inputs.

        Validation is deliberately completed before replacing the collection so
        a malformed batch cannot destroy a currently usable index.
        """

        normalized_fingerprint = self._coerce_fingerprint(fingerprint)
        if normalized_fingerprint is None:
            raise ValueError("fingerprint is required")
        self._assert_expected_fingerprint(normalized_fingerprint)
        prepared = self._prepare_records(records, vectors, normalized_fingerprint)
        self._write_collection(prepared, normalized_fingerprint)
        self._question_catalog = self._catalog_from_prepared(prepared)

    def sync(
        self,
        records: Sequence[InterviewQuestionRecord | Mapping[str, Any]],
        vectors: Sequence[Sequence[float]],
        fingerprint: IndexFingerprint | Mapping[str, Any],
        *,
        today: date | None = None,
    ) -> None:
        """Upsert current active records and delete retired/stale points.

        A sync never mixes embedding contracts.  If an existing manifest or
        configured expected fingerprint differs, this method raises before any
        point mutation; callers can then choose an explicit ``rebuild``.
        """

        normalized_fingerprint = self._coerce_fingerprint(fingerprint)
        if normalized_fingerprint is None:
            raise ValueError("fingerprint is required")
        prepared = self._prepare_records(records, vectors, normalized_fingerprint)
        self._assert_expected_fingerprint(normalized_fingerprint)
        as_of = today if today is not None else date.today()
        if not isinstance(as_of, date):
            raise TypeError("today must be a date")

        current_manifest = self._read_manifest()
        if current_manifest is not None and current_manifest != normalized_fingerprint:
            raise ValueError("index fingerprint mismatch")

        active = [
            item
            for item in prepared
            if item["payload"].get("status") == "active"
            and self._date_from_payload(item["payload"].get("valid_until")) >= as_of
        ]
        # Sync is a full snapshot replacement as well: staging the active
        # subset and switching the alias means retired/expired/stale points
        # disappear together with the new manifest.
        self._write_collection(active, normalized_fingerprint)
        self._question_catalog = self._catalog_from_prepared(active)

    def search(
        self,
        *,
        intent: QuestionRetrievalIntent,
        query_vector: Sequence[float],
        today: date,
        limit: int = 3,
    ) -> QuestionStoreSearchResult:
        """Return only current, role/dimension/mode-compatible question hits."""

        if not isinstance(intent, QuestionRetrievalIntent):
            raise TypeError("intent must be QuestionRetrievalIntent")
        if not isinstance(today, date):
            raise TypeError("today must be a date")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        try:
            vector = self._validate_vector(query_vector, expected_dimension=None)
        except (TypeError, ValueError):
            return QuestionStoreSearchResult(status="index_mismatch", hits=[])

        try:
            if not self._client.collection_exists(self.collection_name):
                return QuestionStoreSearchResult(status="unavailable", hits=[])
            manifest = self._read_manifest()
            if manifest is None:
                raw_manifest = self._client.retrieve(
                    collection_name=self.collection_name,
                    ids=[self._manifest_point_id()],
                    with_payload=True,
                    with_vectors=False,
                )
                return QuestionStoreSearchResult(
                    status="index_mismatch" if raw_manifest else "unavailable",
                    hits=[],
                )
            if self._expected_fingerprint is not None and manifest != self._expected_fingerprint:
                return QuestionStoreSearchResult(
                    status="index_mismatch",
                    hits=[],
                    index_version=manifest.index_version,
                )
            if len(vector) != manifest.dimension:
                return QuestionStoreSearchResult(
                    status="index_mismatch",
                    hits=[],
                    index_version=manifest.index_version,
                )

            must = [
                FieldCondition(
                    key="record_type", match=MatchValue(value=_QUESTION_RECORD_TYPE)
                ),
                FieldCondition(key="role", match=MatchValue(value=intent.role)),
                FieldCondition(
                    key="dimension_id", match=MatchValue(value=intent.dimension_id)
                ),
                FieldCondition(
                    key="question_mode", match=MatchValue(value=intent.question_mode)
                ),
                (
                    FieldCondition(
                        key="status",
                        match=MatchAny(any=["active", "needs_review"]),
                    )
                    if self.candidate_safe
                    else FieldCondition(key="status", match=MatchValue(value="active"))
                ),
                FieldCondition(key="trust_level", match=MatchAny(any=["medium", "high"])),
                FieldCondition(
                    key="valid_until_epoch",
                    range=Range(gte=float(today.toordinal())),
                ),
            ]
            must_not = []
            if intent.excluded_question_ids:
                must_not.append(
                    FieldCondition(
                        key="question_id",
                        match=MatchAny(any=list(intent.excluded_question_ids)),
                    )
                )
            query_filter = Filter(must=must, must_not=must_not or None)
            response = self._client.query_points(
                collection_name=self.collection_name,
                query=vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            hits: list[RetrievedQuestion] = []
            missing_canonical_record = False
            for point in response.points:
                payload = point.payload if isinstance(point.payload, Mapping) else {}
                if payload.get("record_type") != _QUESTION_RECORD_TYPE:
                    continue
                if not self._payload_matches_intent(
                    payload,
                    intent,
                    today,
                    candidate_safe=self.candidate_safe,
                ):
                    continue
                record = self._record_from_payload(payload)
                if record is None:
                    missing_canonical_record = True
                    continue
                try:
                    score = float(point.score)
                except (TypeError, ValueError, OverflowError):
                    continue
                if not math.isfinite(score):
                    continue
                hits.append(
                    RetrievedQuestion(
                        record=record,
                        score=score,
                        index_version=manifest.index_version,
                    )
                )
            if not hits:
                return QuestionStoreSearchResult(
                    status=(
                        "unavailable" if missing_canonical_record else "no_match"
                    ),
                    hits=[],
                    index_version=manifest.index_version,
                )
            return QuestionStoreSearchResult(
                status="hit",
                hits=hits,
                index_version=manifest.index_version,
            )
        except Exception:
            # Qdrant is an optimization.  Do not leak backend details or make
            # retrieval failures look like an empty authoritative bank.
            return QuestionStoreSearchResult(status="unavailable", hits=[])

    def get_manifest(self) -> IndexFingerprint | None:
        """Return the persisted fingerprint for diagnostics, if available."""

        return self._read_manifest()

    @staticmethod
    def _coerce_fingerprint(
        fingerprint: IndexFingerprint | Mapping[str, Any] | None,
    ) -> IndexFingerprint | None:
        if fingerprint is None:
            return None
        if isinstance(fingerprint, IndexFingerprint):
            return fingerprint
        if isinstance(fingerprint, Mapping):
            try:
                return IndexFingerprint.model_validate(fingerprint)
            except Exception as exc:
                raise ValueError("invalid index fingerprint") from exc
        raise TypeError("fingerprint must be IndexFingerprint or a mapping")

    def _assert_expected_fingerprint(self, fingerprint: IndexFingerprint) -> None:
        if (
            self._expected_fingerprint is not None
            and fingerprint != self._expected_fingerprint
        ):
            raise ValueError("index fingerprint mismatch")

    def _prepare_records(
        self,
        records: Sequence[InterviewQuestionRecord | Mapping[str, Any]],
        vectors: Sequence[Sequence[float]],
        fingerprint: IndexFingerprint,
    ) -> list[dict[str, Any]]:
        if isinstance(records, (str, bytes, bytearray)) or isinstance(
            vectors, (str, bytes, bytearray)
        ):
            raise TypeError("records and vectors must be sequences")
        try:
            record_list = list(records)
            vector_list = list(vectors)
        except TypeError as exc:
            raise TypeError("records and vectors must be sequences") from exc
        if len(record_list) != len(vector_list):
            raise ValueError("records and vectors must have the same length")

        prepared: list[dict[str, Any]] = []
        question_ids: set[str] = set()
        for record, vector in zip(record_list, vector_list):
            validated_record = self._validated_question_record(record)
            payload = self._record_to_payload(record)
            question_id = payload.get("question_id")
            if not isinstance(question_id, str) or not question_id.strip():
                raise ValueError("question records require a non-blank question_id")
            question_id = question_id.strip()
            if question_id in question_ids:
                raise ValueError("question_id values must be unique")
            question_ids.add(question_id)
            normalized_vector = self._validate_vector(
                vector,
                expected_dimension=fingerprint.dimension,
            )
            payload["record_type"] = _QUESTION_RECORD_TYPE
            payload["question_id"] = question_id
            valid_until = self._date_from_payload(payload.get("valid_until"))
            payload["valid_until"] = valid_until.isoformat()
            payload["valid_until_epoch"] = valid_until.toordinal()
            prepared.append(
                {
                    "point_id": self._question_point_id(question_id),
                    "vector": normalized_vector,
                    "payload": payload,
                    "record": validated_record,
                }
            )
        return prepared

    @staticmethod
    def _validated_question_record(
        record: InterviewQuestionRecord | Mapping[str, Any],
    ) -> InterviewQuestionRecord:
        if isinstance(record, InterviewQuestionRecord):
            payload = record.model_dump(mode="python", warnings=False)
        elif isinstance(record, Mapping):
            payload = dict(record)
        else:
            raise TypeError(
                "records must be InterviewQuestionRecord instances or mappings"
            )
        try:
            return InterviewQuestionRecord.model_validate(payload)
        except Exception as exc:
            raise ValueError("invalid interview question record") from exc

    @staticmethod
    def _catalog_from_prepared(
        prepared: Sequence[Mapping[str, Any]],
    ) -> dict[str, InterviewQuestionRecord]:
        catalog: dict[str, InterviewQuestionRecord] = {}
        for item in prepared:
            record = item.get("record")
            if isinstance(record, InterviewQuestionRecord):
                catalog[record.question_id] = record
        return catalog

    @classmethod
    def _coerce_authoritative_catalog(
        cls,
        catalog: Mapping[str, InterviewQuestionRecord | Mapping[str, Any]] | None,
    ) -> dict[str, InterviewQuestionRecord]:
        if catalog is None:
            return {}
        if not isinstance(catalog, Mapping):
            raise TypeError("authoritative_catalog must be a mapping")
        normalized: dict[str, InterviewQuestionRecord] = {}
        for question_id, record in catalog.items():
            if not isinstance(question_id, str) or not question_id.strip():
                raise ValueError("authoritative catalog IDs must be non-blank strings")
            validated = cls._validated_question_record(record)
            if validated.question_id != question_id:
                raise ValueError(
                    "authoritative catalog key must match question_id"
                )
            normalized[question_id] = validated
        return normalized

    @staticmethod
    def _record_to_payload(
        record: InterviewQuestionRecord | Mapping[str, Any],
    ) -> dict[str, Any]:
        validated_record = QdrantQuestionStore._validated_question_record(record)
        payload = validated_record.model_dump(mode="json", warnings=False)
        if not isinstance(payload, dict):
            raise ValueError("invalid interview question record")
        safe_payload = {
            key: payload[key]
            for key in _QUESTION_RECORD_PAYLOAD_FIELDS
            if key in payload
        }
        safe_payload["source_id"] = _safe_source_id(
            safe_payload.get("source_id"), safe_payload.get("question_id")
        )
        if safe_payload.get("source_type") not in _SAFE_SOURCE_TYPES:
            safe_payload["source_type"] = _SAFE_RECONSTRUCTED_SOURCE_TYPE
        return safe_payload

    @staticmethod
    def _validate_vector(
        vector: Sequence[float],
        *,
        expected_dimension: int | None,
    ) -> list[float]:
        if isinstance(vector, (str, bytes, bytearray)):
            raise TypeError("vectors must contain numeric sequences")
        try:
            values = list(vector)
        except TypeError as exc:
            raise TypeError("vectors must contain numeric sequences") from exc
        if not values:
            raise ValueError("vectors must not be empty")
        if expected_dimension is not None and len(values) != expected_dimension:
            raise ValueError("vector dimension does not match index fingerprint")
        normalized: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("vectors must contain finite numbers")
            try:
                numeric_value = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("vectors must contain finite numbers") from exc
            if not math.isfinite(numeric_value):
                raise ValueError("vectors must contain finite numbers")
            normalized.append(numeric_value)
        return normalized

    def _write_collection(
        self,
        prepared: Sequence[Mapping[str, Any]],
        fingerprint: IndexFingerprint,
    ) -> None:
        temporary_collection = self._temporary_collection_name()
        try:
            self._client.create_collection(
                collection_name=temporary_collection,
                vectors_config=VectorParams(
                    size=fingerprint.dimension,
                    distance=Distance.COSINE,
                ),
            )
            points = [self._manifest_point(fingerprint)]
            points.extend(
                PointStruct(
                    id=item["point_id"],
                    vector=item["vector"],
                    payload=item["payload"],
                )
                for item in prepared
            )
            self._client.upsert(
                collection_name=temporary_collection,
                points=points,
                wait=True,
            )
            self._verify_staged_collection(
                temporary_collection,
                fingerprint,
                expected_question_count=len(prepared),
            )
            old_collection = self._active_collection_name()
            self._switch_active_collection(temporary_collection, old_collection)
        except Exception:
            self._delete_collection_safely(temporary_collection)
            raise

    def _verify_staged_collection(
        self,
        collection_name: str,
        fingerprint: IndexFingerprint,
        *,
        expected_question_count: int,
    ) -> None:
        if self._read_manifest(collection_name=collection_name) != fingerprint:
            raise RuntimeError("staged index manifest verification failed")
        if (
            len(self._question_points(collection_name=collection_name))
            != expected_question_count
        ):
            raise RuntimeError("staged index question count verification failed")

    def _switch_active_collection(
        self,
        new_collection: str,
        old_collection: str | None,
    ) -> None:
        alias_target = self._alias_target()
        if alias_target is not None:
            # Qdrant applies the delete/create alias operations as one update,
            # leaving the old alias readable if the update is rejected.
            try:
                self._client.update_collection_aliases(
                    [
                        DeleteAliasOperation(
                            delete_alias=DeleteAlias(alias_name=COLLECTION_NAME)
                        ),
                        CreateAliasOperation(
                            create_alias=CreateAlias(
                                collection_name=new_collection,
                                alias_name=COLLECTION_NAME,
                            )
                        ),
                    ]
                )
            except Exception:
                # The server may have committed the alias update before the
                # client observed a transport error.  Inspect the current
                # alias and staged manifest before deleting the temporary
                # collection in _write_collection's failure path.
                if not self._migration_target_is_verified(new_collection):
                    self._restore_alias_target(alias_target)
                    raise
        elif self._client.collection_exists(COLLECTION_NAME):
            self._migrate_legacy_collection(new_collection)
        else:
            try:
                self._client.update_collection_aliases(
                    [
                        CreateAliasOperation(
                            create_alias=CreateAlias(
                                collection_name=new_collection,
                                alias_name=COLLECTION_NAME,
                            )
                        )
                    ]
                )
            except Exception:
                if not self._migration_target_is_verified(new_collection):
                    raise

        if old_collection and old_collection not in {COLLECTION_NAME, new_collection}:
            self._delete_collection_safely(old_collection)

    def _restore_alias_target(self, old_collection: str | None) -> None:
        """Restore the previous alias after an uncertain staged switch."""

        if old_collection is None:
            return
        if self._alias_target() == old_collection:
            return
        try:
            self._client.update_collection_aliases(
                [
                    DeleteAliasOperation(
                        delete_alias=DeleteAlias(alias_name=COLLECTION_NAME)
                    ),
                    CreateAliasOperation(
                        create_alias=CreateAlias(
                            collection_name=old_collection,
                            alias_name=COLLECTION_NAME,
                        )
                    ),
                ]
            )
        except Exception:
            # A restore request has the same uncertain-commit semantics.  A
            # committed restore is safe even if the response path failed.
            if self._alias_target() == old_collection:
                return
            raise
        if self._alias_target() != old_collection:
            raise RuntimeError("legacy alias restore verification failed")

    def _migrate_legacy_collection(self, new_collection: str) -> None:
        """Switch a pre-alias collection without losing its active snapshot."""

        backup_collection = self._temporary_collection_name()
        self._clone_collection(COLLECTION_NAME, backup_collection)
        migration_succeeded = False
        try:
            # The complete copy is now a rollback point.  Only after it is
            # ready do we touch the legacy fixed-name collection.
            try:
                self._client.delete_collection(collection_name=COLLECTION_NAME)
            except Exception:
                # A request can be applied remotely and still raise while
                # returning.  The collection state is therefore unknown;
                # probe both the fixed name and alias before deciding whether
                # to restore.  In particular, never clean the only rollback
                # copy on this path.
                if self._migration_target_is_verified(new_collection):
                    migration_succeeded = True
                    return
                self._recover_legacy_collection(backup_collection)
                raise

            try:
                self._client.update_collection_aliases(
                    [
                        CreateAliasOperation(
                            create_alias=CreateAlias(
                                collection_name=new_collection,
                                alias_name=COLLECTION_NAME,
                            )
                        )
                    ]
                )
                if not self._migration_target_is_verified(new_collection):
                    raise RuntimeError("legacy alias verification failed")
            except Exception:
                # Alias creation is also an external request.  If it applied
                # despite reporting an error, the target verification above
                # (or the probe below) allows us to complete safely; otherwise
                # restore the legacy snapshot and retain the backup.
                if self._migration_target_is_verified(new_collection):
                    migration_succeeded = True
                    return
                self._recover_legacy_collection(backup_collection)
                raise

            migration_succeeded = True
        finally:
            # A backup is disposable only after the new alias and its
            # manifest have both been verified.  Cleanup itself is best
            # effort; a failure leaves the backup available for operators.
            if migration_succeeded:
                self._delete_collection_safely(backup_collection)

    def _migration_target_is_verified(self, new_collection: str) -> bool:
        """Return whether the fixed alias now points to a valid new index."""

        try:
            if self._alias_target() != new_collection:
                return False
            return (
                self._read_manifest(collection_name=new_collection)
                == self._expected_fingerprint
            )
        except Exception:
            return False

    def _recover_legacy_collection(
        self,
        backup_collection: str,
    ) -> None:
        """Restore the old fixed-name index after an uncertain migration.

        The method deliberately swallows recovery errors so the original
        backend failure remains the API error.  If cloning back to the fixed
        name fails, the still-intact backup is exposed through the same alias
        as a last-resort read path; callers can then continue searching while
        an operator repairs/reclaims the orphan.
        """

        try:
            fixed_exists, alias_target = self._probe_legacy_collection_state()
        except Exception as probe_error:
            self._log_legacy_recovery_failure("probe", probe_error)
            return

        # If the delete did not take effect and the legacy fixed collection is
        # still present, do not touch it.  It is already the safest rollback
        # target; the backup remains for later manual cleanup.
        if (
            fixed_exists
            and alias_target is None
            and self._legacy_collection_is_verified()
        ):
            return

        try:
            if alias_target is not None:
                self._remove_fixed_alias()
            self._restore_collection(backup_collection, COLLECTION_NAME)
            if self._legacy_collection_is_verified():
                return
            raise RuntimeError("restored legacy collection verification failed")
        except Exception as restore_error:
            self._log_legacy_recovery_failure("restore", restore_error)

        # A failed clone can leave the fixed name unavailable.  The backup is
        # a complete old snapshot, so point the fixed alias at it as a final
        # read-only fallback.  This operation is intentionally independent of
        # the original restore error and never makes the backup disposable.
        try:
            self._client.update_collection_aliases(
                [
                    DeleteAliasOperation(
                        delete_alias=DeleteAlias(alias_name=COLLECTION_NAME)
                    ),
                    CreateAliasOperation(
                        create_alias=CreateAlias(
                            collection_name=backup_collection,
                            alias_name=COLLECTION_NAME,
                        )
                    ),
                ]
            )
            if self._alias_target() != backup_collection:
                raise RuntimeError("legacy backup alias verification failed")
        except Exception as fallback_error:
            self._log_legacy_recovery_failure("backup alias", fallback_error)

    def _probe_legacy_collection_state(self) -> tuple[bool, str | None]:
        """Probe fixed collection and alias independently after ambiguity."""

        alias_target = self._alias_target()
        fixed_exists = self._client.collection_exists(COLLECTION_NAME)
        return fixed_exists, alias_target

    def _legacy_collection_is_verified(self) -> bool:
        try:
            return (
                self._alias_target() is None
                and self._client.collection_exists(COLLECTION_NAME)
                and self._read_manifest(collection_name=COLLECTION_NAME)
                == self._expected_fingerprint
            )
        except Exception:
            return False

    def _remove_fixed_alias(self) -> None:
        self._client.update_collection_aliases(
            [
                DeleteAliasOperation(
                    delete_alias=DeleteAlias(alias_name=COLLECTION_NAME)
                )
            ]
        )

    @staticmethod
    def _log_legacy_recovery_failure(operation: str, error: Exception) -> None:
        logger.warning(
            "qdrant legacy collection %s failed",
            operation,
            extra={
                "collection_name": COLLECTION_NAME,
                "error_type": type(error).__name__,
            },
        )

    def _clone_collection(self, source: str, target: str) -> None:
        """Copy a local dense collection, including vectors and payloads."""

        collection = self._client.get_collection(source)
        vectors_config = collection.config.params.vectors
        if not isinstance(vectors_config, VectorParams):
            raise RuntimeError("legacy collection uses unsupported vector config")
        try:
            self._client.create_collection(
                collection_name=target,
                vectors_config=vectors_config,
            )
            offset: str | UUID | int | None = None
            while True:
                points, next_offset = self._client.scroll(
                    collection_name=source,
                    limit=10000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
                if points:
                    cloned_points = []
                    for point in points:
                        if point.vector is None or isinstance(point.vector, Mapping):
                            raise RuntimeError("legacy collection has unsupported vectors")
                        cloned_points.append(
                            PointStruct(
                                id=point.id,
                                vector=point.vector,
                                payload=point.payload or {},
                            )
                        )
                    self._client.upsert(
                        collection_name=target,
                        points=cloned_points,
                        wait=True,
                    )
                if next_offset is None:
                    break
                offset = next_offset
        except Exception:
            self._delete_collection_safely(target)
            raise

    def _restore_collection(self, backup: str, target: str) -> None:
        if self._client.collection_exists(target):
            self._client.delete_collection(collection_name=target)
        self._clone_collection(backup, target)

    @staticmethod
    def _temporary_collection_name() -> str:
        return f"{COLLECTION_NAME}__{uuid4().hex}"

    def _alias_target(self) -> str | None:
        aliases = self._client.get_aliases().aliases
        for alias in aliases:
            if alias.alias_name == COLLECTION_NAME:
                return alias.collection_name
        return None

    def _active_collection_name(self) -> str | None:
        alias_target = self._alias_target()
        if alias_target is not None:
            return alias_target
        if self._client.collection_exists(COLLECTION_NAME):
            return COLLECTION_NAME
        return None

    def _delete_collection_safely(self, collection_name: str) -> None:
        try:
            if self._client.collection_exists(collection_name):
                self._client.delete_collection(collection_name=collection_name)
        except Exception as exc:
            # Cleanup failure must not mask the write failure or compromise the
            # old active alias, but it must remain diagnosable so operators can
            # identify and reclaim an orphaned temporary collection.
            logger.warning(
                "qdrant collection cleanup failed",
                extra={
                    "collection_name": collection_name,
                    "error_type": type(exc).__name__,
                },
            )
            return

    @staticmethod
    def _manifest_point(fingerprint: IndexFingerprint) -> PointStruct:
        return PointStruct(
            id=QdrantQuestionStore._manifest_point_id(),
            vector=[0.0] * fingerprint.dimension,
            payload={
                "record_type": _MANIFEST_RECORD_TYPE,
                "manifest_key": _MANIFEST_KEY,
                "embedding_provider": fingerprint.provider,
                "embedding_model": fingerprint.model,
                "provider": fingerprint.provider,
                "model": fingerprint.model,
                "dimension": fingerprint.dimension,
                "vector_dimension": fingerprint.dimension,
                "index_version": fingerprint.index_version,
                "embedding_text_version": fingerprint.embedding_text_version,
                "question_bank_manifest_hash": fingerprint.question_bank_manifest_hash,
                "mode_policy_version": fingerprint.mode_policy_version,
            },
        )

    def _read_manifest(self, *, collection_name: str | None = None) -> IndexFingerprint | None:
        target_collection = collection_name or self._active_collection_name()
        if target_collection is None or not self._client.collection_exists(target_collection):
            return None
        points = self._client.retrieve(
            collection_name=target_collection,
            ids=[self._manifest_point_id()],
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return None
        payload = points[0].payload
        if not isinstance(payload, Mapping) or payload.get("record_type") != _MANIFEST_RECORD_TYPE:
            return None
        required_v2_fields = (
            "embedding_text_version",
            "question_bank_manifest_hash",
            "mode_policy_version",
        )
        if any(field not in payload for field in required_v2_fields):
            return None
        try:
            return IndexFingerprint.model_validate(
                {
                    "provider": payload.get("embedding_provider", payload.get("provider")),
                    "model": payload.get("embedding_model", payload.get("model")),
                    "dimension": payload.get(
                        "dimension", payload.get("vector_dimension")
                    ),
                    "index_version": payload.get("index_version", payload.get("version")),
                    "embedding_text_version": payload.get(
                        "embedding_text_version",
                        payload.get("text_version", DEFAULT_EMBEDDING_TEXT_VERSION),
                    ),
                    "question_bank_manifest_hash": payload.get(
                        "question_bank_manifest_hash",
                        payload.get("manifest_hash", DEFAULT_QUESTION_BANK_MANIFEST_HASH),
                    ),
                    "mode_policy_version": payload.get(
                        "mode_policy_version",
                        payload.get("policy_version", DEFAULT_MODE_POLICY_VERSION),
                    ),
                }
            )
        except Exception:
            return None

    def _question_points(
        self, *, collection_name: str | None = None
    ) -> dict[str, str | UUID | int]:
        target_collection = collection_name or self._active_collection_name()
        if target_collection is None:
            return {}
        result: dict[str, str | UUID | int] = {}
        offset: str | UUID | int | None = None
        while True:
            points, next_offset = self._client.scroll(
                collection_name=target_collection,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="record_type", match=MatchValue(value=_QUESTION_RECORD_TYPE)
                        )
                    ]
                ),
                limit=10000,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload
                if isinstance(payload, Mapping) and isinstance(payload.get("question_id"), str):
                    result[payload["question_id"]] = point.id
            if next_offset is None:
                break
            offset = next_offset
        return result

    @staticmethod
    def _question_point_id(question_id: str) -> UUID:
        return uuid5(_NAMESPACE, f"question:{question_id}")

    @staticmethod
    def _manifest_point_id() -> UUID:
        return uuid5(_NAMESPACE, _MANIFEST_KEY)

    @staticmethod
    def _date_from_payload(value: Any) -> date:
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("valid_until must be an ISO date") from exc
        raise ValueError("valid_until must be a date")

    @staticmethod
    def _payload_matches_intent(
        payload: Mapping[str, Any],
        intent: QuestionRetrievalIntent,
        today: date,
        *,
        candidate_safe: bool = False,
    ) -> bool:
        if payload.get("record_type") != _QUESTION_RECORD_TYPE:
            return False
        if payload.get("role") != intent.role:
            return False
        if payload.get("dimension_id") != intent.dimension_id:
            return False
        if payload.get("question_mode") != intent.question_mode:
            return False
        status = payload.get("status")
        if status != "active" and not (candidate_safe and status == "needs_review"):
            return False
        if payload.get("trust_level") not in {"medium", "high"}:
            return False
        question_id = payload.get("question_id")
        if question_id in set(intent.excluded_question_ids):
            return False
        try:
            return QdrantQuestionStore._date_from_payload(payload.get("valid_until")) >= today
        except ValueError:
            return False

    def _record_from_payload(
        self, payload: Mapping[str, Any]
    ) -> InterviewQuestionRecord | None:
        # Payload fields are only retrieval filters/trace.  A durable Qdrant
        # point is not a canonical question record; rehydrate by ID from the
        # process-local authoritative catalog and fail closed when it is not
        # available (for example after a process restart).
        question_id = payload.get("question_id")
        if not isinstance(question_id, str) or not question_id.strip():
            return None
        record = self._question_catalog.get(question_id)
        if record is None:
            return None
        try:
            validated = self._validated_question_record(record)
        except (TypeError, ValueError):
            return None
        if validated.question_id != question_id:
            return None
        return validated


__all__ = [
    "COLLECTION_NAME",
    "DeterministicFakeQuestionStore",
    "FakeQuestionStore",
    "InMemoryQuestionStore",
    "IndexFingerprint",
    "LOOPBACK_HOSTS",
    "QdrantQuestionStore",
    "QuestionStoreSearchResult",
    "is_loopback_url",
    "validate_loopback_url",
]
