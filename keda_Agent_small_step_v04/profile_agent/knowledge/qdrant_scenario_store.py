"""Disposable one-vector-per-ScenarioModule retrieval index."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import math
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

try:  # The offline/fake path must not require the optional Qdrant package.
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        MatchValue,
        PointStruct,
        VectorParams,
    )
except ModuleNotFoundError:  # pragma: no cover - exercised only without Qdrant installed
    QdrantClient = Any  # type: ignore[misc,assignment]
    Distance = FieldCondition = Filter = MatchValue = None  # type: ignore[assignment]

    @dataclass(frozen=True)
    class PointStruct:  # type: ignore[no-redef]
        id: str
        vector: list[float]
        payload: dict[str, Any]

    @dataclass(frozen=True)
    class VectorParams:  # type: ignore[no-redef]
        size: int
        distance: str

    class Distance:  # type: ignore[no-redef]
        COSINE = "Cosine"

from profile_agent.schemas.scenario_rag_schema import (
    ScenarioCandidate,
    ScenarioCandidateSet,
    ScenarioRetrievalRequest,
)
from profile_agent.services.scenario_bank_service import ScenarioCatalog


COLLECTION_NAME = "scenario_modules"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]+")
_POINT_NAMESPACE = uuid5(NAMESPACE_URL, "keda-profile-agent/scenario-modules")
PAYLOAD_FIELDS = frozenset(
    {
        "role_family",
        "role_profile_version",
        "retrieval_unit_id",
        "scenario_id",
        "module_id",
        "primary_dimension_id",
        "supported_modes",
        "supported_requirement_types",
        "difficulties",
        "status",
        "valid_from",
        "valid_until",
        "version",
    }
)


def _tokens(value: str) -> list[str]:
    result: list[str] = []
    for chunk in _TOKEN_RE.findall(value.casefold()):
        result.append(chunk)
        if any("\u3400" <= char <= "\u9fff" for char in chunk):
            result.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return result


def _bm25(query: str, documents: Mapping[str, str]) -> dict[str, float]:
    query_terms = _tokens(query)
    tokenized = {key: _tokens(value) for key, value in documents.items()}
    if not query_terms or not tokenized:
        return {}
    average_length = sum(len(value) for value in tokenized.values()) / len(tokenized)
    document_frequency: Counter[str] = Counter()
    for values in tokenized.values():
        document_frequency.update(set(values))
    scores: dict[str, float] = {}
    for key, values in tokenized.items():
        frequencies = Counter(values)
        score = 0.0
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            df = document_frequency[term]
            inverse_frequency = math.log(1.0 + (len(tokenized) - df + 0.5) / (df + 0.5))
            denominator = frequency + 1.5 * (1.0 - 0.75 + 0.75 * len(values) / max(1.0, average_length))
            score += inverse_frequency * frequency * 2.5 / denominator
        if score > 0:
            scores[key] = score
    return scores


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


def _vector(value: Sequence[float]) -> list[float]:
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError("vector must be a numeric sequence")
    values = list(value)
    if not values or any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)) for item in values):
        raise ValueError("vector must contain finite numeric values")
    return [float(item) for item in values]


@dataclass(frozen=True)
class _Point:
    retrieval_unit_id: str
    payload: dict[str, Any]
    semantic_text: str
    vector: list[float]


@dataclass(frozen=True)
class _RankRow:
    """Immutable ranking row retaining every score component."""

    retrieval_unit_id: str
    dense_score: float
    lexical_score: float
    hybrid_score: float
    raw_reranker_score: float | None
    normalized_reranker_score: float | None
    final_score: float


class QdrantScenarioStore:
    """Small scenario-module index with an optional injected Qdrant client."""

    def __init__(
        self,
        *,
        embedding: Any | None = None,
        embedding_client: Any | None = None,
        client: QdrantClient | None = None,
        collection_name: str = COLLECTION_NAME,
    ) -> None:
        if embedding is not None and embedding_client is not None:
            raise ValueError("pass only one of embedding and embedding_client")
        if collection_name != COLLECTION_NAME:
            raise ValueError(f"collection_name must be {COLLECTION_NAME!r}")
        self._embedding = embedding if embedding is not None else embedding_client
        self._client = client
        self._points: dict[str, _Point] = {}
        self._semantic_text: dict[str, str] = {}
        self._catalog: ScenarioCatalog | None = None
        self._dimension: int | None = None
        self._closed = False

    @property
    def payloads(self) -> dict[str, dict[str, Any]]:
        return {key: dict(point.payload) for key, point in self._points.items()}

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._points))

    def close(self) -> None:
        self._closed = True

    def load_catalog(self, catalog: ScenarioCatalog) -> None:
        """Load canonical metadata/text for querying an existing index."""

        if not isinstance(catalog, ScenarioCatalog):
            raise TypeError("catalog must be ScenarioCatalog")
        self._catalog = catalog
        self._semantic_text = {
            module.retrieval_unit_id: module.semantic_text
            for module in catalog.active_modules
        }

    def rebuild(self, catalog: ScenarioCatalog) -> None:
        if not isinstance(catalog, ScenarioCatalog):
            raise TypeError("catalog must be ScenarioCatalog")
        self.load_catalog(catalog)
        modules = sorted(catalog.active_modules, key=lambda item: item.module_id)
        if not modules:
            raise ValueError("catalog has no active scenario modules")
        if self._embedding is None or not callable(getattr(self._embedding, "embed", None)):
            raise ValueError("embedding client is required to rebuild scenario index")
        raw_vectors = self._embedding.embed([module.semantic_text for module in modules])
        vectors = list(raw_vectors)
        if len(vectors) != len(modules):
            raise ValueError("embedding count does not match active modules")
        normalized = [_vector(vector) for vector in vectors]
        dimension = len(normalized[0])
        if any(len(vector) != dimension for vector in normalized):
            raise ValueError("embedding vectors must share one dimension")
        points: dict[str, _Point] = {}
        for module, vector in zip(modules, normalized):
            payload = {
                "role_family": module.role_family,
                "role_profile_version": module.role_profile_version,
                "retrieval_unit_id": module.retrieval_unit_id,
                "scenario_id": module.scenario_id,
                "module_id": module.module_id,
                "primary_dimension_id": module.primary_dimension_id,
                "supported_modes": list(module.supported_modes),
                "supported_requirement_types": list(module.supported_requirement_types),
                "difficulties": list(module.difficulties),
                "status": module.status,
                "valid_from": module.valid_from.isoformat(),
                "valid_until": module.valid_until.isoformat() if module.valid_until else None,
                "version": module.version,
            }
            points[module.retrieval_unit_id] = _Point(
                retrieval_unit_id=module.retrieval_unit_id,
                payload=payload,
                semantic_text=module.semantic_text,
                vector=vector,
            )
        self._points = points
        self._dimension = dimension
        self._closed = False
        if self._client is not None:
            self._write_qdrant(points, dimension)

    def _write_qdrant(self, points: Mapping[str, _Point], dimension: int) -> None:
        if self._client.collection_exists(COLLECTION_NAME):
            self._client.delete_collection(collection_name=COLLECTION_NAME)
        self._client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
        )
        self._client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                PointStruct(
                    id=str(uuid5(_POINT_NAMESPACE, retrieval_unit_id)),
                    vector=point.vector,
                    payload=point.payload,
                )
                for retrieval_unit_id, point in points.items()
            ],
            wait=True,
        )

    def search(
        self,
        request: ScenarioRetrievalRequest,
        *,
        as_of: date,
        limit: int = 3,
        reranker: Any | None = None,
    ) -> ScenarioCandidateSet:
        request = ScenarioRetrievalRequest.model_validate(request)
        if not isinstance(as_of, date):
            raise TypeError("as_of must be a date")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if self._closed or self._embedding is None or (self._dimension is None and self._client is None):
            return ScenarioCandidateSet(status="unavailable")
        try:
            query_vectors = list(self._embedding.embed([request.semantic_query]))
            if len(query_vectors) != 1:
                raise ValueError
            query_vector = _vector(query_vectors[0])
            if self._dimension is not None and len(query_vector) != self._dimension:
                raise ValueError
        except Exception:
            return ScenarioCandidateSet(status="index_mismatch")

        if self._client is not None:
            return self._search_qdrant(
                request,
                query_vector,
                as_of=as_of,
                limit=limit,
                reranker=reranker,
            )

        candidates = [point for point in self._points.values() if self._hard_match(point.payload, request, as_of)]
        if not candidates:
            return ScenarioCandidateSet(status="no_match", hard_filter=self._hard_filter(request, as_of))
        dense = {point.retrieval_unit_id: max(0.0, _cosine(query_vector, point.vector)) for point in candidates}
        return self._rank_points(
            candidates,
            dense,
            request,
            as_of=as_of,
            limit=limit,
            reranker=reranker,
        )

    def _rank_points(
        self,
        candidates: Sequence[_Point],
        dense: Mapping[str, float],
        request: ScenarioRetrievalRequest,
        *,
        as_of: date,
        limit: int,
        reranker: Any | None,
    ) -> ScenarioCandidateSet:
        lexical = _bm25(request.semantic_query, {point.retrieval_unit_id: point.semantic_text for point in candidates})
        max_lexical = max(lexical.values(), default=0.0)
        ranked: list[_RankRow] = []
        for point in candidates:
            lexical_score = lexical.get(point.retrieval_unit_id, 0.0) / max_lexical if max_lexical else 0.0
            dense_score = dense[point.retrieval_unit_id]
            hybrid_score = 0.7 * dense_score + 0.3 * lexical_score
            ranked.append(
                _RankRow(
                    retrieval_unit_id=point.retrieval_unit_id,
                    dense_score=dense_score,
                    lexical_score=lexical_score,
                    hybrid_score=hybrid_score,
                    raw_reranker_score=None,
                    normalized_reranker_score=None,
                    final_score=hybrid_score,
                )
            )
        if reranker is not None and callable(getattr(reranker, "rerank", None)):
            documents = [point.semantic_text for point in candidates]
            try:
                rerank_scores = list(reranker.rerank(request.semantic_query, documents))
                if len(rerank_scores) != len(candidates):
                    raise ValueError
                rerank_values = [float(value) for value in rerank_scores]
                if any(not math.isfinite(value) for value in rerank_values):
                    raise ValueError
                maximum = max(rerank_values, default=0.0)
                minimum = min(rerank_values, default=0.0)
                span = maximum - minimum
                if not math.isfinite(span):
                    raise ValueError
                normalized_values = [
                    (raw_score - minimum) / span if span else 0.0
                    for raw_score in rerank_values
                ]
                if any(not math.isfinite(value) for value in normalized_values):
                    raise ValueError
                final_values = [
                    0.6 * row.hybrid_score + 0.4 * normalized_score
                    for row, normalized_score in zip(ranked, normalized_values)
                ]
                if any(not math.isfinite(value) for value in final_values):
                    raise ValueError
                ranked = [
                    _RankRow(
                        retrieval_unit_id=row.retrieval_unit_id,
                        dense_score=row.dense_score,
                        lexical_score=row.lexical_score,
                        hybrid_score=row.hybrid_score,
                        raw_reranker_score=raw_score,
                        normalized_reranker_score=normalized_score,
                        final_score=final_score,
                    )
                    for row, raw_score, normalized_score, final_score in zip(
                        ranked,
                        rerank_values,
                        normalized_values,
                        final_values,
                    )
                ]
            except Exception:
                pass
        ranked.sort(key=lambda item: (-item.final_score, item.retrieval_unit_id))
        hits = [
            ScenarioCandidate(
                retrieval_unit_id=row.retrieval_unit_id,
                score=row.final_score,
                dense_score=row.dense_score,
                lexical_score=row.lexical_score,
                raw_reranker_score=row.raw_reranker_score,
                normalized_reranker_score=row.normalized_reranker_score,
            )
            for row in ranked[:limit]
        ]
        top1_margin = None
        if len(hits) >= 2 and hits[0].score is not None and hits[1].score is not None:
            top1_margin = hits[0].score - hits[1].score
        return ScenarioCandidateSet(
            status="hit",
            candidates=hits,
            index_version="scenario-modules-v1",
            top1_margin=top1_margin,
            hard_filter=self._hard_filter(request, as_of),
        )

    def _search_qdrant(
        self,
        request: ScenarioRetrievalRequest,
        query_vector: Sequence[float],
        *,
        as_of: date,
        limit: int,
        reranker: Any | None,
    ) -> ScenarioCandidateSet:
        """Query the injected Qdrant client, then rank using canonical text."""

        query_filter = self._qdrant_filter(request)
        try:
            if callable(getattr(self._client, "query_points", None)):
                response = self._client.query_points(
                    collection_name=COLLECTION_NAME,
                    query=list(query_vector),
                    query_filter=query_filter,
                    limit=max(limit, 10),
                    with_payload=True,
                )
            elif callable(getattr(self._client, "search", None)):
                response = self._client.search(
                    collection_name=COLLECTION_NAME,
                    query_vector=list(query_vector),
                    query_filter=query_filter,
                    limit=max(limit, 10),
                    with_payload=True,
                )
            else:
                return ScenarioCandidateSet(status="unavailable")
        except Exception:
            return ScenarioCandidateSet(status="unavailable")

        raw_points = getattr(response, "points", response)
        candidates: list[_Point] = []
        dense: dict[str, float] = {}
        for raw_point in raw_points or []:
            payload = getattr(raw_point, "payload", None)
            if payload is None and isinstance(raw_point, Mapping):
                payload = raw_point.get("payload")
            if not isinstance(payload, Mapping):
                continue
            retrieval_unit_id = payload.get("retrieval_unit_id")
            if not isinstance(retrieval_unit_id, str) or not retrieval_unit_id:
                continue
            if not self._hard_match(payload, request, as_of):
                continue
            semantic_text = self._semantic_text.get(retrieval_unit_id)
            if not semantic_text:
                continue
            score = getattr(raw_point, "score", None)
            if score is None and isinstance(raw_point, Mapping):
                score = raw_point.get("score")
            try:
                score = float(score)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(score):
                continue
            candidates.append(
                _Point(
                    retrieval_unit_id=retrieval_unit_id,
                    payload=dict(payload),
                    semantic_text=semantic_text,
                    vector=[],
                )
            )
            dense[retrieval_unit_id] = max(0.0, score)
        if not candidates:
            return ScenarioCandidateSet(status="no_match", hard_filter=self._hard_filter(request, as_of))
        return self._rank_points(
            candidates,
            dense,
            request,
            as_of=as_of,
            limit=limit,
            reranker=reranker,
        )

    @staticmethod
    def _qdrant_filter(request: ScenarioRetrievalRequest) -> object:
        if Filter is None or FieldCondition is None or MatchValue is None:
            return {
                "must": [
                    {"key": "role_family", "match": request.role_family},
                    {"key": "role_profile_version", "match": request.role_profile_version},
                    {"key": "primary_dimension_id", "match": request.primary_dimension_id},
                    {"key": "supported_modes", "match": request.question_mode},
                    {"key": "supported_requirement_types", "match": request.requirement_type},
                    {"key": "difficulties", "match": request.difficulty},
                    {"key": "status", "match": "active"},
                ]
            }
        return Filter(
            must=[
                FieldCondition(key="role_family", match=MatchValue(value=request.role_family)),
                FieldCondition(key="role_profile_version", match=MatchValue(value=request.role_profile_version)),
                FieldCondition(key="primary_dimension_id", match=MatchValue(value=request.primary_dimension_id)),
                FieldCondition(key="supported_modes", match=MatchValue(value=request.question_mode)),
                FieldCondition(key="supported_requirement_types", match=MatchValue(value=request.requirement_type)),
                FieldCondition(key="difficulties", match=MatchValue(value=request.difficulty)),
                FieldCondition(key="status", match=MatchValue(value="active")),
            ]
        )

    @staticmethod
    def _hard_match(payload: Mapping[str, Any], request: ScenarioRetrievalRequest, as_of: date) -> bool:
        valid_from = date.fromisoformat(payload["valid_from"])
        valid_until = date.fromisoformat(payload["valid_until"]) if payload.get("valid_until") else None
        return (
            payload.get("role_family") == request.role_family
            and payload.get("role_profile_version") == request.role_profile_version
            and payload.get("primary_dimension_id") == request.primary_dimension_id
            and request.question_mode in payload.get("supported_modes", [])
            and request.requirement_type in payload.get("supported_requirement_types", [])
            and request.difficulty in payload.get("difficulties", [])
            and payload.get("status") == "active"
            and valid_from <= as_of
            and (valid_until is None or as_of <= valid_until)
            and payload.get("retrieval_unit_id") not in request.excluded_retrieval_unit_ids
            and payload.get("scenario_id") not in request.excluded_scenario_ids
        )

    @staticmethod
    def _hard_filter(request: ScenarioRetrievalRequest, as_of: date) -> dict[str, Any]:
        return {
            "role_family": request.role_family,
            "role_profile_version": request.role_profile_version,
            "primary_dimension_id": request.primary_dimension_id,
            "question_mode": request.question_mode,
            "requirement_type": request.requirement_type,
            "difficulty": request.difficulty,
            "status": "active",
            "as_of": as_of.isoformat(),
            "excluded_retrieval_unit_ids": list(request.excluded_retrieval_unit_ids),
            "excluded_scenario_ids": list(request.excluded_scenario_ids),
        }


__all__ = ["COLLECTION_NAME", "PAYLOAD_FIELDS", "QdrantScenarioStore"]
