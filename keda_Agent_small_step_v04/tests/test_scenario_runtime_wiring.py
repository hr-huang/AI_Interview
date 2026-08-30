from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from profile_agent.schemas.scenario_rag_schema import ScenarioRetrievalRequest
from profile_agent.services.scenario_bank_service import ScenarioCatalog
from profile_agent.web.container import LazyScenarioRetriever, WebContainer


class _CloseSpy:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class ScenarioRuntimeWiringTests(TestCase):
    def request(self) -> ScenarioRetrievalRequest:
        return ScenarioRetrievalRequest(
            primary_dimension_id="role_dim_01",
            requirement_type="problem_solving",
            question_mode="scenario",
            difficulty="intermediate",
            objective="验证 Agent 架构与失败恢复",
        )

    def test_lazy_retriever_does_not_construct_until_first_retrieval(self) -> None:
        catalog = ScenarioCatalog.load()
        calls: list[str] = []

        class Retriever:
            def retrieve(self, request, *, as_of):
                calls.append("retrieved")
                raise RuntimeError("provider unavailable")

        lazy = LazyScenarioRetriever(
            lambda: calls.append("constructed") or Retriever(),
            catalog=catalog,
        )

        self.assertEqual(calls, [])
        result = lazy.retrieve(self.request(), as_of=date(2026, 8, 29))

        self.assertEqual(calls, ["constructed", "retrieved"])
        self.assertEqual(result.status, "fallback")
        self.assertTrue(result.fallback_reason)

    def test_lazy_retriever_closes_initialized_resource_once(self) -> None:
        catalog = ScenarioCatalog.load()
        resource = _CloseSpy()

        class Retriever(_CloseSpy):
            def retrieve(self, request, *, as_of):
                raise RuntimeError("provider unavailable")

        retriever = Retriever()
        lazy = LazyScenarioRetriever(lambda: retriever, catalog=catalog)
        lazy.retrieve(self.request(), as_of=date(2026, 8, 29))
        lazy.close()
        lazy.close()

        self.assertEqual(retriever.close_calls, 1)
        self.assertEqual(resource.close_calls, 0)

    def test_default_container_loads_catalog_and_keeps_scenario_provider_lazy(self) -> None:
        captured: dict[str, object] = {}
        factory_calls: list[str] = []

        def build_graph(**kwargs):
            captured.update(kwargs)
            return object()

        with TemporaryDirectory() as root:
            env = {
                "WEB_DATABASE_PATH": str(Path(root) / "web.sqlite3"),
                "WEB_CHECKPOINT_PATH": str(Path(root) / "checkpoints.sqlite3"),
                "SCENARIO_RAG_INDEX_PATH": str(Path(root) / "scenario_index"),
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch("profile_agent.web.container.build_interview_graph", side_effect=build_graph),
            ):
                container = WebContainer.default(
                    scenario_retriever_factory=lambda: factory_calls.append("constructed") or None,
                )
                try:
                    self.assertIsInstance(container.scenario_catalog, ScenarioCatalog)
                    self.assertIs(captured["scenario_catalog"], container.scenario_catalog)
                    self.assertIs(captured["scenario_retriever"], container.scenario_retriever)
                    self.assertEqual(factory_calls, [])
                finally:
                    container.scenario_retriever.close()
                    container.repository.close()
                    container.dispatcher.close()
                    container.checkpoint_connection.close()

    def test_for_test_accepts_scenario_dependencies(self) -> None:
        catalog = ScenarioCatalog.load()
        retriever = object()
        container = WebContainer.for_test(
            repository=object(),
            pre_interview_graph=object(),
            dispatcher=object(),
            scenario_catalog=catalog,
            scenario_retriever=retriever,
        )
        self.assertIs(container.scenario_catalog, catalog)
        self.assertIs(container.scenario_retriever, retriever)


if __name__ == "__main__":
    import unittest

    unittest.main()
