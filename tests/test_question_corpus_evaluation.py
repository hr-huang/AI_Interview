from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_question_bank
from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    LabeledQuestionIntent,
    QuestionRetrievalIntent,
)
from profile_agent.services.question_bank_service import load_question_bank
from profile_agent.services.question_corpus_evaluation import (
    EvaluationValidationError,
    HARD_NEGATIVE_CATEGORIES,
    SYNTHETIC_HARD_NEGATIVE_CATALOG,
    evaluate_question_corpus,
    intent_to_runtime_intent,
    load_retrieval_intents,
)


ROOT = Path(__file__).parents[1]
CORPUS_DIR = ROOT / "profile_agent" / "knowledge" / "question_banks" / "ai_agent_engineer_2026_h2"
INTENTS_PATH = CORPUS_DIR / "retrieval_intents.jsonl"
QUESTIONS_PATH = CORPUS_DIR / "questions.json"
RELEASE_REGISTRY = CORPUS_DIR / ".." / ".." / "approval_registries" / "ai_agent_engineer_2026_h2_release_20260828.json"


class QuestionCorpusEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.records = load_question_bank(QUESTIONS_PATH)
        cls.intents = load_retrieval_intents(INTENTS_PATH, records=cls.records)

    def test_canonical_intents_cover_each_question_once(self) -> None:
        self.assertEqual(len(self.intents), 30)
        self.assertEqual(
            {intent.gold_question_id for intent in self.intents},
            {f"q{index:03d}" for index in range(1, 31)},
        )
        self.assertEqual(
            {intent.intent_id for intent in self.intents},
            {f"intent_q{index:03d}" for index in range(1, 31)},
        )
        for intent in self.intents:
            self.assertEqual(intent.role, "ai_agent_engineer")
            self.assertEqual(intent.role_version, "2026-H2")
            self.assertTrue(intent.query_text)
            self.assertTrue(intent.label_notes)
            self.assertIn(intent.gold_question_id, intent.acceptable_question_ids)

    def test_hard_negative_registry_is_per_intent_and_attribute_validated(self) -> None:
        all_ids = [negative_id for intent in self.intents for negative_id in intent.hard_negative_ids]
        self.assertEqual(len(all_ids), 30 * len(HARD_NEGATIVE_CATEGORIES))
        self.assertEqual(len(set(all_ids)), len(all_ids))
        as_of = date(2026, 8, 27)
        for intent in self.intents:
            categories = set()
            for negative_id in intent.hard_negative_ids:
                fixture = SYNTHETIC_HARD_NEGATIVE_CATALOG[negative_id]
                category = fixture["category"]
                categories.add(category)
                if category == "wrong_dimension":
                    self.assertNotEqual(fixture["dimension_id"], intent.dimension_id)
                elif category == "wrong_mode":
                    self.assertNotEqual(fixture["question_mode"], intent.requested_mode)
                    self.assertNotIn(fixture["question_mode"], self.records[0].compatible_modes)
                elif category == "expired":
                    self.assertLess(fixture["valid_until"], as_of.isoformat())
                elif category == "retired":
                    self.assertEqual(fixture["status"], "retired")
                elif category == "duplicate":
                    self.assertEqual(fixture["duplicate_of"], intent.gold_question_id)
                elif category == "wrong_role":
                    self.assertNotEqual(fixture["role"], intent.role)
                elif category == "low_trust":
                    self.assertEqual(fixture["trust_level"], "low")
            self.assertEqual(categories, set(HARD_NEGATIVE_CATEGORIES))

    def test_hard_negative_ids_are_strict_and_notes_cannot_fake_coverage(self) -> None:
        payload = [intent.model_dump(mode="json") for intent in self.intents]
        payload[0]["hard_negative_ids"] = []
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "intents.jsonl"
            path.write_text("\n".join(json.dumps(item) for item in payload) + "\n", encoding="utf-8")
            with self.assertRaises(EvaluationValidationError):
                load_retrieval_intents(path, records=self.records)
        payload = [intent.model_dump(mode="json") for intent in self.intents]
        payload[0]["hard_negative_ids"][0] = "madeup_wrong_dimension_fixture"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "intents.jsonl"
            path.write_text("\n".join(json.dumps(item) for item in payload) + "\n", encoding="utf-8")
            with self.assertRaises(EvaluationValidationError):
                load_retrieval_intents(path, records=self.records)

    def test_fake_provider_is_label_blind_and_gold_mutation_fails(self) -> None:
        provider = run_question_bank._fake_corpus_result_provider(self.records)
        original = self.intents[0]
        runtime = intent_to_runtime_intent(original, records=self.records)
        raw = provider(runtime)
        self.assertTrue(set(original.hard_negative_ids).issubset(set(raw["candidate_pool"])))
        self.assertTrue(set(original.hard_negative_ids).isdisjoint({hit["question_id"] for hit in raw["hits"]}))
        mutated = original.model_copy(
            update={
                "gold_question_id": "q006",
                "acceptable_question_ids": ["q006"],
                "hard_negative_ids": [
                    "hn_wrong_dimension_q006",
                    "hn_wrong_mode_q006",
                    "hn_expired_q006",
                    "hn_retired_q006",
                    "hn_duplicate_q006",
                    "hn_wrong_role_q006",
                    "hn_low_trust_q006",
                ],
            }
        )
        self.assertEqual(provider(runtime), provider(intent_to_runtime_intent(mutated, records=self.records)))
        source = run_question_bank._fake_corpus_result_provider.__code__.co_names
        self.assertNotIn("gold_question_id", source)
        self.assertNotIn("acceptable_question_ids", source)
        report = evaluate_question_corpus([mutated], None, result_provider=provider)
        self.assertFalse(report.passed)

    def test_cli_local_without_loopback_fails_closed(self) -> None:
        stdout = io.StringIO()
        with patch.object(run_question_bank, "_write_corpus_artifact"):
            with redirect_stdout(stdout):
                code = run_question_bank.main(
                    [
                        "evaluate-local",
                        "--corpus-dir",
                        str(CORPUS_DIR),
                        "--approval-registry",
                        str(RELEASE_REGISTRY),
                        "--store",
                        "local",
                        "--dry-run",
                        "--format",
                        "json",
                    ]
                )
        self.assertNotEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["passed"])
        self.assertIn("Task 9", payload["issues"][-1]["message"])

    def test_canonical_labels_exercise_compatible_tier(self) -> None:
        compatible = [intent for intent in self.intents if len(intent.acceptable_question_ids) > 1]
        self.assertTrue(compatible)
        intent = compatible[0]
        result = evaluate_question_corpus(
            [intent],
            self.records,
            results={
                intent.intent_id: {
                    "status": "hit",
                    "hits": [
                        {
                            "question_id": intent.gold_question_id,
                            "source_id": next(r.source_id for r in self.records if r.question_id == intent.gold_question_id),
                            "score": 1.0,
                            "index_version": "fixture",
                            "match_tier": "exact",
                        },
                        {
                            "question_id": intent.acceptable_question_ids[1],
                            "source_id": next(r.source_id for r in self.records if r.question_id == intent.acceptable_question_ids[1]),
                            "score": 0.9,
                            "index_version": "fixture",
                            "match_tier": "compatible",
                        },
                    ],
                    "trace": {
                        "status": "hit",
                        "question_id": intent.gold_question_id,
                        "source_id": next(r.source_id for r in self.records if r.question_id == intent.gold_question_id),
                        "score": 1.0,
                        "index_version": "fixture",
                        "match_tier": "exact",
                    },
                }
            },
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.intent_results[0].top3[1]["match_tier"], "compatible")

    def test_intent_to_runtime_intent_only_projects_runtime_fields(self) -> None:
        runtime = intent_to_runtime_intent(self.intents[0], records=self.records)
        self.assertIsInstance(runtime, QuestionRetrievalIntent)
        self.assertEqual(
            set(runtime.model_dump()),
            {
                "query_text",
                "role",
                "dimension_id",
                "question_mode",
                "difficulty",
                "excluded_question_ids",
            },
        )
        self.assertEqual(runtime.role, "ai_agent_engineer")
        self.assertNotIn("gold_question_id", runtime.query_text)
        self.assertNotIn("hard_negative", runtime.query_text)
        self.assertNotIn("source_id", runtime.query_text)

    def test_label_validation_rejects_cross_dimension_acceptable(self) -> None:
        payload = [intent.model_dump(mode="json") for intent in self.intents]
        payload[0]["acceptable_question_ids"] = ["q001", "q007"]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "intents.jsonl"
            path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in payload) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(EvaluationValidationError):
                load_retrieval_intents(path, records=self.records)

    def test_jsonl_requires_explicit_role_and_version_fields(self) -> None:
        payload = self.intents[0].model_dump(mode="json")
        payload.pop("role")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "intents.jsonl"
            path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaises(EvaluationValidationError):
                load_retrieval_intents(path, records=self.records)

    def test_trace_without_explicit_match_tier_fails(self) -> None:
        by_id = {record.question_id: record for record in self.records}

        def provider(intent: LabeledQuestionIntent, runtime: QuestionRetrievalIntent):
            del runtime
            gold = by_id[intent.gold_question_id]
            return {
                "status": "hit",
                "hits": [{"question": gold, "score": 1.0, "index_version": "fake-v1"}],
                "trace": {
                    "status": "hit",
                    "question_id": gold.question_id,
                    "source_id": gold.source_id,
                    "score": 1.0,
                    "index_version": "fake-v1",
                },
            }

        report = evaluate_question_corpus(
            self.intents,
            self.records,
            result_provider=provider,
            as_of=date(2026, 8, 27),
        )
        self.assertFalse(report.passed)
        self.assertEqual(report.trace_coverage, 0.0)

    def test_string_candidates_and_rank_trace_are_supported(self) -> None:
        labels = self.intents[:1]
        gold = self.records[0]
        result = {
            "status": "hit",
            "top3": ["q001"],
            "trace": {
                "status": "hit",
                "question_id": "q001",
                "source_id": gold.source_id,
                "score": 0.75,
                "index_version": "fake-v1",
                "match_tier": "exact",
            },
        }
        report = evaluate_question_corpus(
            labels,
            self.records,
            results={"intent_q001": result},
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.intent_results[0].top3[0]["question_id"], "q001")

    def test_duplicate_gold_keeps_first_rank_while_failing_duplicate_gate(self) -> None:
        gold = self.records[0]

        def provider(intent: LabeledQuestionIntent, runtime: QuestionRetrievalIntent):
            del runtime
            return {
                "status": "hit",
                "hits": [
                    {"question": gold, "score": 1.0, "index_version": "fake-v1"},
                    {"question": gold, "score": 0.9, "index_version": "fake-v1"},
                ],
                "trace": {
                    "status": "hit",
                    "question_id": "q001",
                    "source_id": gold.source_id,
                    "score": 1.0,
                    "index_version": "fake-v1",
                    "match_tier": "exact",
                },
            }

        report = evaluate_question_corpus(
            self.intents[:1],
            self.records,
            result_provider=provider,
        )
        self.assertEqual(report.intent_results[0].gold_rank, 1)
        self.assertGreater(report.duplicate_count, 0)
        self.assertFalse(report.passed)

    def test_evaluation_computes_metrics_and_serializes_trace(self) -> None:
        by_id = {record.question_id: record for record in self.records}

        def provider(intent: LabeledQuestionIntent, runtime: QuestionRetrievalIntent):
            del runtime
            gold = by_id[intent.gold_question_id]
            acceptable = [
                by_id[question_id]
                for question_id in intent.acceptable_question_ids
                if question_id != intent.gold_question_id
            ]
            candidates = [gold, *acceptable]
            return {
                "status": "hit",
                "hits": [
                    {
                        "question": record,
                        "question_id": record.question_id,
                        "source_id": record.source_id,
                        "score": 1.0 - (index * 0.01),
                        "index_version": "fake-v1",
                        "match_tier": "exact" if index == 0 else "compatible",
                    }
                    for index, record in enumerate(candidates[:3])
                ],
                "trace": {
                    "status": "hit",
                    "question_id": gold.question_id,
                    "source_id": gold.source_id,
                    "score": 1.0,
                    "index_version": "fake-v1",
                    "match_tier": "exact",
                },
            }

        report = evaluate_question_corpus(
            self.intents,
            self.records,
            result_provider=provider,
            as_of=date(2026, 8, 27),
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.recall_at_3, 1.0)
        self.assertEqual(report.mrr_at_3, 1.0)
        self.assertEqual(report.hard_negative_hits, 0)
        self.assertEqual(report.invalid_count, 0)
        self.assertEqual(report.duplicate_count, 0)
        self.assertEqual(report.trace_coverage, 1.0)
        self.assertEqual(len(report.intent_results), 30)
        self.assertEqual(report.intent_results[0].top3[0]["question_id"], "q001")
        self.assertEqual(report.intent_results[0].gold_rank, 1)
        self.assertEqual(report.intent_results[0].match_tier, "exact")
        serialized = report.to_dict()
        self.assertEqual(serialized["metrics"]["recall_at_3"], 1.0)
        self.assertEqual(len(serialized["intent_results"]), 30)

    def test_missing_gold_duplicate_trace_and_wrong_tier_fail_closed(self) -> None:
        by_id = {record.question_id: record for record in self.records}

        def provider(intent: LabeledQuestionIntent, runtime: QuestionRetrievalIntent):
            del runtime
            gold = by_id[intent.gold_question_id]
            return {
                "status": "hit",
                "hits": [
                    {
                        "question": gold,
                        "question_id": gold.question_id,
                        "source_id": gold.source_id,
                        "score": 1.0,
                        "index_version": "fake-v1",
                        "match_tier": "compatible",
                    },
                    {
                        "question": gold,
                        "question_id": gold.question_id,
                        "source_id": gold.source_id,
                        "score": 0.9,
                        "index_version": "fake-v1",
                        "match_tier": "compatible",
                    },
                ],
                "trace": None,
            }

        report = evaluate_question_corpus(
            self.intents,
            self.records,
            result_provider=provider,
            as_of=date(2026, 8, 27),
        )
        self.assertFalse(report.passed)
        self.assertGreater(report.duplicate_count, 0)
        self.assertEqual(report.trace_coverage, 0.0)
        self.assertGreater(report.invalid_count, 0)
        self.assertTrue(report.intent_results[0].errors)

    def test_cli_fake_evaluation_is_read_only_and_does_not_read_provider_env(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory(), patch.dict(
            "os.environ", {"SILICONFLOW_API_KEY": "must-not-be-read"}, clear=False
        ):
            with patch.object(run_question_bank, "_write_corpus_artifact"):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = run_question_bank.main(
                        [
                            "evaluate-local",
                            "--corpus-dir",
                            str(CORPUS_DIR),
                            "--approval-registry",
                            str(RELEASE_REGISTRY),
                            "--store",
                            "fake",
                            "--dry-run",
                            "--format",
                            "json",
                        ],
                        env={"SILICONFLOW_API_KEY": "must-not-be-read"},
                    )
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["action"], "evaluate-local")
        self.assertEqual(payload["store"], "fake")
        self.assertTrue(payload["passed"])


if __name__ == "__main__":
    unittest.main()
