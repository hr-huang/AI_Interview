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
from profile_agent.services.role_profile_service import load_role_profile
from profile_agent.web.document_ingestion import DocumentExtractor
from profile_agent.web.repository import SqliteAssessmentRepository


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
            checkpoint_connection=checkpoint_connection,
            interview_lock=interview_lock or RLock(),
        )

    @classmethod
    def default(cls) -> WebContainer:
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
        try:
            interview_graph = build_interview_graph(
                checkpointer=SqliteSaver(checkpoint_connection)
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
            checkpoint_connection=checkpoint_connection,
        )
