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


class FailingReranker:
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise RuntimeError("reranker unavailable")


class WrongLengthReranker:
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [1.0 for _ in documents[:-1]]


class NonFiniteReranker:
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [float("nan") for _ in documents]


class SameScoreReranker:
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [7.0 for _ in documents]


class UniformEmbedding:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]


class QueryEmbedding:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]


class DocumentScoreReranker:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = dict(scores)

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [self.scores[document] for document in documents]


FIXED_UNIT_A = "fixed_alpha::fixed_alpha_module"
FIXED_UNIT_B = "fixed_beta::fixed_beta_module"


def fixed_payload(retrieval_unit_id: str) -> dict[str, object]:
    scenario_id, _, module_id = retrieval_unit_id.partition("::")
    return {
        "role_family": "ai_application_engineering",
        "role_profile_version": "2026-H2",
        "retrieval_unit_id": retrieval_unit_id,
        "scenario_id": scenario_id,
        "module_id": module_id,
        "primary_dimension_id": "role_dim_03",
        "supported_modes": ["system_design"],
        "supported_requirement_types": ["system_design"],
        "difficulties": ["intermediate"],
        "status": "active",
        "valid_from": "2026-01-01",
        "valid_until": None,
        "version": 1,
    }


def fixed_local_store() -> QdrantScenarioStore:
    store = QdrantScenarioStore(embedding=QueryEmbedding())
    beta = SimpleNamespace(
        retrieval_unit_id=FIXED_UNIT_B,
        payload=fixed_payload(FIXED_UNIT_B),
        semantic_text="beta",
        vector=[0.6, 0.8],
    )
    alpha = SimpleNamespace(
        retrieval_unit_id=FIXED_UNIT_A,
        payload=fixed_payload(FIXED_UNIT_A),
        semantic_text="alpha",
        vector=[1.0, 0.0],
    )
    # Keep the lower-ranked point first so positional score mix-ups are visible.
    store._points = {FIXED_UNIT_B: beta, FIXED_UNIT_A: alpha}
    store._dimension = 2
    return store


class FixedRankingQdrantClient:
    def query_points(
        self,
        *,
        collection_name: str,
        query: list[float],
        query_filter: object,
        limit: int,
        with_payload: bool,
    ) -> object:
        return SimpleNamespace(
            points=[
                SimpleNamespace(score=0.4, payload=fixed_payload(FIXED_UNIT_B)),
                SimpleNamespace(score=0.8, payload=fixed_payload(FIXED_UNIT_A)),
            ]
        )


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

    def test_local_ranking_preserves_components_and_top1_margin(self) -> None:
        store = fixed_local_store()
        reranker = DocumentScoreReranker({"alpha": 10.0, "beta": -2.0})

        result = store.search(
            make_request(semantic_query="alpha"),
            as_of=self.AS_OF,
            reranker=reranker,
            limit=2,
        )

        self.assertEqual(result.status, "hit")
        self.assertEqual(
            [candidate.retrieval_unit_id for candidate in result.candidates],
            [FIXED_UNIT_A, FIXED_UNIT_B],
        )
        by_id = {candidate.retrieval_unit_id: candidate for candidate in result.candidates}
        alpha = by_id[FIXED_UNIT_A]
        beta = by_id[FIXED_UNIT_B]
        self.assertAlmostEqual(alpha.dense_score, 1.0)
        self.assertAlmostEqual(alpha.lexical_score, 1.0)
        self.assertAlmostEqual(alpha.raw_reranker_score, 10.0)
        self.assertAlmostEqual(alpha.normalized_reranker_score, 1.0)
        self.assertAlmostEqual(alpha.score, 1.0)
        self.assertAlmostEqual(beta.dense_score, 0.6)
        self.assertAlmostEqual(beta.lexical_score, 0.0)
        self.assertAlmostEqual(beta.raw_reranker_score, -2.0)
        self.assertAlmostEqual(beta.normalized_reranker_score, 0.0)
        self.assertAlmostEqual(beta.score, 0.252)
        self.assertAlmostEqual(result.top1_margin, 0.748)

    def test_reranker_span_overflow_falls_back_to_hybrid_scores(self) -> None:
        store = fixed_local_store()
        reranker = DocumentScoreReranker(
            {"alpha": 1.7e308, "beta": -1.7e308}
        )

        result = store.search(
            make_request(semantic_query="alpha"),
            as_of=self.AS_OF,
            reranker=reranker,
            limit=2,
        )

        self.assertEqual(result.status, "hit")
        self.assertEqual(
            [candidate.retrieval_unit_id for candidate in result.candidates],
            [FIXED_UNIT_A, FIXED_UNIT_B],
        )
        by_id = {candidate.retrieval_unit_id: candidate for candidate in result.candidates}
        self.assertIsNone(by_id[FIXED_UNIT_A].raw_reranker_score)
        self.assertIsNone(by_id[FIXED_UNIT_A].normalized_reranker_score)
        self.assertAlmostEqual(by_id[FIXED_UNIT_A].score, 1.0)
        self.assertIsNone(by_id[FIXED_UNIT_B].raw_reranker_score)
        self.assertIsNone(by_id[FIXED_UNIT_B].normalized_reranker_score)
        self.assertAlmostEqual(by_id[FIXED_UNIT_B].score, 0.42)
        self.assertAlmostEqual(result.top1_margin, 0.58)

    def test_local_reranker_failure_keeps_hybrid_and_clears_reranker_components(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        store = QdrantScenarioStore(embedding=FakeEmbedding())
        store.rebuild(catalog)

        for failed_reranker in (None, FailingReranker(), WrongLengthReranker(), NonFiniteReranker()):
            result = store.search(make_request(), as_of=self.AS_OF, reranker=failed_reranker, limit=2)

            self.assertEqual(result.status, "hit")
            for candidate in result.candidates:
                self.assertIsNone(candidate.raw_reranker_score)
                self.assertIsNone(candidate.normalized_reranker_score)
                self.assertAlmostEqual(
                    candidate.score,
                    0.7 * candidate.dense_score + 0.3 * candidate.lexical_score,
                )

    def test_same_reranker_scores_preserve_raw_and_normalize_to_zero(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        store = QdrantScenarioStore(embedding=FakeEmbedding())
        store.rebuild(catalog)

        result = store.search(make_request(), as_of=self.AS_OF, reranker=SameScoreReranker(), limit=2)

        self.assertEqual(result.status, "hit")
        for candidate in result.candidates:
            self.assertEqual(candidate.raw_reranker_score, 7.0)
            self.assertEqual(candidate.normalized_reranker_score, 0.0)
            self.assertAlmostEqual(
                candidate.score,
                0.6 * (0.7 * candidate.dense_score + 0.3 * candidate.lexical_score),
            )

    def test_qdrant_ranking_preserves_components_and_top1_margin(self) -> None:
        reader = QdrantScenarioStore(
            embedding=QueryEmbedding(),
            client=FixedRankingQdrantClient(),
        )
        reader._semantic_text = {
            FIXED_UNIT_A: "alpha",
            FIXED_UNIT_B: "beta",
        }
        reranker = DocumentScoreReranker({"alpha": 5.0, "beta": -5.0})

        result = reader.search(
            make_request(semantic_query="alpha"),
            as_of=self.AS_OF,
            reranker=reranker,
            limit=2,
        )

        self.assertEqual(result.status, "hit")
        self.assertEqual(
            [candidate.retrieval_unit_id for candidate in result.candidates],
            [FIXED_UNIT_A, FIXED_UNIT_B],
        )
        by_id = {candidate.retrieval_unit_id: candidate for candidate in result.candidates}
        alpha = by_id[FIXED_UNIT_A]
        beta = by_id[FIXED_UNIT_B]
        self.assertAlmostEqual(alpha.dense_score, 0.8)
        self.assertAlmostEqual(alpha.lexical_score, 1.0)
        self.assertAlmostEqual(alpha.raw_reranker_score, 5.0)
        self.assertAlmostEqual(alpha.normalized_reranker_score, 1.0)
        self.assertAlmostEqual(alpha.score, 0.916)
        self.assertAlmostEqual(beta.dense_score, 0.4)
        self.assertAlmostEqual(beta.lexical_score, 0.0)
        self.assertAlmostEqual(beta.raw_reranker_score, -5.0)
        self.assertAlmostEqual(beta.normalized_reranker_score, 0.0)
        self.assertAlmostEqual(beta.score, 0.168)
        self.assertAlmostEqual(result.top1_margin, 0.748)

    def test_top1_margin_is_none_for_zero_or_one_candidate_and_zero_for_tie(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        store = QdrantScenarioStore(embedding=FakeEmbedding())
        store.rebuild(catalog)
        matching_ids = [
            point.retrieval_unit_id
            for point in store._points.values()
            if point.payload["primary_dimension_id"] == "role_dim_03"
        ]

        no_match = store.search(
            make_request(excluded_retrieval_unit_ids=matching_ids),
            as_of=self.AS_OF,
            limit=2,
        )
        self.assertEqual(no_match.status, "no_match")
        self.assertIsNone(no_match.top1_margin)

        one = store.search(
            make_request(excluded_retrieval_unit_ids=matching_ids[1:]),
            as_of=self.AS_OF,
            limit=1,
        )
        self.assertEqual(one.status, "hit")
        self.assertEqual(len(one.candidates), 1)
        self.assertIsNone(one.top1_margin)

        tie_store = QdrantScenarioStore(embedding=UniformEmbedding())
        tie_store.rebuild(catalog)
        tie = tie_store.search(
            make_request(semantic_query="unmatched-token"),
            as_of=self.AS_OF,
            reranker=SameScoreReranker(),
            limit=2,
        )
        self.assertEqual(tie.status, "hit")
        self.assertEqual(tie.candidates[0].score, tie.candidates[1].score)
        self.assertEqual(tie.top1_margin, 0.0)

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
