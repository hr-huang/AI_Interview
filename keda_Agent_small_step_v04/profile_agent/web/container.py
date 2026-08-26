from __future__ import annotations

import os
import sqlite3
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
    QuestionRetrievalIntent,
    QuestionRetrievalResult,
)
from profile_agent.services.role_profile_service import load_role_profile
from profile_agent.web.document_ingestion import DocumentExtractor
from profile_agent.web.repository import SqliteAssessmentRepository


QuestionRetrieverFactory = Callable[[], object | None]
_UNINITIALIZED = object()


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


def _question_retriever_factory_from_env() -> QuestionRetrieverFactory | None:
    """Return a lazy local-index factory when an index path is configured."""

    index_path = (
        os.getenv("QUESTION_RAG_INDEX_PATH", "").strip()
        or os.getenv("QDRANT_QUESTION_INDEX_PATH", "").strip()
    )
    if not index_path:
        return None

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
        from profile_agent.services.question_retrieval_service import (
            QuestionRetriever,
        )
        config = resolve_embedding_config()
        embedding = SiliconFlowEmbeddingClient.from_env()
        try:
            store = QdrantQuestionStore(
                path=index_path,
                expected_fingerprint=IndexFingerprint(
                    provider=config.provider,
                    model=config.model,
                    dimension=config.dimension,
                    index_version=config.index_version,
                ),
            )
            return QuestionRetriever(
                embedding_client=embedding,
                store=store,
                owns_embedding_client=True,
                owns_store=True,
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
            checkpoint_connection=checkpoint_connection,
            interview_lock=interview_lock or RLock(),
        )

    @classmethod
    def default(
        cls,
        *,
        question_retriever_factory: QuestionRetrieverFactory | None = None,
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
            else _question_retriever_factory_from_env()
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
            checkpoint_connection=checkpoint_connection,
        )
