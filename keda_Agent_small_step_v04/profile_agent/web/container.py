from __future__ import annotations

import os
import json
import sqlite3
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from langgraph.checkpoint.sqlite import SqliteSaver

from profile_agent.graphs.interview import build_interview_graph
from profile_agent.graphs.pre_interview import pre_interview_draft_graph
from profile_agent.schemas.report_schema import RoleCompetencyProfile
from profile_agent.schemas.question_rag_schema import (
    QuestionBankManifest,
    QuestionModePolicy,
    QuestionRetrievalIntent,
    QuestionRetrievalResult,
)
from profile_agent.services.role_profile_service import load_role_profile
from profile_agent.services.question_bank_service import (
    EMBEDDING_TEXT_VERSION,
    UNAVAILABLE_QUESTION_BANK_MANIFEST_HASH,
    load_question_bank,
    load_question_bank_runtime_identity,
)
from profile_agent.web.document_ingestion import DocumentExtractor
from profile_agent.web.repository import SqliteAssessmentRepository


QuestionRetrieverFactory = Callable[[], object | None]
_UNINITIALIZED = object()

_QUESTION_BANK_ENV_NAMES: tuple[str, ...] = (
    "QUESTION_RAG_QUESTION_BANK_PATH",
    "QUESTION_RAG_QUESTION_BANK",
    "QUESTION_RAG_CANONICAL_QUESTION_BANK_PATH",
    "QUESTION_RAG_CANONICAL_BANK_PATH",
    "QUESTION_RAG_BANK_PATH",
    "QUESTION_RAG_BANK",
    "QUESTION_BANK_PATH",
    "QUESTION_BANK",
    "QUESTION_RAG_CORPUS_DIR",
)


class LazyQuestionRetriever:
    """Construct the optional question retriever only on the first lookup."""

    def __init__(self, factory: QuestionRetrieverFactory) -> None:
        if not callable(factory):
            raise TypeError("question_retriever_factory must be callable")
        self._factory = factory
        self._retriever: object = _UNINITIALIZED
        self._closed = False
        self._lock = RLock()

    def _get_retriever(self) -> object | None:
        with self._lock:
            if self._closed:
                return None
            if self._retriever is _UNINITIALIZED:
                try:
                    self._retriever = self._factory()
                except Exception:
                    # Construction failures are an optional-provider miss;
                    # never echo provider configuration or exception details.
                    self._retriever = None
            return self._retriever

    def retrieve(self, intent: QuestionRetrievalIntent) -> QuestionRetrievalResult:
        retriever = self._get_retriever()
        if retriever is None:
            return QuestionRetrievalResult(status="unavailable")

        try:
            retrieve = getattr(retriever, "retrieve", None)
            if not callable(retrieve):
                return QuestionRetrievalResult(status="unavailable")
            return QuestionRetrievalResult.model_validate(retrieve(intent))
        except Exception:
            # The graph has an additional failure boundary; keeping this
            # adapter safe also protects direct container callers.
            return QuestionRetrievalResult(status="unavailable")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            retriever = self._retriever
            self._retriever = None
        if retriever is _UNINITIALIZED or retriever is None:
            return
        close = getattr(retriever, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _configured_question_bank_path() -> Path | None:
    """Resolve the local canonical bank path without touching the network."""

    for name in _QUESTION_BANK_ENV_NAMES:
        value = os.getenv(name, "").strip()
        if not value:
            continue
        path = Path(value)
        if path.is_dir():
            path = path / "questions.json"
        return path
    packaged_path = (
        Path(__file__).resolve().parents[1]
        / "knowledge"
        / "question_banks"
        / "ai_agent_engineer_2026_h2"
        / "questions.json"
    )
    try:
        if packaged_path.is_file():
            # A draft/review-only bank must not silently become a runtime
            # authoritative catalog before its human release gate passes.
            payload = json.loads(packaged_path.read_text(encoding="utf-8"))
            if all(item.get("status") == "active" for item in payload.get("questions", [])):
                return packaged_path
    except OSError:
        pass
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return None


def _question_retriever_factory_from_env(
    *,
    question_mode_policy: QuestionModePolicy | Any | None = None,
    question_bank_manifest: QuestionBankManifest | Any | None = None,
) -> QuestionRetrieverFactory | None:
    """Return a lazy local-index factory when an index path is configured."""

    index_path = (
        os.getenv("QUESTION_RAG_INDEX_PATH", "").strip()
        or os.getenv("QDRANT_QUESTION_INDEX_PATH", "").strip()
    )
    if not index_path:
        return None
    question_bank_path = _configured_question_bank_path()

    def factory() -> object:
        # Keep optional dependencies and all provider/client construction out
        # of WebContainer.default().  This factory runs only on first lookup.
        from profile_agent.knowledge.qdrant_question_store import (
            IndexFingerprint,
            QdrantQuestionStore,
        )
        from profile_agent.services.siliconflow_embedding_service import (
            SiliconFlowEmbeddingClient,
            resolve_embedding_config,
        )
        from profile_agent.services.siliconflow_rerank_service import (
            SiliconFlowRerankClient,
        )
        from profile_agent.services.question_retrieval_service import (
            QuestionRetriever,
        )
        if question_bank_path is None:
            # A durable index without its authoritative bank cannot produce a
            # canonical RetrievedQuestion.  Keep the reader constructible so
            # the graph remains askable, but use an explicit unavailable
            # identity; any persisted index with another hash becomes an
            # honest index_mismatch rather than a fabricated hit.
            catalog: dict[str, object] = {}
            manifest_hash = UNAVAILABLE_QUESTION_BANK_MANIFEST_HASH
            runtime_policy = (
                QuestionModePolicy.default()
                if question_mode_policy is None
                else QuestionModePolicy.model_validate(question_mode_policy)
            )
        else:
            records = load_question_bank(question_bank_path)
            runtime_identity = load_question_bank_runtime_identity(
                records,
                bank_path=question_bank_path,
                environment=os.environ,
                policy=question_mode_policy,
            )
            catalog = runtime_identity.catalog
            manifest_hash = runtime_identity.manifest_hash
            runtime_policy = runtime_identity.policy
            loaded_bank_manifest = runtime_identity.bank_manifest
        if question_bank_path is None:
            loaded_bank_manifest = None
        config = resolve_embedding_config()
        embedding = SiliconFlowEmbeddingClient.from_env()
        reranker = None
        try:
            # Reranking is an optional quality layer.  A missing reranker key
            # must not make the local question index unavailable; retrieval can
            # still use the hybrid dense/lexical candidates safely.
            if os.getenv("SILICONFLOW_API_KEY", "").strip():
                reranker = SiliconFlowRerankClient.from_env()
            store = QdrantQuestionStore(
                path=index_path,
                expected_fingerprint=IndexFingerprint(
                    provider=config.provider,
                    model=config.model,
                    dimension=config.dimension,
                    index_version=config.index_version,
                    embedding_text_version=EMBEDDING_TEXT_VERSION,
                    question_bank_manifest_hash=manifest_hash,
                    mode_policy_version=runtime_policy.mode_policy_version,
                ),
                authoritative_catalog=catalog,
            )
            return QuestionRetriever(
                embedding_client=embedding,
                store=store,
                owns_embedding_client=True,
                owns_store=True,
                reranker_client=reranker,
                owns_reranker_client=True,
                rerank_threshold=float(
                    os.getenv("QUESTION_RAG_RERANK_THRESHOLD", "0.2")
                ),
                question_mode_policy=runtime_policy,
                question_bank_manifest=(
                    question_bank_manifest
                    if question_bank_manifest is not None
                    else loaded_bank_manifest
                ),
            )
        except Exception:
            # The embedding client is owned by the retriever we intended to
            # return; release it if later construction cannot complete.
            close = getattr(embedding, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            close_reranker = getattr(reranker, "close", None)
            if callable(close_reranker):
                try:
                    close_reranker()
                except Exception:
                    pass
            raise

    return factory


class ThreadPoolDispatcher:
    """Production dispatcher; tests inject a synchronous equivalent."""

    def __init__(self, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="assessment",
        )

    def submit(self, function: Callable[..., Any], *args: Any) -> None:
        self._executor.submit(function, *args)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


@dataclass
class WebContainer:
    repository: SqliteAssessmentRepository
    pre_interview_graph: object
    document_extractor: DocumentExtractor
    dispatcher: object
    role_profile: RoleCompetencyProfile
    interview_graph: object | None = None
    checkpoint_connection: sqlite3.Connection | None = None
    interview_lock: RLock = field(default_factory=RLock)
    # Optional runtime retriever.  ``None`` intentionally means an unavailable
    # RAG provider; graph construction must remain free of HTTP/client setup.
    # Keep this after every pre-existing field so direct positional construction
    # remains compatible with the original WebContainer dataclass.
    question_retriever: object | None = None
    question_mode_policy: QuestionModePolicy | None = field(
        default=None,
        kw_only=True,
    )
    question_bank_manifest: QuestionBankManifest | None = field(
        default=None,
        kw_only=True,
    )

    @classmethod
    def for_test(
        cls,
        *,
        repository: SqliteAssessmentRepository,
        pre_interview_graph: object,
        dispatcher: object,
        document_extractor: DocumentExtractor | None = None,
        role_profile: RoleCompetencyProfile | None = None,
        interview_graph: object | None = None,
        question_retriever: object | None = None,
        question_mode_policy: QuestionModePolicy | None = None,
        question_bank_manifest: QuestionBankManifest | None = None,
        checkpoint_connection: sqlite3.Connection | None = None,
        interview_lock: RLock | None = None,
    ) -> WebContainer:
        return cls(
            repository=repository,
            pre_interview_graph=pre_interview_graph,
            document_extractor=document_extractor or DocumentExtractor(),
            dispatcher=dispatcher,
            role_profile=role_profile
            or load_role_profile("ai_application_engineering", "2026-H2"),
            interview_graph=interview_graph,
            question_retriever=question_retriever,
            question_mode_policy=question_mode_policy,
            question_bank_manifest=question_bank_manifest,
            checkpoint_connection=checkpoint_connection,
            interview_lock=interview_lock or RLock(),
        )

    @classmethod
    def default(
        cls,
        *,
        question_retriever_factory: QuestionRetrieverFactory | None = None,
        question_mode_policy: QuestionModePolicy | None = None,
        question_bank_manifest: QuestionBankManifest | None = None,
    ) -> WebContainer:
        database_path = Path(
            os.getenv("WEB_DATABASE_PATH", "data/web.sqlite3")
        )
        checkpoint_path = Path(
            os.getenv(
                "WEB_CHECKPOINT_PATH",
                str(database_path.with_name("checkpoints.sqlite3")),
            )
        )
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_connection = sqlite3.connect(
            checkpoint_path,
            check_same_thread=False,
        )
        configured_factory = (
            question_retriever_factory
            if question_retriever_factory is not None
            else _question_retriever_factory_from_env(
                question_mode_policy=question_mode_policy,
                question_bank_manifest=question_bank_manifest,
            )
        )
        question_retriever = (
            LazyQuestionRetriever(configured_factory)
            if configured_factory is not None
            else None
        )
        try:
            interview_graph = build_interview_graph(
                checkpointer=SqliteSaver(checkpoint_connection),
                question_retriever=question_retriever,
                question_mode_policy=question_mode_policy,
                question_bank_manifest=question_bank_manifest,
            )
        except Exception:
            checkpoint_connection.close()
            raise
        return cls(
            repository=SqliteAssessmentRepository(database_path),
            pre_interview_graph=pre_interview_draft_graph,
            document_extractor=DocumentExtractor(),
            dispatcher=ThreadPoolDispatcher(),
            role_profile=load_role_profile(
                "ai_application_engineering",
                "2026-H2",
            ),
            interview_graph=interview_graph,
            question_retriever=question_retriever,
            question_mode_policy=question_mode_policy,
            question_bank_manifest=question_bank_manifest,
            checkpoint_connection=checkpoint_connection,
        )
