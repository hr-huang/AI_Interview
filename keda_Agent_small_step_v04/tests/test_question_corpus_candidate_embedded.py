from pathlib import Path
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_question_bank as cli


def _args(tmp_path, *extra):
    return ["evaluate-candidate-embedded", "--bank", str(tmp_path / "bank.json"),
            "--source-registry", str(tmp_path / "sources.json"),
            "--manifest", str(tmp_path / "manifest.json"),
            "--intents", str(tmp_path / "intents.jsonl"),
            "--index-path", str(tmp_path / "tmp-candidate-qdrant"),
            "--artifact", str(tmp_path / "candidate-artifact.json"), *extra]


class CandidateEmbeddedTests(unittest.TestCase):
    def test_candidate_paths_reject_formal_qdrant_and_require_marker(self):
        with self.assertRaises(cli.QuestionBankConfigurationError):
            cli._validate_candidate_embedded_path(Path("data/question_rag/qdrant"), label="index path")
        with self.assertRaises(cli.QuestionBankConfigurationError):
            cli._validate_candidate_embedded_path(Path("index"), label="index path")
        self.assertTrue(cli._validate_candidate_embedded_path(Path("tmp/candidate-qdrant"), label="index path"))


    def test_probe_uses_qdrant_hit_shape(self):
        self.assertTrue(cli._candidate_probe_hit(SimpleNamespace(status="hit", results=[])))
        self.assertFalse(cli._candidate_probe_hit(SimpleNamespace(status="matched", results=[])))

    def test_gate_fails_when_evaluation_passes_but_probe_fails(self):
        report = SimpleNamespace(passed=True)
        self.assertFalse(cli._candidate_gate_passed(report, [{"status": "failed"}], SimpleNamespace(status="no_match")))

    def test_gate_fails_when_evaluation_passes_but_no_match_fails(self):
        report = SimpleNamespace(passed=True)
        self.assertFalse(cli._candidate_gate_passed(report, [{"status": "pass"}], SimpleNamespace(status="hit")))

    def test_precomputed_provider_preserves_trace_without_embedding(self):
        class Store:
            def search(self, **kwargs):
                record = SimpleNamespace(primary_mode="screening", question_mode="screening")
                hit = SimpleNamespace(record=record, question_id="q1", source_id="s1", score=0.9, index_version="candidate")
                return SimpleNamespace(hits=[hit], status="hit", index_version="candidate")
        labeled = SimpleNamespace(intent_id="i1")
        runtime = SimpleNamespace(question_mode="screening")
        result = cli._candidate_embedded_result_provider(Store(), {"i1": [1.0]}, as_of=date.today())(labeled, runtime)
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["trace"]["question_id"], "q1")
        self.assertEqual(result["trace"]["source_id"], "s1")
        self.assertEqual(result["trace"]["score"], 0.9)
        self.assertEqual(result["trace"]["index_version"], "candidate")
        self.assertEqual(result["trace"]["match_tier"], "exact")
        self.assertEqual(result["trace"]["query_vector_source"], "precomputed-batch")
        self.assertEqual(result["hits"][0]["question_id"], "q1")

    def test_candidate_default_is_dry_run_and_never_constructs_provider(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            records = [SimpleNamespace(status="needs_review") for _ in range(30)]
            runtime = SimpleNamespace(policy=SimpleNamespace(mode_policy_version="2026-H2"), catalog={}, manifest_hash="m")
            with patch.object(cli, "_call_loader", return_value=records), \
                 patch.object(cli, "load_question_bank_runtime_identity", return_value=runtime), \
                 patch.object(cli, "load_retrieval_intents", return_value=[object()] * 30), \
                 patch.object(cli.SiliconFlowEmbeddingClient, "from_env", side_effect=AssertionError("network")), \
                 patch.object(cli, "QdrantQuestionStore", side_effect=AssertionError("write")):
                out = []
                args = cli._build_parser().parse_args(_args(tmp_path))
                view = cli._DependencyView({"bank_loader": lambda *_args, **_kwargs: records})
                self.assertEqual(cli._run_candidate_embedded_action(args, view=view, as_of=date.today(), output=out.append, output_format="human"), cli.EXIT_OK, out)
            self.assertIn("dry-run", out[0])

    def test_apply_loads_dotenv_before_default_embedding_only(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            tmp_path = Path(folder)
            records = [SimpleNamespace(status="needs_review") for _ in range(30)]
            runtime = SimpleNamespace(policy=SimpleNamespace(mode_policy_version="2026-H2"), catalog={}, manifest_hash="m")
            config = SimpleNamespace(provider="siliconflow", model="BAAI/bge-m3", dimension=1024)
            args = cli._build_parser().parse_args(_args(tmp_path, "--apply-real-embedding"))
            view = cli._DependencyView({"bank_loader": lambda *_args, **_kwargs: records})
            with patch.object(cli, "_call_loader", return_value=records), \
                 patch.object(cli, "load_question_bank_runtime_identity", return_value=runtime), \
                 patch.object(cli, "load_retrieval_intents", return_value=[object()] * 30), \
                 patch.object(cli, "resolve_embedding_config", return_value=config), \
                 patch("dotenv.load_dotenv") as load_dotenv, \
                 patch.object(cli.SiliconFlowEmbeddingClient, "from_env", side_effect=ValueError("missing key")):
                with self.assertRaises(ValueError):
                    cli._run_candidate_embedded_action(args, view=view, as_of=date.today(), output=lambda _: None, output_format="human")
            load_dotenv.assert_called_once_with()
