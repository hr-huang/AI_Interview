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

    def test_supervisor_probes_cover_exact_modes_and_compatible_fallback(self):
        class Store:
            def __init__(self):
                self.compatible_intent = None

            def search(self, *, intent, query_vector, today, limit):
                if query_vector[0] == "q004":
                    self.compatible_intent = intent
                    excluded = set(getattr(intent, "excluded_question_ids", ()))
                    if intent.question_mode == "scenario" and excluded == {"q003", "q006"}:
                        return SimpleNamespace(hits=[], status="no_match", index_version="candidate")
                    question_id = "q004" if intent.question_mode == "system_design" and excluded == {"q003", "q006"} else "q003"
                else:
                    question_id = query_vector[0]
                mode = "system_design" if question_id == "q004" else intent.question_mode
                record = SimpleNamespace(primary_mode=mode, question_mode=mode)
                hit = SimpleNamespace(record=record, question_id=question_id,
                                      score=0.7, source_id="fixture", index_version="candidate")
                return SimpleNamespace(hits=[hit], status="hit", index_version="candidate")

        labels = [SimpleNamespace(intent_id=f"intent_{qid}") for qid in ("q001", "q010", "q028", "q003")]
        runtimes = [SimpleNamespace(question_mode=mode, dimension_id="role_dim_01") for mode in ("foundation", "scenario", "coding", "scenario")]
        vectors = {label.intent_id: [qid] for label, qid in zip(labels, ("q001", "q010", "q028", "q004"))}
        store = Store()

        def runtime_with_exclusions(intent, *, records, excluded_question_ids=()):
            return SimpleNamespace(question_mode="scenario", dimension_id="role_dim_01",
                                   excluded_question_ids=tuple(excluded_question_ids))

        policy = SimpleNamespace(compatible_order_for=lambda dimension_id: ("scenario", "system_design"))

        with patch.object(cli, "intent_to_runtime_intent", side_effect=runtime_with_exclusions) as to_runtime:
            probes = cli._run_supervisor_probes(store, labels, runtimes, vectors,
                                                records=[SimpleNamespace(question_id="q001")], policy=policy,
                                                as_of=date.today())

        self.assertEqual([probe["intent_id"] for probe in probes],
                         ["intent_q001", "intent_q010", "intent_q028", "intent_q003"])
        self.assertEqual([probe["status"] for probe in probes], ["pass"] * 4)
        self.assertEqual(probes[0]["top3"][0]["question_id"], "q001")
        self.assertEqual(probes[1]["top3"][0]["question_id"], "q010")
        self.assertEqual(probes[2]["top3"][0]["question_id"], "q028")
        self.assertEqual(probes[3]["top3"][0]["question_id"], "q004")
        self.assertEqual(probes[3]["top3"][0]["tier"], "compatible")
        self.assertEqual(store.compatible_intent.excluded_question_ids, ("q003", "q006"))
        to_runtime.assert_called_once()
        self.assertTrue(all("expected" in probe and "assertion" in probe for probe in probes))

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
