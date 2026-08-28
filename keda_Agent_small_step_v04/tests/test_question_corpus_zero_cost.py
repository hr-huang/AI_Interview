from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from qdrant_client import QdrantClient

import run_question_bank
from profile_agent.knowledge import qdrant_question_store as qdrant_store_module
from profile_agent.knowledge.qdrant_question_store import (
    DeterministicFakeQuestionStore,
    IndexFingerprint,
    is_loopback_url,
    validate_loopback_url,
)
from profile_agent.services.question_bank_service import load_question_bank
from profile_agent.services.question_corpus_evaluation import (
    SYNTHETIC_HARD_NEGATIVE_CATALOG,
    evaluate_question_corpus,
    load_retrieval_intents,
)
from profile_agent.services.question_retrieval_service import (
    DeterministicFakeEmbedding,
)


ROOT = Path(__file__).parents[1]
CORPUS_DIR = ROOT / "profile_agent" / "knowledge" / "question_banks" / "ai_agent_engineer_2026_h2"
QUESTIONS_PATH = CORPUS_DIR / "questions.json"
INTENTS_PATH = CORPUS_DIR / "retrieval_intents.jsonl"
AS_OF = date(2026, 8, 27)


class QuestionCorpusZeroCostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.records = load_question_bank(QUESTIONS_PATH)
        cls.intents = load_retrieval_intents(INTENTS_PATH, records=cls.records, as_of=AS_OF)

    def test_deterministic_embedding_is_hash_based_and_provider_free(self) -> None:
        embedding = DeterministicFakeEmbedding(dimension=16)
        first = embedding.embed(["检索 RAG 失败恢复"])[0]
        second = embedding.embed(["检索 RAG 失败恢复"])[0]
        other = embedding.embed(["工具权限校验"])[0]

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first), 16)
        self.assertEqual(embedding.provider, "deterministic-fake")
        self.assertEqual(embedding.model, "deterministic-fake")

    def test_fake_store_is_not_candidate_safe_by_default(self) -> None:
        fingerprint = IndexFingerprint(
            provider="deterministic-fake",
            model="deterministic-fake",
            dimension=16,
            index_version="deterministic-fake-v1",
            embedding_text_version="six-section-v1",
            question_bank_manifest_hash="sha256:fixture",
        )
        store = DeterministicFakeQuestionStore(
            fingerprint=fingerprint,
            embedding=DeterministicFakeEmbedding(dimension=16),
        )
        self.assertFalse(store.candidate_safe)
        store.close()

    def test_fake_adapter_evaluates_thirty_intents_without_qdrant_or_provider(self) -> None:
        fingerprint = IndexFingerprint(
            provider="deterministic-fake",
            model="deterministic-fake",
            dimension=16,
            index_version="deterministic-fake-v1",
            embedding_text_version="six-section-v1",
            question_bank_manifest_hash="sha256:fixture",
        )
        embedding = DeterministicFakeEmbedding(dimension=16)
        store = DeterministicFakeQuestionStore(
            fingerprint=fingerprint,
            embedding=embedding,
            candidate_safe=True,
            hard_negative_candidates=SYNTHETIC_HARD_NEGATIVE_CATALOG,
        )
        question_vectors = embedding.embed(
            [run_question_bank.build_question_embedding_text(record) for record in self.records]
        )
        store.rebuild(
            self.records,
            question_vectors,
            fingerprint,
            hard_negative_candidates=SYNTHETIC_HARD_NEGATIVE_CATALOG,
        )
        report = evaluate_question_corpus(
            self.intents,
            self.records,
            result_provider=store.retrieve,
            as_of=AS_OF,
            backend="deterministic-fake",
        )

        self.assertEqual(len(report.intent_results), 30)
        self.assertTrue(report.passed, report.to_json())
        self.assertEqual(report.trace_coverage, 1.0)
        self.assertEqual(report.hard_negative_hits, 0)
        self.assertTrue(all(len(item.top3) <= 3 for item in report.intent_results))
        self.assertTrue(all(item.trace for item in report.intent_results))

    def test_loopback_allowlist_rejects_external_hosts_before_client_creation(self) -> None:
        self.assertTrue(is_loopback_url("http://127.0.0.1:6333"))
        self.assertTrue(is_loopback_url("http://localhost:6333"))
        self.assertFalse(is_loopback_url("https://qdrant.example.com:6333"))
        with self.assertRaises(ValueError):
            validate_loopback_url("https://qdrant.example.com:6333")
        with self.assertRaises(ValueError):
            validate_loopback_url("http://127.0.0.2:6333")

    def test_fake_cli_is_zero_cost_and_report_is_repeatable(self) -> None:
        outputs: list[dict[str, object]] = []

        def capture(path: Path, payload: object) -> None:
            if (
                path.name == "evaluation_fake.json"
                and isinstance(payload, dict)
                and payload.get("action") == "evaluate-local"
            ):
                outputs.append(payload)

        fail = AssertionError("zero-cost evaluation attempted a paid/network dependency")
        with patch.object(run_question_bank, "_write_corpus_artifact", side_effect=capture), patch.object(
            run_question_bank, "SiliconFlowEmbeddingClient", side_effect=fail
        ), patch.object(run_question_bank, "QdrantQuestionStore", side_effect=fail), patch.object(
            httpx, "Client", side_effect=fail
        ):
            for _ in range(2):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = run_question_bank.main(
                        [
                            "evaluate-local",
                            "--corpus-dir",
                            str(CORPUS_DIR),
                            "--store",
                            "fake",
                            "--dry-run",
                            "--format",
                            "json",
                            "--as-of",
                            AS_OF.isoformat(),
                        ]
                    )
                self.assertEqual(code, 0)
                self.assertEqual(stderr.getvalue(), "")
                outputs.append(json.loads(stdout.getvalue()))

        self.assertEqual(len(outputs), 4)
        first = json.dumps(outputs[0], ensure_ascii=False, sort_keys=True).encode("utf-8")
        second = json.dumps(outputs[2], ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.assertEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest())
        payload = outputs[0]
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["backend"], "deterministic-fake")
        self.assertEqual(payload["model"], "deterministic-fake")
        self.assertIn("manifest_fingerprint", payload)
        self.assertIn("repeatability", payload)

    def test_fake_cli_indexes_all_hard_negative_categories_and_audits_pool(self) -> None:
        stdout = io.StringIO()
        fail = AssertionError("fake calibration must not construct a provider or network client")
        with patch.object(run_question_bank, "_write_corpus_artifact"), patch.object(
            run_question_bank, "SiliconFlowEmbeddingClient", side_effect=fail
        ), patch.object(run_question_bank, "QdrantQuestionStore", side_effect=fail), patch.object(
            httpx, "Client", side_effect=fail
        ), redirect_stdout(stdout):
            code = run_question_bank.main(
                [
                    "evaluate-local",
                    "--corpus-dir",
                    str(CORPUS_DIR),
                    "--store",
                    "fake",
                    "--dry-run",
                    "--format",
                    "json",
                    "--as-of",
                    AS_OF.isoformat(),
                ]
            )

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        index_audit = payload["hard_negative_index"]
        self.assertEqual(index_audit["candidate_count"], len(SYNTHETIC_HARD_NEGATIVE_CATALOG))
        self.assertEqual(
            set(index_audit["categories"]),
            {
                "wrong_dimension",
                "wrong_mode",
                "expired",
                "retired",
                "duplicate",
                "wrong_role",
                "low_trust",
            },
        )
        for intent, result in zip(self.intents, payload["intent_results"], strict=True):
            candidate_pool = set(result["candidate_pool"])
            self.assertTrue(set(intent.hard_negative_ids) <= candidate_pool)
            self.assertEqual(result["hard_negative_hits"], [])
            self.assertEqual(
                result["hard_negative_filter"]["indexed"],
                len(SYNTHETIC_HARD_NEGATIVE_CATALOG),
            )
            self.assertEqual(
                result["hard_negative_filter"]["filtered"],
                len(SYNTHETIC_HARD_NEGATIVE_CATALOG),
            )
            self.assertEqual(result["hard_negative_filter"]["eligible"], [])

    def test_local_cli_evaluates_twice_on_one_built_index(self) -> None:
        instances: list[object] = []

        class CountingLocalStore:
            def __init__(self, **kwargs: object) -> None:
                self.search_calls = 0
                self.rebuild_calls = 0
                self.inner = qdrant_store_module.QdrantQuestionStore(
                    client=QdrantClient(path=":memory:"),
                    fingerprint=kwargs["fingerprint"],
                    candidate_safe=kwargs["candidate_safe"],
                    authoritative_catalog=kwargs["authoritative_catalog"],
                )
                instances.append(self)

            def rebuild(self, records, vectors, fingerprint) -> None:
                self.rebuild_calls += 1
                self.inner.rebuild(records, vectors, fingerprint)

            def search(self, **kwargs):
                self.search_calls += 1
                return self.inner.search(**kwargs)

            def close(self) -> None:
                self.inner.close()

        stdout = io.StringIO()
        fail = AssertionError("local calibration must not construct a paid provider")
        with patch.object(run_question_bank, "QdrantQuestionStore", CountingLocalStore), patch.object(
            run_question_bank, "SiliconFlowEmbeddingClient", side_effect=fail
        ), patch.object(httpx, "Client", side_effect=fail), patch.object(
            run_question_bank, "_write_corpus_artifact"
        ), redirect_stdout(stdout):
            code = run_question_bank.main(
                [
                    "evaluate-local",
                    "--corpus-dir",
                    str(CORPUS_DIR),
                    "--store",
                    "local",
                    "--qdrant-url",
                    "http://127.0.0.1:6333",
                    "--dry-run",
                    "--format",
                    "json",
                    "--as-of",
                    AS_OF.isoformat(),
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].rebuild_calls, 1)
        self.assertEqual(instances[0].search_calls, 60)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["repeatability"]["checked"])
        self.assertTrue(payload["repeatability"]["stable"])
        self.assertEqual(
            payload["repeatability"]["first_report_hash"],
            payload["repeatability"]["second_report_hash"],
        )

    def test_manifest_comparison_uses_an_independent_snapshot(self) -> None:
        snapshot = run_question_bank.load_question_corpus_snapshot(CORPUS_DIR, AS_OF)
        mutated_manifest = snapshot.manifest.model_copy(
            update={"manifest_version": "tampered-preview-v1"}
        )
        mutated_snapshot = snapshot.model_copy(
            deep=True,
            update={"manifest": mutated_manifest},
        )
        stdout = io.StringIO()
        with patch.object(
            run_question_bank,
            "load_question_corpus_snapshot",
            side_effect=[snapshot, mutated_snapshot],
        ), patch.object(run_question_bank, "_write_corpus_artifact"), redirect_stdout(stdout):
            code = run_question_bank.main(
                [
                    "evaluate-local",
                    "--corpus-dir",
                    str(CORPUS_DIR),
                    "--store",
                    "fake",
                    "--dry-run",
                    "--format",
                    "json",
                    "--as-of",
                    AS_OF.isoformat(),
                ]
            )

        self.assertNotEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["manifest_preview_comparison"]["matched"])
        self.assertEqual(payload["issues"][0]["code"], "manifest_preview_mismatch")

    def test_external_local_url_is_a_policy_error_not_unavailable(self) -> None:
        stdout = io.StringIO()
        fail = AssertionError("external local URL must be rejected before Qdrant construction")
        with patch.object(run_question_bank, "QdrantQuestionStore", side_effect=fail), redirect_stdout(stdout):
            code = run_question_bank.main(
                [
                    "evaluate-local",
                    "--corpus-dir",
                    str(CORPUS_DIR),
                    "--store",
                    "local",
                    "--qdrant-url",
                    "https://qdrant.example.com:6333",
                    "--dry-run",
                    "--format",
                    "json",
                    "--as-of",
                    AS_OF.isoformat(),
                ]
            )

        self.assertNotEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "invalid")
        self.assertEqual(payload["issues"][0]["code"], "local_url_policy_rejected")
        self.assertEqual(payload["local_qdrant"]["status"], "invalid")

    def test_local_cli_without_explicit_loopback_is_honest_unavailable(self) -> None:
        stdout = io.StringIO()
        fail = AssertionError("local unavailable path must not construct a client")
        with patch.object(run_question_bank, "QdrantQuestionStore", side_effect=fail), redirect_stdout(stdout):
            code = run_question_bank.main(
                [
                    "evaluate-local",
                    "--corpus-dir",
                    str(CORPUS_DIR),
                    "--store",
                    "local",
                    "--dry-run",
                    "--format",
                    "json",
                    "--as-of",
                    AS_OF.isoformat(),
                ]
            )
        self.assertNotEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["passed"])
        self.assertIn(payload["status"], {"unavailable", "invalid", "skipped"})
        self.assertNotEqual(payload.get("backend"), "deterministic-fake")
if __name__ == "__main__":
    unittest.main()
