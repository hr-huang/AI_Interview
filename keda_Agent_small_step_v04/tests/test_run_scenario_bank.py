from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import run_scenario_bank
from profile_agent.schemas.scenario_calibration_schema import ScenarioCalibrationReport
from profile_agent.schemas.scenario_rag_schema import ScenarioCandidateSet


class RunScenarioBankTests(TestCase):
    def test_validate_is_offline_and_reports_frozen_counts(self) -> None:
        with patch(
            "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
            side_effect=AssertionError("validate must not construct provider"),
        ):
            result = run_scenario_bank.main(["validate"])
        self.assertEqual(result, 0)

    def test_rebuild_requires_scenario_qdrant_configuration(self) -> None:
        with (
            patch.dict(run_scenario_bank.os.environ, {}, clear=True),
            patch.object(run_scenario_bank, "load_dotenv") as load_dotenv,
        ):
            result = run_scenario_bank.main(["rebuild-index", "--apply"])
        self.assertEqual(result, 2)
        load_dotenv.assert_called_once_with()

    def test_rebuild_preview_does_not_construct_provider(self) -> None:
        with patch(
            "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
            side_effect=AssertionError("preview must not construct provider"),
        ):
            result = run_scenario_bank.main(["rebuild-index"])
        self.assertEqual(result, 0)

    def test_evaluate_preview_reports_24_cases_and_provider_call_estimates(self) -> None:
        output = StringIO()
        with (
            patch(
                "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
                side_effect=AssertionError("preview must not construct provider"),
            ),
            redirect_stdout(output),
        ):
            result = run_scenario_bank.main(["evaluate"])
        self.assertEqual(result, 0)
        self.assertIn("正常命中预计 24 次 rerank calls", output.getvalue())
        self.assertIn("最多 24 次", output.getvalue())

    def test_scenario_rag_artifacts_are_ignored(self) -> None:
        gitignore = Path(__file__).resolve().parents[1] / ".gitignore"
        self.assertIn("artifacts/scenario_rag/", gitignore.read_text(encoding="utf-8"))

    def test_evaluate_apply_writes_safe_run_metadata_with_fake_providers(self) -> None:
        class FakeEmbedding:
            provider = "fake-embedding-provider"
            model = "fake-embedding-model"

            def close(self) -> None:
                pass

        class FakeReranker:
            provider = "fake-reranker-provider"
            model = "fake-reranker-model"

            def close(self) -> None:
                pass

        class FakeStore:
            def __init__(self, *, embedding_client, client) -> None:
                self.embedding_client = embedding_client
                self.client = client

            def load_catalog(self, catalog) -> None:
                self.catalog = catalog

            def search(self, request, *, as_of, limit, reranker):
                return ScenarioCandidateSet(status="no_match")

            def close(self) -> None:
                pass

        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            output = StringIO()
            with (
                patch.dict(
                    run_scenario_bank.os.environ,
                    {"SCENARIO_RAG_INDEX_PATH": "fake-index"},
                    clear=True,
                ),
                patch.object(run_scenario_bank, "SCENARIO_CALIBRATION_ARTIFACT_ROOT", artifact_root),
                patch.object(run_scenario_bank, "load_dotenv"),
                patch.object(run_scenario_bank, "_qdrant_client_from_env", return_value=object()),
                patch(
                    "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
                    return_value=FakeEmbedding(),
                ),
                patch(
                    "profile_agent.knowledge.qdrant_scenario_store.QdrantScenarioStore",
                    FakeStore,
                ),
                patch(
                    "profile_agent.services.siliconflow_rerank_service.SiliconFlowRerankClient.from_env",
                    return_value=FakeReranker(),
                ),
                redirect_stdout(output),
            ):
                result = run_scenario_bank.main(["evaluate", "--apply"])

            self.assertEqual(result, 1)
            self.assertIn("forbidden_top1=0", output.getvalue())
            self.assertIn("top3_forbidden_diagnostic=0", output.getvalue())
            self.assertIn("fallback=24", output.getvalue())
            self.assertIn("gate=FAIL", output.getvalue())
            report_path = artifact_root / "scenario_retrieval_report.json"
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            metadata = payload["metadata"]
            self.assertEqual(metadata["embedding_provider"], "fake-embedding-provider")
            self.assertEqual(metadata["embedding_model"], "fake-embedding-model")
            self.assertEqual(metadata["reranker_provider"], "fake-reranker-provider")
            self.assertEqual(metadata["reranker_model"], "fake-reranker-model")
            self.assertEqual(metadata["qdrant_collection"], "scenario_modules")
            self.assertEqual(metadata["qdrant_index_version"], "scenario-modules-v1")
            self.assertEqual(metadata["bank_version"], 1)
            self.assertEqual(metadata["role_profile_version"], "2026-H2")
            self.assertEqual(metadata["as_of"], "2026-08-31")
            serialized = json.dumps(payload, ensure_ascii=False).lower()
            for sensitive in ("api_key", "token", "authorization", "base_url", "fake-index"):
                self.assertNotIn(sensitive, serialized)

    def test_evaluate_apply_returns_zero_when_gate_passes_with_top3_diagnostic(self) -> None:
        class FakeEmbedding:
            provider = "fake-embedding-provider"
            model = "fake-embedding-model"

            def close(self) -> None:
                pass

        class FakeReranker:
            provider = "fake-reranker-provider"
            model = "fake-reranker-model"

            def close(self) -> None:
                pass

        class FakeStore:
            def __init__(self, *, embedding_client, client) -> None:
                self.embedding_client = embedding_client
                self.client = client

            def load_catalog(self, catalog) -> None:
                self.catalog = catalog

            def close(self) -> None:
                pass

        passing_report = ScenarioCalibrationReport(
            case_count=0,
            top1_acceptable_rate=1.0,
            top3_recall=1.0,
            forbidden_hit_count=1,
            forbidden_top1_hit_count=0,
            fallback_count=0,
            case_results=[],
        )

        with TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            output = StringIO()
            with (
                patch.dict(
                    run_scenario_bank.os.environ,
                    {"SCENARIO_RAG_INDEX_PATH": "fake-index"},
                    clear=True,
                ),
                patch.object(run_scenario_bank, "SCENARIO_CALIBRATION_ARTIFACT_ROOT", artifact_root),
                patch.object(run_scenario_bank, "load_dotenv"),
                patch.object(run_scenario_bank, "_qdrant_client_from_env", return_value=object()),
                patch(
                    "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
                    return_value=FakeEmbedding(),
                ),
                patch(
                    "profile_agent.knowledge.qdrant_scenario_store.QdrantScenarioStore",
                    FakeStore,
                ),
                patch(
                    "profile_agent.services.siliconflow_rerank_service.SiliconFlowRerankClient.from_env",
                    return_value=FakeReranker(),
                ),
                patch.object(
                    run_scenario_bank,
                    "evaluate_scenario_retrieval",
                    return_value=passing_report,
                ),
                redirect_stdout(output),
            ):
                result = run_scenario_bank.main(["evaluate", "--apply"])

            self.assertEqual(result, 0)
            self.assertIn("forbidden_top1=0", output.getvalue())
            self.assertIn("top3_forbidden_diagnostic=1", output.getvalue())
            self.assertIn("fallback=0", output.getvalue())
            self.assertIn("gate=PASS", output.getvalue())
            self.assertTrue((artifact_root / "scenario_retrieval_report.json").exists())


if __name__ == "__main__":
    import unittest

    unittest.main()
