from __future__ import annotations

from datetime import date
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

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
    def test_payload_match_rejects_low_trust(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        payload = store._record_to_payload(self.record("q-low").model_copy(update={"trust_level": "low"}))
        self.assertFalse(store._payload_matches_intent(payload, self.intent, date(2026, 8, 26)))

    def test_v2_manifest_missing_each_contract_field_is_unreadable(self) -> None:
        store = self.make_store(fingerprint=IndexFingerprint(provider="p", model="m", dimension=3, index_version="questions-v2", embedding_text_version="six-section-v1", question_bank_manifest_hash="sha256:bank", mode_policy_version="2026-H2"))
        store.rebuild([self.record("q-v2")], [[1.0, 0.0, 0.0]], store.fingerprint)
        point = store.client.retrieve(collection_name=COLLECTION_NAME, ids=[store._manifest_point_id()], with_payload=True, with_vectors=False)[0]
        for field in ("embedding_text_version", "question_bank_manifest_hash", "mode_policy_version"):
            payload = dict(point.payload)
            payload.pop(field)
            store.client.upsert(collection_name=COLLECTION_NAME, points=[PointStruct(id=point.id, vector=[0.0, 0.0, 0.0], payload=payload)])
            self.assertIsNone(store._read_manifest())

    def test_legacy_manifest_missing_contract_field_is_public_index_mismatch(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        store.rebuild([self.record("q-legacy")], [[1.0, 0.0, 0.0]], self.fingerprint)
        point = store.client.retrieve(collection_name=COLLECTION_NAME, ids=[store._manifest_point_id()], with_payload=True, with_vectors=False)[0]
        for field in ("embedding_text_version", "question_bank_manifest_hash", "mode_policy_version"):
            payload = dict(point.payload)
            payload.pop(field)
            store.client.upsert(collection_name=COLLECTION_NAME, points=[PointStruct(id=point.id, vector=[0.0, 0.0, 0.0], payload=payload)])
            self.assertEqual(store.search(intent=self.intent, query_vector=[1.0, 0.0, 0.0], today=date(2026, 8, 26)).status, "index_mismatch")

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
        # model_copy is intentional for filter fixtures: malformed records are
        # injected directly into the backend when search-side filtering needs
        # to exercise a corrupted payload.
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

    def make_legacy_store(self) -> tuple[QdrantQuestionStore, QdrantClient]:
        client = QdrantClient(path=":memory:")
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=3, distance=Distance.COSINE),
        )
        old = self.record("q-old")
        store = QdrantQuestionStore(
            client=client,
            fingerprint=self.fingerprint,
            authoritative_catalog={old.question_id: old},
        )
        old_payload = store._record_to_payload(old)
        old_payload["record_type"] = "question"
        old_payload["valid_until_epoch"] = old.valid_until.toordinal()
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                store._manifest_point(self.fingerprint),
                PointStruct(
                    id=store._question_point_id(old.question_id),
                    vector=[1.0, 0.0, 0.0],
                    payload=old_payload,
                ),
            ],
        )
        return store, client

    @staticmethod
    def temporary_collections_with_question(
        client: QdrantClient,
        question_id: str,
    ) -> list[str]:
        names = [
            collection.name
            for collection in client.get_collections().collections
            if collection.name.startswith(f"{COLLECTION_NAME}__")
        ]
        matches: list[str] = []
        for name in names:
            points, _ = client.scroll(
                collection_name=name,
                limit=100,
                with_payload=True,
                with_vectors=False,
            )
            if any(
                isinstance(point.payload, dict)
                and point.payload.get("question_id") == question_id
                for point in points
            ):
                matches.append(name)
        return matches

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

    def test_question_payload_has_exact_safe_allowlist_and_drops_forbidden_values(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        record = self.record("q-payload").model_copy(
            update={
                "difficulty": "advanced",
                "expected_signals": ["EXPECTED_SIGNAL_PROBE"],
                "critical_errors": ["CRITICAL_ERROR_PROBE"],
                "follow_up_seeds": ["FOLLOW_UP_PROBE"],
                "company_tags": ["COMPANY_TAG_PROBE"],
                "source_url": "https://forbidden.example/SOURCE_URL_PROBE",
                "source_title": "SOURCE_TITLE_PROBE",
                "source_id": "candidate@example.com",
                "source_type": "https://forbidden.example/SOURCE_TYPE_PROBE",
            }
        )

        store.rebuild([record], [[1.0, 0.0, 0.0]], self.fingerprint)

        points, _ = store.client.scroll(
            collection_name=COLLECTION_NAME,
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
        payload = next(
            point.payload
            for point in points
            if point.payload and point.payload.get("record_type") == "question"
        )
        expected_keys = {
            "record_type",
            "question_id",
            "question_text",
            "role",
            "dimension_id",
            "question_mode",
            "skills",
            "source_id",
            "source_type",
            "published_at",
            "verified_at",
            "valid_until",
            "valid_until_epoch",
            "trust_level",
            "status",
        }
        self.assertEqual(set(payload), expected_keys)
        self.assertEqual(payload["source_id"], "qdrant:q-payload")
        self.assertEqual(payload["source_type"], "qdrant_index")
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            "difficulty",
            "expected_signals",
            "critical_errors",
            "follow_up_seeds",
            "company_tags",
            "source_url",
            "source_title",
            "EXPECTED_SIGNAL_PROBE",
            "CRITICAL_ERROR_PROBE",
            "FOLLOW_UP_PROBE",
            "COMPANY_TAG_PROBE",
            "SOURCE_URL_PROBE",
            "SOURCE_TITLE_PROBE",
            "SOURCE_TYPE_PROBE",
        ):
            self.assertNotIn(forbidden, serialized_payload)

        result = self.search(store)
        self.assertEqual(result.status, "hit")
        hit = result.hits[0].record
        self.assertEqual(hit.question_id, "q-payload")
        self.assertEqual(hit.source_url, record.source_url)

    def test_extended_fingerprint_is_persisted_and_each_component_mismatch_is_safe(self) -> None:
        fingerprint = IndexFingerprint(
            provider="fake-embeddings",
            model="fake-model-v2",
            dimension=3,
            index_version="questions-v2",
            embedding_text_version="six-section-v1",
            question_bank_manifest_hash="sha256:manifest",
            mode_policy_version="2026-H2",
        )
        store = self.make_store(fingerprint=fingerprint)
        store.rebuild([self.record("q-fingerprint")], [[1.0, 0.0, 0.0]], fingerprint)
        self.assertEqual(store.get_manifest(), fingerprint)

        points, _ = store.client.scroll(
            collection_name=COLLECTION_NAME,
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
        manifest = next(
            point.payload for point in points if point.payload["record_type"] == "manifest"
        )
        self.assertEqual(manifest["embedding_text_version"], "six-section-v1")
        self.assertEqual(manifest["question_bank_manifest_hash"], "sha256:manifest")
        self.assertEqual(manifest["mode_policy_version"], "2026-H2")

        for field, value in {
            "provider": "other-provider",
            "model": "other-model",
            "dimension": 4,
            "index_version": "questions-v3",
            "embedding_text_version": "six-section-v2",
            "question_bank_manifest_hash": "sha256:other",
            "mode_policy_version": "2027-H1",
        }.items():
            with self.subTest(field=field):
                different = fingerprint.model_copy(update={field: value})
                reader = QdrantQuestionStore(
                    client=store.client,
                    fingerprint=different,
                )
                self.assertEqual(self.search(reader).status, "index_mismatch")

    def test_search_excludes_wrong_role_dimension_mode_status_date_and_ids(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        records = [
            self.record("q-good"),
            self.record("q-wrong-dimension", dimension_id="role_dim_02"),
            self.record("q-wrong-mode", question_mode="coding"),
            self.record("q-retired", status="retired"),
            self.record("q-expired", valid_until=date(2026, 8, 25)),
            self.record("q-excluded"),
        ]
        vectors = [[1.0, 0.0, 0.0]] + [[0.99, 0.01, 0.0]] * (len(records) - 1)
        store.rebuild(records, vectors, self.fingerprint)

        # A malformed role can only enter a disposable index through a raw
        # backend fault; the public record boundary rejects it.  Seed this
        # payload directly to keep the search-side filter regression intact.
        wrong_role = self.record("q-wrong-role", role="other_role")
        wrong_role_payload = wrong_role.model_dump(mode="json", warnings=False)
        wrong_role_payload["record_type"] = "question"
        wrong_role_payload["valid_until_epoch"] = wrong_role.valid_until.toordinal()
        store.client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                PointStruct(
                    id=store._question_point_id(wrong_role.question_id),
                    vector=[0.99, 0.01, 0.0],
                    payload=wrong_role_payload,
                )
            ],
        )
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

    def test_rebuild_revalidates_mutable_record_before_writing(self) -> None:
        updates = {
            "hash": {"content_hash": "not-a-hash"},
            "date": {"valid_until": "not-a-date"},
            "role": {"role": "not-a-role"},
            "mode": {"question_mode": "not-a-mode"},
        }
        for label, update in updates.items():
            with self.subTest(label=label):
                store = self.make_store(fingerprint=self.fingerprint)
                old = self.record("q-old")
                store.rebuild([old], [[1.0, 0.0, 0.0]], self.fingerprint)
                malformed = old.model_copy(update=update)

                with self.assertRaises(ValueError):
                    store.rebuild([malformed], [[0.0, 1.0, 0.0]], self.fingerprint)

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

    def test_persisted_reader_without_authoritative_catalog_returns_unavailable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            writer = QdrantQuestionStore(path=path, fingerprint=self.fingerprint)
            writer.rebuild(
                [self.record("q-catalog")],
                [[1.0, 0.0, 0.0]],
                self.fingerprint,
            )
            writer.close()

            reader = QdrantQuestionStore(path=path, fingerprint=self.fingerprint)
            result = self.search(reader)
            reader.close()

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.hits, [])

    def test_search_uses_authoritative_catalog_instead_of_clipped_payload(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        record = self.record("q-catalog")
        store.rebuild([record], [[1.0, 0.0, 0.0]], self.fingerprint)
        point = store.client.retrieve(
            collection_name=COLLECTION_NAME,
            ids=[store._question_point_id(record.question_id)],
            with_payload=True,
            with_vectors=True,
        )[0]
        clipped_payload = dict(point.payload or {})
        clipped_payload["question_text"] = "PAYLOAD_ONLY_NOT_CANONICAL"
        clipped_payload["source_id"] = "payload-only-source"
        store.client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                PointStruct(
                    id=point.id,
                    vector=point.vector,
                    payload=clipped_payload,
                )
            ],
        )

        result = self.search(store)

        self.assertEqual(result.status, "hit")
        self.assertEqual(result.hits[0].record.question_text, record.question_text)
        self.assertEqual(result.hits[0].record.source_id, record.source_id)
        self.assertNotEqual(
            result.hits[0].record.question_text,
            "PAYLOAD_ONLY_NOT_CANONICAL",
        )

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

    def test_alias_switch_applied_then_raises_keeps_new_alias_and_search(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        store.rebuild([self.record("q-old")], [[1.0, 0.0, 0.0]], self.fingerprint)
        original_update = store.client.update_collection_aliases

        def update_then_raise(*args: object, **kwargs: object) -> object:
            result = original_update(*args, **kwargs)
            raise RuntimeError("alias applied before response failed")

        with patch.object(
            store.client,
            "update_collection_aliases",
            side_effect=update_then_raise,
        ):
            store.rebuild(
                [self.record("q-new")],
                [[0.0, 1.0, 0.0]],
                self.fingerprint,
            )

        self.assertIsNotNone(store._alias_target())
        self.assertEqual(self.search(store).status, "hit")
        self.assertEqual(self.search(store).hits[0].record.question_id, "q-new")
        self.assertEqual(store.get_manifest(), self.fingerprint)
        self.assertEqual(
            self.temporary_collections_with_question(store.client, "q-old"),
            [],
        )

    def test_legacy_collection_alias_failure_preserves_old_index(self) -> None:
        store, client = self.make_legacy_store()

        with patch.object(
            client,
            "update_collection_aliases",
            side_effect=RuntimeError("injected legacy alias failure"),
        ):
            with self.assertRaises(RuntimeError):
                store.rebuild(
                    [self.record("q-new")],
                    [[0.0, 1.0, 0.0]],
                    self.fingerprint,
                )

        result = self.search(store)
        self.assertEqual(result.status, "hit")
        self.assertEqual(result.hits[0].record.question_id, "q-old")
        self.assertEqual(store.get_manifest(), self.fingerprint)
        self.assertEqual(client.get_aliases().aliases, [])

    def test_legacy_delete_after_effect_restores_searchable_old_index_and_keeps_backup(self) -> None:
        store, client = self.make_legacy_store()
        original_delete = client.delete_collection

        def delete_then_raise(*args: object, **kwargs: object) -> object:
            result = original_delete(*args, **kwargs)
            if kwargs.get("collection_name") == COLLECTION_NAME:
                raise RuntimeError("delete applied before response failed")
            return result

        with patch.object(client, "delete_collection", side_effect=delete_then_raise):
            with self.assertRaisesRegex(RuntimeError, "delete applied"):
                store.rebuild(
                    [self.record("q-new")],
                    [[0.0, 1.0, 0.0]],
                    self.fingerprint,
                )

        result = self.search(store)
        self.assertEqual(result.status, "hit")
        self.assertEqual(result.hits[0].record.question_id, "q-old")
        self.assertEqual(client.get_aliases().aliases, [])
        self.assertTrue(
            self.temporary_collections_with_question(client, "q-old"),
            "legacy backup must remain recoverable after an ambiguous delete",
        )

    def test_legacy_delete_after_effect_restore_failure_keeps_backup_and_logs(self) -> None:
        store, client = self.make_legacy_store()
        original_delete = client.delete_collection

        def delete_then_raise(*args: object, **kwargs: object) -> object:
            result = original_delete(*args, **kwargs)
            if kwargs.get("collection_name") == COLLECTION_NAME:
                raise RuntimeError("delete applied before response failed")
            return result

        with patch.object(client, "delete_collection", side_effect=delete_then_raise):
            with patch.object(
                store,
                "_restore_collection",
                side_effect=RuntimeError("injected restore failure"),
            ):
                with self.assertLogs(
                    "profile_agent.knowledge.qdrant_question_store",
                    level=logging.WARNING,
                ):
                    with self.assertRaisesRegex(RuntimeError, "delete applied"):
                        store.rebuild(
                            [self.record("q-new")],
                            [[0.0, 1.0, 0.0]],
                            self.fingerprint,
                        )

        result = self.search(store)
        self.assertEqual(result.status, "hit")
        self.assertEqual(result.hits[0].record.question_id, "q-old")
        backup_target = store._alias_target()
        self.assertIsNotNone(backup_target)
        self.assertTrue(backup_target.startswith(f"{COLLECTION_NAME}__"))
        self.assertTrue(
            self.temporary_collections_with_question(client, "q-old"),
            "backup must remain after restore failure",
        )

    def test_legacy_backup_cleanup_failure_keeps_new_index_and_backup(self) -> None:
        store, client = self.make_legacy_store()
        original_delete = client.delete_collection

        def fail_backup_cleanup(*args: object, **kwargs: object) -> object:
            collection_name = kwargs.get("collection_name")
            if isinstance(collection_name, str) and collection_name.startswith(
                f"{COLLECTION_NAME}__"
            ):
                raise RuntimeError("injected backup cleanup failure")
            return original_delete(*args, **kwargs)

        with patch.object(client, "delete_collection", side_effect=fail_backup_cleanup):
            with self.assertLogs(
                "profile_agent.knowledge.qdrant_question_store",
                level=logging.WARNING,
            ):
                store.rebuild(
                    [self.record("q-new")],
                    [[0.0, 1.0, 0.0]],
                    self.fingerprint,
                )

        result = self.search(store)
        self.assertEqual(result.status, "hit")
        self.assertEqual(result.hits[0].record.question_id, "q-new")
        self.assertTrue(
            self.temporary_collections_with_question(client, "q-old"),
            "backup must remain recoverable when cleanup fails",
        )

    def test_temporary_cleanup_failure_is_logged_and_old_index_remains(self) -> None:
        store = self.make_store(fingerprint=self.fingerprint)
        store.rebuild([self.record("q-old")], [[1.0, 0.0, 0.0]], self.fingerprint)

        with patch.object(store.client, "upsert", side_effect=RuntimeError("injected write")):
            with patch.object(
                store.client,
                "delete_collection",
                side_effect=RuntimeError("injected cleanup"),
            ):
                with self.assertLogs(
                    "profile_agent.knowledge.qdrant_question_store",
                    level=logging.WARNING,
                ) as captured:
                    with self.assertRaises(RuntimeError):
                        store.rebuild(
                            [self.record("q-new")],
                            [[0.0, 1.0, 0.0]],
                            self.fingerprint,
                        )

        self.assertTrue(any("cleanup failed" in line for line in captured.output))
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
