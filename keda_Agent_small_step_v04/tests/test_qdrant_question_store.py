from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from profile_agent.knowledge.qdrant_question_store import (
    COLLECTION_NAME,
    IndexFingerprint,
    QdrantQuestionStore,
)
from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    QuestionRetrievalIntent,
)


class QdrantQuestionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fingerprint = IndexFingerprint(
            provider="fake-embeddings",
            model="fake-model-v1",
            dimension=3,
            index_version="questions-v1",
        )
        self.intent = QuestionRetrievalIntent(
            query_text="工具执行成功但响应丢失，如何避免重复执行？",
            role="ai_agent_engineer",
            dimension_id="role_dim_01",
            question_mode="scenario",
            difficulty="intermediate",
        )

    @staticmethod
    def record(
        question_id: str,
        *,
        dimension_id: str = "role_dim_01",
        question_mode: str = "scenario",
        role: str = "ai_agent_engineer",
        status: str = "active",
        valid_until: date = date(2027, 1, 1),
    ) -> InterviewQuestionRecord:
        # model_copy is intentional for filter fixtures: the store must still
        # write and filter payload fields even when a malformed point enters a
        # disposable index through a test-only boundary.
        base = InterviewQuestionRecord(
            question_id=question_id,
            question_text=f"如何处理 {question_id}？",
            role="ai_agent_engineer",
            role_version="2026-H2",
            dimension_id=dimension_id,
            skills=["幂等"],
            question_mode="scenario",
            difficulty="intermediate",
            expected_signals=["状态查询"],
            critical_errors=[],
            follow_up_seeds=[],
            company_tags=[],
            source_id=f"source-{question_id}",
            source_url="https://example.com/source",
            source_title="Public source",
            source_type="public_interview_experience",
            published_at=date(2026, 7, 1),
            verified_at=date(2026, 8, 1),
            valid_until=valid_until,
            trust_level="medium",
            status="active",
            version=1,
            content_hash="sha256:test",
        )
        return base.model_copy(
            update={
                "role": role,
                "question_mode": question_mode,
                "status": status,
            }
        )

    def make_store(self, *, fingerprint: IndexFingerprint | None = None) -> QdrantQuestionStore:
        return QdrantQuestionStore(path=":memory:", fingerprint=fingerprint)

    def search(self, store: QdrantQuestionStore, *, query_vector=(1.0, 0.0, 0.0), limit=3):
        return store.search(
            intent=self.intent,
            query_vector=query_vector,
            today=date(2026, 8, 26),
            limit=limit,
        )

    def test_rebuild_records_manifest_and_filter_payload(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        record = self.record("q-good")

        store.rebuild([record], [[1.0, 0.0, 0.0]], self.fingerprint)

        points, _ = store.client.scroll(
            collection_name=COLLECTION_NAME,
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
        payloads = [point.payload for point in points]
        manifest = next(payload for payload in payloads if payload["record_type"] == "manifest")
        question = next(payload for payload in payloads if payload["record_type"] == "question")
        self.assertEqual(manifest["embedding_provider"], "fake-embeddings")
        self.assertEqual(manifest["embedding_model"], "fake-model-v1")
        self.assertEqual(manifest["dimension"], 3)
        self.assertEqual(manifest["index_version"], "questions-v1")
        self.assertEqual(question["question_id"], "q-good")
        self.assertEqual(question["role"], "ai_agent_engineer")
        self.assertEqual(question["dimension_id"], "role_dim_01")
        self.assertEqual(question["question_mode"], "scenario")
        self.assertEqual(question["status"], "active")
        self.assertEqual(question["valid_until"], "2027-01-01")

        result = self.search(store)
        self.assertEqual(result.status, "hit")
        self.assertEqual(result.hits[0].record.question_id, "q-good")

    def test_search_excludes_wrong_role_dimension_mode_status_date_and_ids(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        records = [
            self.record("q-good"),
            self.record("q-wrong-role", role="other_role"),
            self.record("q-wrong-dimension", dimension_id="role_dim_02"),
            self.record("q-wrong-mode", question_mode="coding"),
            self.record("q-retired", status="retired"),
            self.record("q-expired", valid_until=date(2026, 8, 25)),
            self.record("q-excluded"),
        ]
        vectors = [[1.0, 0.0, 0.0]] + [[0.99, 0.01, 0.0]] * (len(records) - 1)
        store.rebuild(records, vectors, self.fingerprint)
        self.intent = self.intent.model_copy(update={"excluded_question_ids": ["q-excluded"]})

        result = self.search(store, limit=10)

        self.assertEqual(result.status, "hit")
        self.assertEqual([hit.record.question_id for hit in result.hits], ["q-good"])

    def test_sync_removes_retired_and_stale_records(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        old = self.record("q-old")
        keep = self.record("q-keep")
        retired = self.record("q-retired", status="retired")
        expired = self.record("q-expired", valid_until=date(2026, 8, 25))
        store.rebuild([old, keep], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], self.fingerprint)

        store.sync(
            [keep, retired, expired],
            [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.5, 0.5, 0.0]],
            self.fingerprint,
            today=date(2026, 8, 26),
        )

        points, _ = store.client.scroll(
            collection_name=COLLECTION_NAME,
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
        question_ids = {
            point.payload["question_id"]
            for point in points
            if point.payload.get("record_type") == "question"
        }
        self.assertEqual(question_ids, {"q-keep"})
        self.assertEqual(self.search(store).hits[0].record.question_id, "q-keep")

    def test_sync_rejects_fingerprint_mismatch_without_writes(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        store.rebuild([self.record("q-old")], [[1.0, 0.0, 0.0]], self.fingerprint)
        mismatched = IndexFingerprint(
            provider="fake-embeddings",
            model="fake-model-v2",
            dimension=3,
            index_version="questions-v2",
        )

        with self.assertRaises(ValueError):
            store.sync(
                [self.record("q-new")],
                [[0.0, 1.0, 0.0]],
                mismatched,
                today=date(2026, 8, 26),
            )

        self.assertEqual(self.search(store).hits[0].record.question_id, "q-old")
        self.assertEqual(store.get_manifest(), self.fingerprint)

    def test_invalid_rebuild_does_not_replace_usable_collection(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        old = self.record("q-old")
        store.rebuild([old], [[1.0, 0.0, 0.0]], self.fingerprint)

        with self.assertRaises(ValueError):
            store.rebuild(
                [self.record("q-new")],
                [[1.0, 0.0]],
                self.fingerprint,
            )

        self.assertEqual(self.search(store).hits[0].record.question_id, "q-old")

    def test_fingerprint_mismatch_returns_safe_index_mismatch(self) -> None:
        expected = self.fingerprint
        actual = IndexFingerprint(
            provider="fake-embeddings",
            model="fake-model-v2",
            dimension=3,
            index_version="questions-v2",
        )
        store = self.make_store(fingerprint=expected)
        store.rebuild([self.record("q-good")], [[1.0, 0.0, 0.0]], expected)
        mismatched_reader = QdrantQuestionStore(
            client=store.client,
            fingerprint=actual,
        )

        result = self.search(mismatched_reader)

        self.assertEqual(result.status, "index_mismatch")
        self.assertEqual(result.hits, [])

    def test_query_dimension_mismatch_returns_safe_index_mismatch(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        store.rebuild([self.record("q-good")], [[1.0, 0.0, 0.0]], self.fingerprint)

        result = self.search(store, query_vector=(1.0, 0.0))

        self.assertEqual(result.status, "index_mismatch")
        self.assertEqual(result.hits, [])

    def test_reader_fingerprint_is_required(self) -> None:
        with self.assertRaises(ValueError):
            QdrantQuestionStore(path=":memory:")

    def test_rebuild_rejects_mismatched_fingerprint_without_replacing_index(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        store.rebuild([self.record("q-old")], [[1.0, 0.0, 0.0]], self.fingerprint)
        mismatched = IndexFingerprint(
            provider="fake-embeddings",
            model="fake-model-v2",
            dimension=3,
            index_version="questions-v2",
        )

        with self.assertRaises(ValueError):
            store.rebuild([self.record("q-new")], [[1.0, 0.0, 0.0]], mismatched)

        self.assertEqual(self.search(store).hits[0].record.question_id, "q-old")

    def test_persisted_reader_with_different_fingerprint_returns_index_mismatch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            writer = QdrantQuestionStore(path=path, fingerprint=self.fingerprint)
            writer.rebuild(
                [self.record("q-good")],
                [[1.0, 0.0, 0.0]],
                self.fingerprint,
            )
            writer.close()
            different = IndexFingerprint(
                provider="fake-embeddings",
                model="fake-model-v2",
                dimension=3,
                index_version="questions-v2",
            )
            reader = QdrantQuestionStore(path=path, fingerprint=different)
            result = self.search(reader)
            reader.close()

        self.assertEqual(result.status, "index_mismatch")
        self.assertEqual(result.hits, [])

    def test_rebuild_write_failure_keeps_previous_collection_and_manifest(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        store.rebuild([self.record("q-old")], [[1.0, 0.0, 0.0]], self.fingerprint)

        with patch.object(store.client, "upsert", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                store.rebuild(
                    [self.record("q-new")],
                    [[0.0, 1.0, 0.0]],
                    self.fingerprint,
                )

        self.assertEqual(self.search(store).hits[0].record.question_id, "q-old")
        self.assertEqual(store.get_manifest(), self.fingerprint)

    def test_rebuild_create_failure_keeps_previous_collection_and_manifest(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        store.rebuild([self.record("q-old")], [[1.0, 0.0, 0.0]], self.fingerprint)

        with patch.object(
            store.client,
            "create_collection",
            side_effect=RuntimeError("injected create failure"),
        ):
            with self.assertRaises(RuntimeError):
                store.rebuild(
                    [self.record("q-new")],
                    [[0.0, 1.0, 0.0]],
                    self.fingerprint,
                )

        self.assertEqual(self.search(store).hits[0].record.question_id, "q-old")
        self.assertEqual(store.get_manifest(), self.fingerprint)

    def test_rebuild_alias_switch_failure_keeps_previous_collection_and_manifest(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        store.rebuild([self.record("q-old")], [[1.0, 0.0, 0.0]], self.fingerprint)

        with patch.object(
            store.client,
            "update_collection_aliases",
            side_effect=RuntimeError("injected alias failure"),
        ):
            with self.assertRaises(RuntimeError):
                store.rebuild(
                    [self.record("q-new")],
                    [[0.0, 1.0, 0.0]],
                    self.fingerprint,
                )

        self.assertEqual(self.search(store).hits[0].record.question_id, "q-old")
        self.assertEqual(store.get_manifest(), self.fingerprint)

    def test_sync_write_failure_keeps_previous_collection_and_manifest(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        old = self.record("q-old")
        keep = self.record("q-keep")
        store.rebuild(
            [old, keep],
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            self.fingerprint,
        )

        with patch.object(store.client, "upsert", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                store.sync(
                    [keep],
                    [[0.0, 1.0, 0.0]],
                    self.fingerprint,
                    today=date(2026, 8, 26),
                )

        self.assertEqual(self.search(store).hits[0].record.question_id, "q-old")
        self.assertEqual(store.get_manifest(), self.fingerprint)

    def test_sync_scrolls_all_pages_when_removing_stale_points(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        keep = self.record("q-keep")
        store.rebuild([keep], [[1.0, 0.0, 0.0]], self.fingerprint)
        first_id = uuid4()
        second_id = uuid4()
        calls: list[object] = []

        def paginated_scroll(*args, **kwargs):
            calls.append(kwargs.get("offset"))
            if kwargs.get("offset") is None:
                return [
                    SimpleNamespace(
                        id=first_id,
                        payload={"record_type": "question", "question_id": "q-stale-1"},
                    )
                ], "next-page"
            return [
                SimpleNamespace(
                    id=second_id,
                    payload={"record_type": "question", "question_id": "q-stale-2"},
                )
            ], None

        with patch.object(store.client, "scroll", side_effect=paginated_scroll):
            points = store._question_points()

        self.assertEqual(calls, [None, "next-page"])
        self.assertEqual(
            points,
            {"q-stale-1": first_id, "q-stale-2": second_id},
        )

    def test_collection_name_is_fixed_to_interview_questions(self) -> None:
        with self.assertRaises(ValueError):
            QdrantQuestionStore(
                path=":memory:",
                fingerprint=self.fingerprint,
                collection_name="parallel_questions",
            )


if __name__ == "__main__":
    unittest.main()
