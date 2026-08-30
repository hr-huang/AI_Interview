from datetime import date
from pathlib import Path
from types import SimpleNamespace
import unittest

from profile_agent.knowledge.qdrant_scenario_store import QdrantScenarioStore
from profile_agent.schemas.scenario_rag_schema import ScenarioCandidateSet, ScenarioRetrievalRequest
from profile_agent.services.scenario_bank_service import ScenarioCatalog
from profile_agent.services.scenario_retrieval_service import ScenarioRetriever


ROOT = Path(__file__).resolve().parents[1] / "profile_agent" / "knowledge" / "scenario_banks" / "ai_application_engineering_2026_h2"


class FakeEmbedding:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0, 0.0] if "Memory" in text or "Context" in text else [0.0, 1.0] for text in texts]


class FakeReranker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, list(documents)))
        return [float(len(documents) - index) for index in range(len(documents))]


class FakeQdrantClient:
    def __init__(self) -> None:
        self.points: list[object] = []
        self.query_points_calls = 0
        self.last_query_filter = None

    def collection_exists(self, collection_name: str) -> bool:
        return bool(self.points)

    def delete_collection(self, *, collection_name: str) -> None:
        self.points = []

    def create_collection(self, *, collection_name: str, vectors_config: object) -> None:
        return None

    def upsert(self, *, collection_name: str, points: list[object], wait: bool) -> None:
        self.points = list(points)

    def query_points(self, *, collection_name: str, query: list[float], query_filter: object, limit: int, with_payload: bool) -> object:
        self.query_points_calls += 1
        self.last_query_filter = query_filter
        # Return a cross-dimension result as a guard: the store must apply
        # its hard filter to the Qdrant response as well.
        allowed = [point for point in self.points if point.payload["primary_dimension_id"] == "role_dim_03"]
        return SimpleNamespace(points=[SimpleNamespace(id=point.id, score=1.0, payload=point.payload) for point in allowed[:limit]])


def make_request(**overrides: object) -> ScenarioRetrievalRequest:
    values = {
        "primary_dimension_id": "role_dim_03",
        "requirement_type": "system_design",
        "question_mode": "system_design",
        "difficulty": "intermediate",
        "objective": "验证 Context 和 Memory 生命周期",
        "semantic_query": "Memory Context 删除",
    }
    values.update(overrides)
    return ScenarioRetrievalRequest.model_validate(values)


class QdrantScenarioStoreTests(unittest.TestCase):
    AS_OF = date(2026, 8, 29)

    def test_rebuild_embeds_each_active_module_once_and_keeps_payload_controlled(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        embedding = FakeEmbedding()
        store = QdrantScenarioStore(embedding=embedding)

        store.rebuild(catalog)

        self.assertEqual(len(embedding.calls), 1)
        self.assertEqual(len(embedding.calls[0]), len(catalog.active_modules))
        self.assertEqual(len(store.payloads), len(catalog.active_modules))
        allowed = {
            "role_family", "role_profile_version", "retrieval_unit_id", "scenario_id",
            "module_id", "primary_dimension_id", "supported_modes",
            "supported_requirement_types", "difficulties", "status", "valid_from",
            "valid_until", "version",
        }
        for payload in store.payloads.values():
            self.assertEqual(set(payload), allowed)
            self.assertNotIn("constraints", payload)
            self.assertNotIn("evidence_signals", payload)

    def test_search_applies_hard_filters_before_hybrid_ranking(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        store = QdrantScenarioStore(embedding=FakeEmbedding())
        store.rebuild(catalog)

        result = store.search(
            make_request(semantic_query="Memory 知识更新 引用"),
            as_of=self.AS_OF,
            limit=10,
        )

        self.assertIsInstance(result, ScenarioCandidateSet)
        self.assertEqual(result.status, "hit")
        self.assertTrue(result.candidates)
        self.assertTrue(all(candidate.module_id in {"ecommerce_context_tools", "travel_context_tools", "knowledge_rag_memory", "recruitment_context_memory", "data_analysis_context_tools", "sales_context_memory_tools"} for candidate in result.candidates))
        self.assertTrue(all(candidate.module_id != "cost_monitor_observability" for candidate in result.candidates))
        self.assertEqual(result.candidates[0].module_id, "knowledge_rag_memory")

    def test_reranker_receives_only_hard_filtered_semantic_documents(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        store = QdrantScenarioStore(embedding=FakeEmbedding())
        store.rebuild(catalog)
        reranker = FakeReranker()

        result = store.search(make_request(), as_of=self.AS_OF, reranker=reranker, limit=3)

        self.assertEqual(result.status, "hit")
        self.assertEqual(len(reranker.calls), 1)
        documents = reranker.calls[0][1]
        self.assertTrue(documents)
        self.assertLessEqual(len(documents), 6)
        self.assertTrue(all("evidence_signals" not in document for document in documents))
        self.assertTrue(all("cost_monitor_observability" not in document for document in documents))

    def test_unavailable_store_is_converted_to_deterministic_fallback(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        store = QdrantScenarioStore(embedding=FakeEmbedding())
        store.rebuild(catalog)
        store.close()

        selection = ScenarioRetriever(store=store, catalog=catalog).retrieve(
            make_request(), as_of=self.AS_OF
        )

        self.assertEqual(selection.status, "fallback")
        self.assertEqual(selection.module.primary_dimension_id, "role_dim_03")

    def test_client_mode_queries_qdrant_and_reloads_catalog_without_reembedding(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        client = FakeQdrantClient()
        writer_embedding = FakeEmbedding()
        QdrantScenarioStore(embedding=writer_embedding, client=client).rebuild(catalog)

        reader_embedding = FakeEmbedding()
        reader = QdrantScenarioStore(embedding=reader_embedding, client=client)
        reader.load_catalog(catalog)
        result = reader.search(make_request(), as_of=self.AS_OF, limit=1)

        self.assertEqual(client.query_points_calls, 1)
        self.assertEqual(len(reader_embedding.calls), 1)
        self.assertTrue(all(
            key in str(client.last_query_filter)
            for key in ("role_family", "role_profile_version", "primary_dimension_id", "supported_modes", "supported_requirement_types", "difficulties", "status")
        ))
        self.assertEqual(result.status, "hit")
        self.assertIn(result.candidates[0].retrieval_unit_id.split("::")[0], {
            "ecommerce_service", "travel_planner", "enterprise_knowledge_assistant",
            "recruitment_interview", "enterprise_data_analysis", "sales_followup",
        })


if __name__ == "__main__":
    unittest.main()
