import json
import hashlib
from datetime import date
from pathlib import Path
import unittest

from profile_agent.services.question_corpus_governance import (
    ApprovalReceipt, load_approval_registry, load_question_corpus_snapshot,
    validate_question_corpus,
)
from run_question_bank import _load_corpus_role_pack


class PublishedQuestionCorpusTests(unittest.TestCase):
    root = Path(__file__).parents[1] / "profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2"
    registry = Path(__file__).parents[1] / "profile_agent/knowledge/approval_registries/ai_agent_engineer_2026_h2_release_20260828.json"

    def test_external_release_registry_is_required_and_publishes_all_30(self):
        snapshot = load_question_corpus_snapshot(self.root, date(2026, 8, 28))
        self.assertEqual(sum(record.status == "active" for record in snapshot.records), 30)
        self.assertIn("review_decision", {issue.code for issue in validate_question_corpus(snapshot, _load_corpus_role_pack(), date(2026, 8, 28))})
        registry = load_approval_registry(self.registry,
            expected_question_set_hash=snapshot.manifest.question_set_hash,
            expected_sidecar_set_hash=snapshot.manifest.sidecar_set_hash,
            expected_question_ids=[record.question_id for record in snapshot.records])
        issues = validate_question_corpus(snapshot, _load_corpus_role_pack(), date(2026, 8, 28), approval_verifier=registry)
        self.assertEqual(issues, [])

    def test_registry_rejects_tampered_receipt(self):
        payload = json.loads(self.registry.read_text(encoding="utf-8"))
        payload["receipts"][0]["nonce"] = "tampered"
        with self.assertRaises(ValueError):
            load_approval_registry_from_payload(payload)

    def test_registry_rejects_reused_nonce_across_questions(self):
        payload = json.loads(self.registry.read_text(encoding="utf-8"))
        payload["receipts"][1]["nonce"] = payload["receipts"][0]["nonce"]
        unsigned = {key: value for key, value in payload.items() if key != "registry_hash"}
        payload["registry_hash"] = "sha256:" + hashlib.sha256(json.dumps(
            unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        with self.assertRaises(ValueError):
            load_approval_registry_from_payload(payload)


def load_approval_registry_from_payload(payload):
    from profile_agent.services.question_corpus_governance import ExternalApprovalRegistry
    return ExternalApprovalRegistry(payload)
