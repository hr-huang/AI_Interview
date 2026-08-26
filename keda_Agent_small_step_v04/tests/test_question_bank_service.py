from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from profile_agent.services.question_bank_service import (
    audit_question_bank,
    build_question_content_hash,
    load_question_bank,
    normalize_question_text,
)


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "question_rag" / "minimal_question_bank.json"
)


class QuestionBankServiceTests(unittest.TestCase):
    def _load_fixture_json(self) -> dict:
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def _write_bank(self, payload: object) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "question_bank.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_normalize_question_text_collapses_whitespace(self) -> None:
        self.assertEqual(
            normalize_question_text("  Agent  \n  失败恢复  "),
            "Agent 失败恢复",
        )

    def test_content_hash_is_stable_for_whitespace_and_skill_order(self) -> None:
        records = load_question_bank(FIXTURE_PATH, allow_test_only=True)
        record = records[0]
        whitespace_variant = record.model_copy(
            update={
                "question_text": f"  {record.question_text.replace(' ', '  ')}\n",
                "skills": list(reversed([f"  {skill}  " for skill in record.skills])),
            }
        )

        self.assertEqual(
            build_question_content_hash(record),
            build_question_content_hash(whitespace_variant),
        )

    def test_content_hash_changes_when_semantic_skill_changes(self) -> None:
        records = load_question_bank(FIXTURE_PATH, allow_test_only=True)
        record = records[0]
        changed_skill = record.model_copy(
            update={"skills": [*record.skills, "新语义标签"]}
        )

        self.assertNotEqual(
            build_question_content_hash(record),
            build_question_content_hash(changed_skill),
        )

    def test_loads_six_test_only_records_covering_all_dimensions(self) -> None:
        records = load_question_bank(FIXTURE_PATH, allow_test_only=True)

        self.assertEqual(len(records), 6)
        self.assertEqual(
            {record.dimension_id for record in records},
            {f"role_dim_{index:02d}" for index in range(1, 7)},
        )
        self.assertTrue(all(record.source_url.startswith("https://example.com/") for record in records))

    def test_rejects_test_only_bank_without_explicit_dependency(self) -> None:
        with self.assertRaises(ValueError):
            load_question_bank(FIXTURE_PATH)

    def test_rejects_duplicate_question_ids(self) -> None:
        payload = self._load_fixture_json()
        payload["questions"][1]["question_id"] = payload["questions"][0]["question_id"]

        with self.assertRaisesRegex(ValueError, "duplicate question_id"):
            load_question_bank(
                self._write_bank(payload),
                allow_test_only=True,
            )

    def test_rejects_duplicate_content_hashes(self) -> None:
        payload = self._load_fixture_json()
        # The question id is not part of the content hash, so this remains a
        # valid record while deliberately colliding with the first record.
        payload["questions"][1] = deepcopy(payload["questions"][0])
        payload["questions"][1]["question_id"] = "q_agent_duplicate_hash"

        with self.assertRaisesRegex(ValueError, "duplicate content_hash"):
            load_question_bank(
                self._write_bank(payload),
                allow_test_only=True,
            )

    def test_rejects_stored_hash_mismatch(self) -> None:
        payload = self._load_fixture_json()
        payload["questions"][0]["content_hash"] = "sha256:stored-value-does-not-match"

        with self.assertRaisesRegex(ValueError, "content_hash mismatch"):
            load_question_bank(
                self._write_bank(payload),
                allow_test_only=True,
            )

    def test_rejects_non_object_json_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "root"):
            load_question_bank(
                self._write_bank([]),
                allow_test_only=True,
            )

    def test_rejects_unsupported_dimension_id(self) -> None:
        payload = self._load_fixture_json()
        payload["questions"][0]["dimension_id"] = "role_dim_99"

        with self.assertRaisesRegex(ValueError, "dimension"):
            load_question_bank(
                self._write_bank(payload),
                allow_test_only=True,
            )

    def test_audit_reports_expired_and_expiring_records(self) -> None:
        records = load_question_bank(FIXTURE_PATH, allow_test_only=True)
        as_of = date(2026, 8, 26)
        expired = records[0].model_copy(
            update={"valid_until": as_of - timedelta(days=1)}
        )
        expiring = records[1].model_copy(
            update={"valid_until": as_of + timedelta(days=7)}
        )

        report = audit_question_bank(
            [expired, expiring, *records[2:]],
            as_of=as_of,
            expiring_within_days=30,
        )

        self.assertEqual(report.expired_question_ids, [expired.question_id])
        self.assertEqual(report.expiring_question_ids, [expiring.question_id])

    def test_audit_does_not_mutate_records(self) -> None:
        records = load_question_bank(FIXTURE_PATH, allow_test_only=True)
        before = [record.model_dump(mode="json") for record in records]

        audit_question_bank(
            records,
            as_of=date(2026, 8, 26),
            expiring_within_days=30,
        )

        self.assertEqual(
            [record.model_dump(mode="json") for record in records],
            before,
        )


if __name__ == "__main__":
    unittest.main()
