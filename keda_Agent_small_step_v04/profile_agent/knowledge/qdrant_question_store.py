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
    QuestionRetrievalIntent,
    RetrievedQuestion,
)


COLLECTION_NAME = "interview_questions"
_MANIFEST_RECORD_TYPE = "manifest"
_QUESTION_RECORD_TYPE = "question"
_MANIFEST_KEY = "__interview_question_index_manifest__"
_NAMESPACE = uuid5(NAMESPACE_URL, "keda-profile-agent/interview-questions")
_SEARCH_STATUSES = Literal["hit", "no_match", "unavailable", "index_mismatch"]
logger = logging.getLogger(__name__)


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
    dimension: int = Field(gt=0)
    index_version: str = Field(
        min_length=1,
        validation_alias=AliasChoices("index_version", "version"),
    )

    @field_validator("provider", "model", "index_version")
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


class QdrantQuestionStore:
    """A disposable Qdrant-backed index with strict retrieval filters.

    ``path=':memory:'`` is the default so constructing this class is local and
    side-effect free.  A caller may inject a local ``QdrantClient`` or provide
    a persistence path.  The fingerprint is required for every reader and
    writer; a manifest created with another fingerprint is never queried.
    """

    def __init__(
        self,
        *,
        path: str | Path = ":memory:",
        client: QdrantClient | None = None,
        collection_name: str = COLLECTION_NAME,
        fingerprint: IndexFingerprint | Mapping[str, Any] | None = None,
        expected_fingerprint: IndexFingerprint | Mapping[str, Any] | None = None,
    ) -> None:
        if fingerprint is not None and expected_fingerprint is not None:
            raise ValueError("pass only one of fingerprint and expected_fingerprint")
        if collection_name != COLLECTION_NAME:
            raise ValueError(f"collection_name must be {COLLECTION_NAME!r}")
        configured_fingerprint = self._coerce_fingerprint(
            expected_fingerprint if expected_fingerprint is not None else fingerprint
        )
        if configured_fingerprint is None:
            raise ValueError("fingerprint is required")
        self.collection_name = COLLECTION_NAME
        self._expected_fingerprint = configured_fingerprint
        self._owns_client = client is None
        self._client = client if client is not None else QdrantClient(path=str(path))

    @property
    def client(self) -> QdrantClient:
        """Expose the injected/local client for diagnostics and local tests."""

        return self._client

    @property
    def fingerprint(self) -> IndexFingerprint:
        return self._expected_fingerprint

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
        self._assert_expected_fingerprint(normalized_fingerprint)
        prepared = self._prepare_records(records, vectors, normalized_fingerprint)
        self._write_collection(prepared, normalized_fingerprint)

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
                return QuestionStoreSearchResult(status="unavailable", hits=[])
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
                FieldCondition(key="status", match=MatchValue(value="active")),
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
            for point in response.points:
                payload = point.payload if isinstance(point.payload, Mapping) else {}
                if payload.get("record_type") != _QUESTION_RECORD_TYPE:
                    continue
                if not self._payload_matches_intent(payload, intent, today):
                    continue
                record = self._record_from_payload(payload)
                if record is None:
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
                    status="no_match",
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
                }
            )
        return prepared

    @staticmethod
    def _record_to_payload(
        record: InterviewQuestionRecord | Mapping[str, Any],
    ) -> dict[str, Any]:
        if isinstance(record, InterviewQuestionRecord):
            payload = record.model_dump(mode="json")
        elif isinstance(record, Mapping):
            try:
                payload = InterviewQuestionRecord.model_validate(record).model_dump(
                    mode="json"
                )
            except Exception as exc:
                raise ValueError("invalid interview question record") from exc
        else:
            raise TypeError(
                "records must be InterviewQuestionRecord instances or mappings"
            )
        if not isinstance(payload, dict):
            raise ValueError("invalid interview question record")
        return dict(payload)

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
        elif self._client.collection_exists(COLLECTION_NAME):
            self._migrate_legacy_collection(new_collection)
        else:
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

        if old_collection and old_collection not in {COLLECTION_NAME, new_collection}:
            self._delete_collection_safely(old_collection)

    def _migrate_legacy_collection(self, new_collection: str) -> None:
        """Switch a pre-alias collection without losing its active snapshot."""

        backup_collection = self._temporary_collection_name()
        restore_failed = False
        self._clone_collection(COLLECTION_NAME, backup_collection)
        try:
            # The complete copy is now a rollback point.  Only after it is
            # ready do we touch the legacy fixed-name collection.
            self._client.delete_collection(collection_name=COLLECTION_NAME)
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
                try:
                    self._restore_collection(
                        backup_collection,
                        COLLECTION_NAME,
                    )
                except Exception as restore_error:
                    restore_failed = True
                    logger.warning(
                        "qdrant legacy collection restore failed",
                        extra={
                            "collection_name": COLLECTION_NAME,
                            "error_type": type(restore_error).__name__,
                        },
                    )
                raise
        finally:
            if not restore_failed:
                self._delete_collection_safely(backup_collection)

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
                "index_version": fingerprint.index_version,
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
        try:
            return IndexFingerprint.model_validate(
                {
                    "provider": payload.get("embedding_provider", payload.get("provider")),
                    "model": payload.get("embedding_model", payload.get("model")),
                    "dimension": payload.get("dimension"),
                    "index_version": payload.get("index_version", payload.get("version")),
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
        payload: Mapping[str, Any], intent: QuestionRetrievalIntent, today: date
    ) -> bool:
        if payload.get("record_type") != _QUESTION_RECORD_TYPE:
            return False
        if payload.get("role") != intent.role:
            return False
        if payload.get("dimension_id") != intent.dimension_id:
            return False
        if payload.get("question_mode") != intent.question_mode:
            return False
        if payload.get("status") != "active":
            return False
        question_id = payload.get("question_id")
        if question_id in set(intent.excluded_question_ids):
            return False
        try:
            return QdrantQuestionStore._date_from_payload(payload.get("valid_until")) >= today
        except ValueError:
            return False

    @staticmethod
    def _record_from_payload(payload: Mapping[str, Any]) -> InterviewQuestionRecord | None:
        record_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"record_type", "valid_until_epoch"}
        }
        try:
            return InterviewQuestionRecord.model_validate(record_payload)
        except Exception:
            return None


__all__ = [
    "COLLECTION_NAME",
    "IndexFingerprint",
    "QdrantQuestionStore",
    "QuestionStoreSearchResult",
]
