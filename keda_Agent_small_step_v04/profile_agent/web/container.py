from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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

    @classmethod
    def for_test(
        cls,
        *,
        repository: SqliteAssessmentRepository,
        pre_interview_graph: object,
        dispatcher: object,
        document_extractor: DocumentExtractor | None = None,
        role_profile: RoleCompetencyProfile | None = None,
    ) -> WebContainer:
        return cls(
            repository=repository,
            pre_interview_graph=pre_interview_graph,
            document_extractor=document_extractor or DocumentExtractor(),
            dispatcher=dispatcher,
            role_profile=role_profile
            or load_role_profile("ai_application_engineering", "2026-H2"),
        )

    @classmethod
    def default(cls) -> WebContainer:
        database_path = Path(
            os.getenv("WEB_DATABASE_PATH", "data/web.sqlite3")
        )
        return cls(
            repository=SqliteAssessmentRepository(database_path),
            pre_interview_graph=pre_interview_draft_graph,
            document_extractor=DocumentExtractor(),
            dispatcher=ThreadPoolDispatcher(),
            role_profile=load_role_profile(
                "ai_application_engineering",
                "2026-H2",
            ),
        )
