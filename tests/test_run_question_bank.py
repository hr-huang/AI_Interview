from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import date
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_question_bank
from run_question_bank import QuestionBankDependencies, build_question_index_config
from profile_agent.services.question_bank_service import (
    EMBEDDING_TEXT_VERSION,
    build_question_embedding_text,
    load_question_bank,
)


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "question_rag" / "minimal_question_bank.json"
)
LEGACY_V1_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "question_rag" / "legacy_v1_question.json"
)
LEGACY_MANIFEST_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "question_rag" / "legacy_v1_manifest.json"
)
MALFORMED_CORPUS_JSON_FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "question_corpus_v2"
    / "malformed_questions.json"
)


class FakeEmbedding:
    model = "fake-model"
    provider = "fake-provider"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.closed = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(index), 1.0, 2.0] for index, _ in enumerate(texts)]

    def close(self) -> None:
        self.closed = True


class FakeStore:
    def __init__(self) -> None:
        self.rebuild_calls: list[tuple[object, object, object]] = []
        self.sync_calls: list[tuple[object, object, object, object]] = []
        self.closed = False

    def rebuild(self, records, vectors, fingerprint) -> None:
        self.rebuild_calls.append((records, vectors, fingerprint))

    def sync(self, records, vectors, fingerprint, *, today) -> None:
        self.sync_calls.append((records, vectors, fingerprint, today))

    def close(self) -> None:
        self.closed = True


class ReadingEnv(dict):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.reads: list[str] = []

    def get(self, key, default=None):
        self.reads.append(str(key))
        return super().get(key, default)


class RunQuestionBankTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.bank_path = Path(self.temp_dir.name) / "question-bank.json"

    def _write_bank(self, *, test_only: bool = False) -> Path:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["test_only"] = test_only
        for question in payload["questions"]:
            question["source_type"] = (
                "test_only_synthetic" if test_only else "public_interview_experience"
            )
        self.bank_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        return self.bank_path

    def _run(self, argv: list[str], **kwargs) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = run_question_bank.main(argv, **kwargs)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_validate_is_read_only_and_does_not_construct_paid_clients(self) -> None:
        bank = self._write_bank()
        embedding_calls: list[bool] = []
        store_calls: list[bool] = []

        def embedding_factory(**_kwargs):
            embedding_calls.append(True)
            raise AssertionError("embedding must not be constructed")

        def store_factory(**_kwargs):
            store_calls.append(True)
            raise AssertionError("store must not be constructed")

        code, stdout, stderr = self._run(
            ["validate", "--bank", str(bank)],
            embedding_factory=embedding_factory,
            store_factory=store_factory,
        )

        self.assertEqual(code, 0)
        self.assertIn("VALID", stdout)
        self.assertIn("records=6", stdout)
        self.assertEqual(embedding_calls, [])
        self.assertEqual(store_calls, [])
        self.assertEqual(stderr, "")

    def test_audit_is_read_only_and_reports_lifecycle_counts(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["test_only"] = False
        for question in payload["questions"]:
            question["source_type"] = "public_interview_experience"
        payload["questions"][0]["valid_until"] = "2026-08-25"
        payload["questions"][1]["valid_until"] = "2026-09-02"
        payload["questions"][2]["status"] = "needs_review"
        payload["questions"][3]["status"] = "retired"
        self.bank_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        code, stdout, stderr = self._run(
            [
                "audit",
                "--bank",
                str(self.bank_path),
                "--as-of",
                "2026-08-26",
                "--format",
                "json",
            ],
            embedding_factory=lambda **_: self.fail("embedding must not be built"),
            store_factory=lambda **_: self.fail("store must not be built"),
        )

        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["action"], "audit")
        self.assertEqual(report["expired_count"], 1)
        self.assertEqual(report["expiring_soon_count"], 1)
        self.assertEqual(report["needs_review_count"], 1)
        self.assertEqual(report["retired_count"], 1)
        self.assertEqual(stderr, "")

    def test_rebuild_without_apply_is_a_dry_run_without_key_or_client(self) -> None:
        bank = self._write_bank()
        embedding_calls: list[bool] = []
        store_calls: list[bool] = []

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank)],
            env={"SILICONFLOW_API_KEY": ""},
            embedding_factory=lambda **_: embedding_calls.append(True),
            store_factory=lambda **_: store_calls.append(True),
        )

        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", stdout)
        self.assertIn("expected_writes", stdout)
        self.assertIn("collection=interview_questions", stdout)
        self.assertIn("model=BAAI/bge-m3", stdout)
        self.assertNotIn("如何设计 Agent", stdout)
        self.assertEqual(embedding_calls, [])
        self.assertEqual(store_calls, [])
        self.assertEqual(stderr, "")

    def test_dry_run_uses_runtime_canonical_index_defaults(self) -> None:
        bank = self._write_bank()

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--format", "json"]
        )

        self.assertEqual(code, 0)
        summary = json.loads(stdout)
        self.assertEqual(summary["model"], "BAAI/bge-m3")
        self.assertEqual(summary["index_version"], "questions-v1")
        self.assertEqual(stderr, "")

    def test_apply_uses_canonical_embedding_aliases_and_identity(self) -> None:
        bank = self._write_bank()
        store = FakeStore()
        factory_kwargs: dict[str, object] = {}

        class PlainEmbedding:
            def embed(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 2.0, 3.0] for _ in texts]

        def embedding_factory(**kwargs):
            factory_kwargs.update(kwargs)
            return PlainEmbedding()

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply", "--format", "json"],
            env={
                "QUESTION_RAG_EMBEDDING_MODEL": "canonical-model",
                "SILICONFLOW_EMBEDDING_MODEL": "legacy-model",
                "QUESTION_RAG_EMBEDDING_PROVIDER": "canonical-provider",
                "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
                "QUESTION_RAG_INDEX_VERSION": "canonical-index-v2",
            },
            embedding_factory=embedding_factory,
            store_factory=lambda **_: store,
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(store.rebuild_calls), 1)
        fingerprint = store.rebuild_calls[0][2]
        self.assertEqual(fingerprint.provider, "canonical-provider")
        self.assertEqual(fingerprint.model, "canonical-model")
        self.assertEqual(fingerprint.dimension, 3)
        self.assertEqual(fingerprint.index_version, "canonical-index-v2")
        self.assertEqual(factory_kwargs["model"], fingerprint.model)
        self.assertIn("canonical-model", stdout)
        self.assertEqual(stderr, "")

    def test_dry_run_never_loads_dotenv_or_reads_a_key_into_process(self) -> None:
        bank = self._write_bank()

        def malicious_dotenv_loader() -> None:
            os.environ["SILICONFLOW_API_KEY"] = "dotenv-secret-must-not-load"

        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": ""}, clear=False):
            with patch("dotenv.load_dotenv", side_effect=malicious_dotenv_loader) as loader:
                code, stdout, stderr = self._run(
                    ["rebuild", "--bank", str(bank)],
                )

            self.assertEqual(os.environ["SILICONFLOW_API_KEY"], "")

        self.assertEqual(code, 0)
        loader.assert_not_called()
        self.assertIn("DRY-RUN", stdout)
        self.assertEqual(stderr, "")

    def test_read_only_and_dry_run_never_read_injected_environment(self) -> None:
        bank = self._write_bank()
        environment = ReadingEnv(
            {
                "SILICONFLOW_API_KEY": "must-not-be-read",
                "SILICONFLOW_EMBEDDING_MODEL": "secret-model-setting",
            }
        )

        for argv in (
            ["validate", "--bank", str(bank)],
            ["audit", "--bank", str(bank)],
            ["rebuild", "--bank", str(bank)],
        ):
            with self.subTest(argv=argv):
                code, _stdout, _stderr = self._run(argv, env=environment)
                self.assertEqual(code, 0)

        self.assertEqual(environment.reads, [])

    def test_invalid_explicit_config_never_loads_dotenv(self) -> None:
        bank = self._write_bank()
        with patch("dotenv.load_dotenv") as loader:
            code, stdout, stderr = self._run(
                ["rebuild", "--bank", str(bank), "--apply", "--model", "  "],
                embedding_factory=lambda **_: self.fail("embedding must not be built"),
                store_factory=lambda **_: self.fail("store must not be built"),
            )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("配置错误", stderr)
        loader.assert_not_called()

    def test_rebuild_apply_embeds_then_mutates_store_with_fingerprint(self) -> None:
        bank = self._write_bank()
        embedding = FakeEmbedding()
        store = FakeStore()

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply", "--as-of", "2026-08-26"],
            env={
                "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
                "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
            },
            embedding_factory=lambda **_: embedding,
            store_factory=lambda **_: store,
        )

        self.assertEqual(code, 0)
        self.assertIn("APPLIED", stdout)
        self.assertEqual(len(embedding.calls), 1)
        self.assertEqual(len(store.rebuild_calls), 1)
        records, vectors, fingerprint = store.rebuild_calls[0]
        self.assertEqual(len(records), 6)
        self.assertEqual(len(vectors), 6)
        self.assertEqual(fingerprint.provider, "fake-provider")
        self.assertEqual(fingerprint.model, "fake-model")
        self.assertEqual(fingerprint.dimension, 3)
        self.assertFalse(embedding.closed)
        self.assertFalse(store.closed)
        self.assertEqual(stderr, "")

    def test_corpus_read_only_actions_are_zero_cost_and_write_audit_artifacts_only(self) -> None:
        from tests.test_question_corpus_governance import QuestionCorpusGovernanceTests

        corpus_dir = Path(self.temp_dir.name) / "corpus"
        corpus_dir.mkdir()
        QuestionCorpusGovernanceTests()._write_snapshot(corpus_dir)
        embedding_calls: list[bool] = []
        store_calls: list[bool] = []
        artifact_calls: list[Path] = []
        environment = ReadingEnv({"SILICONFLOW_API_KEY": "must-not-be-read"})

        with patch.object(
            run_question_bank,
            "_write_corpus_artifact",
            side_effect=lambda path, _payload: artifact_calls.append(path),
        ):
            for action in ("audit-corpus", "manifest", "evaluate-local"):
                with self.subTest(action=action):
                    code, stdout, stderr = self._run(
                        [
                            action,
                            "--corpus-dir",
                            str(corpus_dir),
                            "--as-of",
                            "2026-08-27",
                            "--dry-run",
                            "--format",
                            "json",
                        ],
                        env=environment,
                        embedding_factory=lambda **_: embedding_calls.append(True),
                        store_factory=lambda **_: store_calls.append(True),
                    )
                    self.assertNotEqual(code, 2)
                    self.assertIn(action, stdout)
                    self.assertEqual(stderr, "")

        self.assertEqual(embedding_calls, [])
        self.assertEqual(store_calls, [])
        self.assertEqual(environment.reads, [])
        self.assertEqual(
            [path.name for path in artifact_calls],
            [
                "manifest_preview.json",
                "validation_report.json",
            ] * 3,
        )

    def test_corpus_malformed_snapshot_writes_structured_failure_artifacts(self) -> None:
        corpus_dir = Path(self.temp_dir.name) / "malformed-corpus"
        corpus_dir.mkdir()
        (corpus_dir / "questions.json").write_text(
            MALFORMED_CORPUS_JSON_FIXTURE_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        artifacts: dict[str, object] = {}

        def capture_artifact(path: Path, payload: object) -> None:
            artifacts[path.name] = payload

        with patch.object(
            run_question_bank,
            "_write_corpus_artifact",
            side_effect=capture_artifact,
        ):
            code, stdout, stderr = self._run(
                [
                    "audit-corpus",
                    "--corpus-dir",
                    str(corpus_dir),
                    "--as-of",
                    "2026-08-27",
                    "--dry-run",
                    "--format",
                    "json",
                ]
            )

        self.assertNotEqual(code, 0)
        self.assertIn("audit-corpus", stdout)
        self.assertEqual(stderr, "")
        self.assertEqual(
            set(artifacts),
            {"manifest_preview.json", "validation_report.json"},
        )
        report = artifacts["validation_report.json"]
        preview = artifacts["manifest_preview.json"]
        self.assertIsInstance(report, dict)
        self.assertIsInstance(preview, dict)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["stage"], "structure")
        self.assertTrue(report["issues"])
        self.assertEqual(preview["status"], "invalid")
        self.assertFalse(preview["structure_valid"])

    def test_corpus_malformed_snapshot_writes_real_artifacts_without_writer_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "malformed-corpus"
            root.mkdir()
            (root / "questions.json").write_text(
                MALFORMED_CORPUS_JSON_FIXTURE_PATH.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            original_cwd = os.getcwd()
            os.chdir(temp)
            try:
                code, stdout, stderr = self._run(
                    [
                        "audit-corpus",
                        "--corpus-dir",
                        str(root),
                        "--as-of",
                        "2026-08-27",
                        "--dry-run",
                        "--format",
                        "json",
                    ]
                )
            finally:
                os.chdir(original_cwd)

            self.assertNotEqual(code, 0)
            self.assertIn("audit-corpus", stdout)
            self.assertEqual(stderr, "")
            report_path = Path(temp) / "artifacts" / "question_corpus" / "validation_report.json"
            preview_path = Path(temp) / "artifacts" / "question_corpus" / "manifest_preview.json"
            self.assertTrue(report_path.is_file())
            self.assertTrue(preview_path.is_file())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            preview = json.loads(preview_path.read_text(encoding="utf-8"))
            self.assertEqual(report["stage"], "structure")
            self.assertEqual(report["issues"][0]["code"], "structure_invalid_json")
            self.assertFalse(preview["structure_valid"])

    def test_apply_embedding_spy_receives_canonical_six_section_projection(self) -> None:
        bank = self._write_bank()
        embedding = FakeEmbedding()
        store = FakeStore()

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply"],
            env={
                "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
                "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
            },
            embedding_factory=lambda **_: embedding,
            store_factory=lambda **_: store,
        )

        self.assertEqual(code, 0)
        records = load_question_bank(bank)
        self.assertEqual(
            embedding.calls,
            [[build_question_embedding_text(record) for record in records]],
        )
        self.assertTrue(all(text.count("\n") == 5 for text in embedding.calls[0]))
        fingerprint = store.rebuild_calls[0][2]
        self.assertEqual(fingerprint.embedding_text_version, EMBEDDING_TEXT_VERSION)
        self.assertTrue(fingerprint.question_bank_manifest_hash.startswith("sha256:"))
        self.assertEqual(fingerprint.mode_policy_version, "2026-H2")
        self.assertEqual(stdout.count("APPLIED"), 1)
        self.assertEqual(stderr, "")

    def test_apply_rejects_sensitive_projection_before_embedding_spy(self) -> None:
        bank = self._write_bank()
        records = load_question_bank(bank)
        unsafe = records[0].model_copy(
            update={"business_constraint": "电话：13800138000"}
        )
        embedding_calls: list[bool] = []
        store_calls: list[bool] = []

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply"],
            env={
                "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
                "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
            },
            bank_loader=lambda *_args, **_kwargs: [unsafe],
            embedding_factory=lambda **_: embedding_calls.append(True),
            store_factory=lambda **_: store_calls.append(True),
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(embedding_calls, [])
        self.assertEqual(store_calls, [])
        self.assertNotIn("13800138000", stderr)

    def test_apply_accepts_legacy_v1_manifest_fixture(self) -> None:
        payload = json.loads(LEGACY_V1_FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["test_only"] = False
        payload["questions"][0]["source_type"] = "public_interview_experience"
        self.bank_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        self.bank_path.with_name("QuestionBankManifest.json").write_text(
            LEGACY_MANIFEST_FIXTURE_PATH.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        embedding = FakeEmbedding()
        store = FakeStore()

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(self.bank_path), "--apply"],
            env={
                "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
                "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
            },
            embedding_factory=lambda **_: embedding,
            store_factory=lambda **_: store,
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(embedding.calls), 1)
        self.assertEqual(len(store.rebuild_calls), 1)
        self.assertTrue(store.rebuild_calls[0][2].question_bank_manifest_hash.startswith("sha256:"))
        self.assertEqual(stderr, "")

    def test_sync_apply_passes_explicit_today_and_calls_only_sync(self) -> None:
        bank = self._write_bank()
        embedding = FakeEmbedding()
        store = FakeStore()

        code, stdout, stderr = self._run(
            ["sync", "--bank", str(bank), "--apply", "--as-of", "2026-08-26"],
            env={
                "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
                "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
            },
            embedding_factory=lambda **_: embedding,
            store_factory=lambda **_: store,
        )

        self.assertEqual(code, 0)
        self.assertIn("APPLIED", stdout)
        self.assertEqual(store.rebuild_calls, [])
        self.assertEqual(len(store.sync_calls), 1)
        self.assertEqual(store.sync_calls[0][3], date(2026, 8, 26))
        self.assertEqual(stderr, "")

    def test_audit_uses_permissive_raw_records_for_source_and_duplicate_diagnostics(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["test_only"] = False
        for question in payload["questions"]:
            question["source_type"] = "public_interview_experience"

        missing_source = payload["questions"][0]
        missing_source["source_id"] = ""
        missing_source["source_url"] = ""
        invalid_record = payload["questions"][1]
        invalid_record.pop("expected_signals")
        duplicate_id = payload["questions"][2]
        duplicate_id["question_id"] = payload["questions"][3]["question_id"]
        duplicate_hash_a = payload["questions"][4]
        duplicate_hash_b = payload["questions"][5]
        duplicate_hash_b["content_hash"] = duplicate_hash_a["content_hash"]
        self.bank_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        code, stdout, stderr = self._run(
            [
                "audit",
                "--bank",
                str(self.bank_path),
                "--as-of",
                "2026-08-26",
                "--format",
                "json",
            ]
        )

        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertGreaterEqual(report["invalid_record_count"], 1)
        self.assertEqual(report["missing_source_count"], 1)
        self.assertGreaterEqual(report["duplicate_question_id_count"], 1)
        self.assertGreaterEqual(report["duplicate_content_hash_count"], 2)
        self.assertNotIn(missing_source["question_id"], report["eligible_ids"])
        self.assertNotIn(invalid_record["question_id"], report["eligible_ids"])
        self.assertEqual(stderr, "")

    def test_audit_keeps_non_object_items_as_invalid_records(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["test_only"] = False
        for question in payload["questions"]:
            question["source_type"] = "public_interview_experience"
        payload["questions"][0] = None
        self.bank_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        code, stdout, stderr = self._run(
            ["audit", "--bank", str(self.bank_path), "--format", "json"]
        )

        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertGreaterEqual(report["invalid_record_count"], 1)
        self.assertIn("<record:0>", report["invalid_record_ids"])
        self.assertEqual(stderr, "")

    def test_audit_rejects_record_role_version_drift_from_bank_root(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["test_only"] = False
        for question in payload["questions"]:
            question["source_type"] = "public_interview_experience"
        payload["questions"][0]["role_version"] = "different-role-version"
        self.bank_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        code, stdout, stderr = self._run(
            ["audit", "--bank", str(self.bank_path), "--format", "json"]
        )

        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertGreaterEqual(report["invalid_record_count"], 1)
        self.assertEqual(report["role_version_mismatch_count"], 1)
        self.assertNotIn("different-role-version", stdout)
        self.assertLess(report["eligible_count"], report["records"])
        self.assertEqual(stderr, "")

    def test_audit_classifies_malformed_source_url_as_invalid_source(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["test_only"] = False
        for question in payload["questions"]:
            question["source_type"] = "public_interview_experience"
        payload["questions"][0]["source_url"] = "http://["
        self.bank_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        code, stdout, stderr = self._run(
            ["audit", "--bank", str(self.bank_path), "--format", "json"]
        )

        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["invalid_source_count"], 1)
        self.assertNotIn("http://[", stdout)
        self.assertNotEqual(report["status"], "error")
        self.assertEqual(stderr, "")

    def test_audit_never_echoes_untrusted_ids_or_statuses(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["test_only"] = False
        for question in payload["questions"]:
            question["source_type"] = "public_interview_experience"
        sensitive_id = "api-key=do-not-print-credential-value"
        sensitive_source_id = "source-secret=do-not-print-source-id"
        sensitive_source_url = "https://secret.example/should-never-appear"
        sensitive_text = "question-snippet=do-not-print-question-text"
        sensitive_status = "opaque-status-do-not-print"
        payload["questions"][0]["question_id"] = sensitive_id
        payload["questions"][0]["source_id"] = sensitive_source_id
        payload["questions"][0]["source_url"] = sensitive_source_url
        payload["questions"][0]["question_text"] = sensitive_text
        payload["questions"][0]["status"] = sensitive_status
        self.bank_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        code, stdout, stderr = self._run(
            ["audit", "--bank", str(self.bank_path), "--format", "json"]
        )

        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertNotIn(sensitive_id, stdout)
        self.assertNotIn(sensitive_source_id, stdout)
        self.assertNotIn(sensitive_source_url, stdout)
        self.assertNotIn(sensitive_text, stdout)
        self.assertNotIn(sensitive_status, stdout)
        self.assertEqual(set(report["status_counts"]).difference(
            {"active", "needs_review", "retired", "invalid"}
        ), set())
        self.assertEqual(stderr, "")

    def test_empty_bank_fails_validate_dry_run_and_apply_before_dependencies(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["test_only"] = False
        payload["questions"] = []
        self.bank_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        embedding_calls: list[bool] = []
        store_calls: list[bool] = []

        def embedding_factory(**_kwargs):
            embedding_calls.append(True)
            return FakeEmbedding()

        def store_factory(**_kwargs):
            store_calls.append(True)
            return FakeStore()

        for argv in (
            ["validate", "--bank", str(self.bank_path)],
            ["rebuild", "--bank", str(self.bank_path)],
            ["rebuild", "--bank", str(self.bank_path), "--apply"],
        ):
            with self.subTest(argv=argv):
                code, _stdout, _stderr = self._run(
                    argv,
                    embedding_factory=embedding_factory,
                    store_factory=store_factory,
                )
                self.assertEqual(code, 2)

        self.assertEqual(embedding_calls, [])
        self.assertEqual(store_calls, [])

    def test_invalid_apply_options_fail_before_embedding_or_store(self) -> None:
        bank = self._write_bank()
        embedding = FakeEmbedding()
        store = FakeStore()
        invalid_path = Path(self.temp_dir.name) / "not-a-directory"
        invalid_path.write_text("occupied", encoding="utf-8")

        for option in (
            ["--index-version", "   "],
            ["--model", "   "],
            ["--index-path", str(invalid_path)],
        ):
            with self.subTest(option=option):
                code, _stdout, _stderr = self._run(
                    ["rebuild", "--bank", str(bank), "--apply", *option],
                    embedding_factory=lambda **_: embedding,
                    store_factory=lambda **_: store,
                )
                self.assertEqual(code, 2)

        self.assertEqual(embedding.calls, [])
        self.assertEqual(store.rebuild_calls, [])

    def test_dimension_mismatch_fails_before_store(self) -> None:
        bank = self._write_bank()
        embedding = FakeEmbedding()
        store_calls: list[bool] = []

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply", "--dimension", "4"],
            env={
                "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
            },
            embedding_factory=lambda **_: embedding,
            store_factory=lambda **_: store_calls.append(True),
        )

        self.assertEqual(code, 2)
        self.assertEqual(len(embedding.calls), 1)
        self.assertEqual(store_calls, [])
        self.assertEqual(stdout, "")
        self.assertIn("配置错误", stderr)

    def test_invalid_fingerprint_fails_before_store(self) -> None:
        bank = self._write_bank()
        embedding = FakeEmbedding()
        store_calls: list[bool] = []

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply"],
            env={
                "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
                "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
            },
            embedding_factory=lambda **_: embedding,
            fingerprint_factory=lambda **_: object(),
            store_factory=lambda **_: store_calls.append(True),
        )

        self.assertEqual(code, 2)
        self.assertEqual(len(embedding.calls), 1)
        self.assertEqual(store_calls, [])
        self.assertEqual(stdout, "")
        self.assertIn("配置错误", stderr)

    def test_fingerprint_dimension_drift_fails_before_store(self) -> None:
        bank = self._write_bank()
        embedding = FakeEmbedding()
        store_calls: list[bool] = []

        def drifted_fingerprint(**kwargs):
            return {
                **kwargs,
                "dimension": kwargs["dimension"] + 1,
            }

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply"],
            env={
                "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
                "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
            },
            embedding_factory=lambda **_: embedding,
            fingerprint_factory=drifted_fingerprint,
            store_factory=lambda **_: store_calls.append(True),
        )

        self.assertEqual(code, 2)
        self.assertEqual(len(embedding.calls), 1)
        self.assertEqual(store_calls, [])
        self.assertEqual(stdout, "")
        self.assertIn("配置错误", stderr)

    def test_invalid_environment_path_fails_before_embedding(self) -> None:
        bank = self._write_bank()
        occupied_path = Path(self.temp_dir.name) / "occupied-index-path"
        occupied_path.write_text("not a directory", encoding="utf-8")
        embedding = FakeEmbedding()
        store_calls: list[bool] = []

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply"],
            env={
                "QUESTION_RAG_INDEX_PATH": str(occupied_path),
            },
            embedding_factory=lambda **_: embedding,
            store_factory=lambda **_: store_calls.append(True),
        )

        self.assertEqual(code, 2)
        self.assertEqual(embedding.calls, [])
        self.assertEqual(store_calls, [])
        self.assertEqual(stdout, "")
        self.assertIn("配置错误", stderr)

    def test_malformed_base_url_is_configuration_error_before_embedding(self) -> None:
        bank = self._write_bank()
        embedding = FakeEmbedding()
        store_calls: list[bool] = []

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply"],
            env={"SILICONFLOW_EMBEDDING_BASE_URL": "http://["},
            embedding_factory=lambda **_: embedding,
            store_factory=lambda **_: store_calls.append(True),
        )

        self.assertEqual(code, 2)
        self.assertEqual(embedding.calls, [])
        self.assertEqual(store_calls, [])
        self.assertEqual(stdout, "")
        self.assertIn("配置错误", stderr)

    def test_date_parser_is_strict_and_extreme_ranges_are_argument_errors(self) -> None:
        bank = self._write_bank()
        invalid_dates = ("20260826", "2026-W34-3", "2026-1-01", "0000-01-01")
        for value in invalid_dates:
            with self.subTest(value=value):
                code, _stdout, _stderr = self._run(
                    ["audit", "--bank", str(bank), "--as-of", value]
                )
                self.assertEqual(code, 2)

        for value in ("9999-12-31",):
            with self.subTest(value=value):
                code, _stdout, _stderr = self._run(
                    ["audit", "--bank", str(bank), "--as-of", value]
                )
                self.assertEqual(code, 2)

        code, _stdout, _stderr = self._run(
            [
                "audit",
                "--bank",
                str(bank),
                "--as-of",
                "2026-08-26",
                "--expiring-within-days",
                str(10**100),
            ]
        )
        self.assertEqual(code, 2)

    def test_invalid_bank_fails_before_embedding_or_store_construction(self) -> None:
        bank = self._write_bank()
        payload = json.loads(bank.read_text(encoding="utf-8"))
        payload["questions"][0]["content_hash"] = "sha256:invalid"
        bank.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        embedding_calls: list[bool] = []
        store_calls: list[bool] = []

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply"],
            embedding_factory=lambda **_: embedding_calls.append(True),
            store_factory=lambda **_: store_calls.append(True),
        )

        self.assertEqual(code, 2)
        self.assertEqual(embedding_calls, [])
        self.assertEqual(store_calls, [])
        self.assertIn("question bank", stderr.lower())
        self.assertNotIn("如何设计 Agent", stderr)
        self.assertEqual(stdout, "")

    def test_synthetic_test_only_bank_is_rejected_in_production(self) -> None:
        bank = self._write_bank(test_only=True)
        embedding_calls: list[bool] = []
        store_calls: list[bool] = []

        code, stdout, stderr = self._run(
            ["validate", "--bank", str(bank)],
            embedding_factory=lambda **_: embedding_calls.append(True),
            store_factory=lambda **_: store_calls.append(True),
        )

        self.assertEqual(code, 2)
        self.assertIn("test-only", stderr.lower())
        self.assertEqual(embedding_calls, [])
        self.assertEqual(store_calls, [])
        self.assertEqual(stdout, "")

    def test_synthetic_bank_requires_and_accepts_explicit_test_dependency(self) -> None:
        bank = self._write_bank(test_only=True)

        code, stdout, stderr = self._run(
            ["validate", "--bank", str(bank), "--format", "json"],
            dependencies=QuestionBankDependencies(test_dependency=object()),
        )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["records"], 6)
        self.assertEqual(stderr, "")

    def test_default_dimension_is_enforced_for_injected_embedding(self) -> None:
        bank = self._write_bank()
        embedding = FakeEmbedding()
        embedding.dimension = 3
        store_calls: list[bool] = []

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply"],
            env={
                "QUESTION_RAG_EMBEDDING_PROVIDER": "fake-provider",
                "QUESTION_RAG_EMBEDDING_MODEL": "fake-model",
            },
            embedding_factory=lambda **_: embedding,
            store_factory=lambda **_: store_calls.append(True),
        )

        self.assertEqual(code, 2)
        self.assertEqual(len(embedding.calls), 0)
        self.assertEqual(store_calls, [])
        self.assertEqual(stdout, "")
        self.assertIn("配置错误", stderr)

    def test_declared_embedding_identity_drift_fails_before_paid_embed(self) -> None:
        bank = self._write_bank()
        store_calls: list[bool] = []

        class DriftedEmbedding:
            provider = "unexpected-provider"
            model = "unexpected-model"
            dimension = 3

            def embed(self, texts: list[str]) -> list[list[float]]:
                raise AssertionError("identity drift must fail before embed")

        code, stdout, stderr = self._run(
            ["rebuild", "--bank", str(bank), "--apply"],
            env={
                "QUESTION_RAG_EMBEDDING_PROVIDER": "configured-provider",
                "QUESTION_RAG_EMBEDDING_MODEL": "configured-model",
                "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
            },
            embedding_factory=lambda **_: DriftedEmbedding(),
            store_factory=lambda **_: store_calls.append(True),
        )

        self.assertEqual(code, 2)
        self.assertEqual(store_calls, [])
        self.assertEqual(stdout, "")
        self.assertIn("配置错误", stderr)

    def test_canonical_config_rejects_non_integer_dimension_values(self) -> None:
        with self.assertRaises(run_question_bank.QuestionBankConfigurationError):
            build_question_index_config(
                {},
                index_path=Path(self.temp_dir.name),
                dimension=3.5,
            )

    def test_runtime_expected_fingerprint_uses_shared_resolver(self) -> None:
        from profile_agent.knowledge import qdrant_question_store
        from profile_agent.services import question_retrieval_service
        from profile_agent.services import siliconflow_embedding_service
        from profile_agent.web import container

        index_path = Path(self.temp_dir.name) / "runtime-index"
        index_path.mkdir()
        with patch.dict(
            os.environ,
            {
                "QUESTION_RAG_INDEX_PATH": str(index_path),
                "QUESTION_RAG_EMBEDDING_MODEL": "question-runtime-model",
                "SILICONFLOW_EMBEDDING_MODEL": "legacy-runtime-model",
                "QUESTION_RAG_EMBEDDING_PROVIDER": "question-runtime-provider",
                "QUESTION_RAG_EMBEDDING_DIMENSION": "3",
                "QUESTION_RAG_INDEX_VERSION": "question-runtime-v2",
                "SILICONFLOW_EMBEDDING_BASE_URL": "https://runtime.example/v1",
                "SILICONFLOW_API_KEY": "runtime-fake-key",
            },
            clear=False,
        ):
            with patch.object(
                siliconflow_embedding_service,
                "SiliconFlowEmbeddingClient",
            ) as embedding_cls, patch.object(
                qdrant_question_store,
                "QdrantQuestionStore",
            ) as store_cls, patch.object(
                question_retrieval_service,
                "QuestionRetriever",
            ) as retriever_cls:
                embedding_cls.return_value = object()
                store_cls.return_value = object()
                retriever_cls.return_value = object()

                factory = container._question_retriever_factory_from_env()
                self.assertIsNotNone(factory)
                factory()

        embedding_cls.from_env.assert_called_once_with()
        store_kwargs = store_cls.call_args.kwargs
        fingerprint = store_kwargs["expected_fingerprint"]
        self.assertEqual(fingerprint.model, "question-runtime-model")
        self.assertEqual(fingerprint.provider, "question-runtime-provider")
        self.assertEqual(fingerprint.dimension, 3)
        self.assertEqual(fingerprint.index_version, "question-runtime-v2")

    def test_argument_errors_do_not_echo_untrusted_input(self) -> None:
        secret = "api-key=argument-secret-must-not-print"

        code, stdout, stderr = self._run(
            ["audit", "--bank", str(self.bank_path), "--format", secret]
        )

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertNotIn(secret, stderr)

    def test_apply_without_key_returns_secret_safe_configuration_error(self) -> None:
        bank = self._write_bank()
        secret = "test-siliconflow-secret"
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": secret}, clear=False):
            code, stdout, stderr = self._run(
                ["rebuild", "--bank", str(bank), "--apply"],
                env={"SILICONFLOW_API_KEY": ""},
            )

        self.assertEqual(code, 2)
        self.assertIn("SILICONFLOW_API_KEY", stderr)
        self.assertNotIn(secret, stdout + stderr)
        self.assertEqual(stdout, "")

    def test_provider_failure_does_not_echo_key_or_error_details(self) -> None:
        bank = self._write_bank()
        secret = "provider-secret-value"

        def failing_factory(**_kwargs):
            raise RuntimeError(f"request Authorization Bearer {secret}")

        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": secret}, clear=False):
            code, stdout, stderr = self._run(
                ["rebuild", "--bank", str(bank), "--apply"],
                embedding_factory=failing_factory,
            )

        self.assertEqual(code, 1)
        self.assertIn("RuntimeError", stderr)
        self.assertNotIn(secret, stdout + stderr)
        self.assertNotIn("Bearer", stderr)


if __name__ == "__main__":
    unittest.main()
