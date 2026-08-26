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
from run_question_bank import QuestionBankDependencies


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "question_rag" / "minimal_question_bank.json"
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

    def test_sync_apply_passes_explicit_today_and_calls_only_sync(self) -> None:
        bank = self._write_bank()
        embedding = FakeEmbedding()
        store = FakeStore()

        code, stdout, stderr = self._run(
            ["sync", "--bank", str(bank), "--apply", "--as-of", "2026-08-26"],
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
            embedding_factory=lambda **_: embedding,
            fingerprint_factory=lambda **_: object(),
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
