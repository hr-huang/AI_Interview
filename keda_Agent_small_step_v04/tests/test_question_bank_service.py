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

    def test_rejects_synthetic_records_when_root_is_not_test_only(self) -> None:
        for root_test_only in (False, None):
            with self.subTest(root_test_only=root_test_only):
                payload = self._load_fixture_json()
                if root_test_only is None:
                    payload.pop("test_only")
                else:
                    payload["test_only"] = root_test_only

                with self.assertRaisesRegex(ValueError, "test_only_synthetic"):
                    load_question_bank(
                        self._write_bank(payload),
                        allow_test_only=True,
                    )

                with self.assertRaises(ValueError):
                    load_question_bank(self._write_bank(payload))

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

    def test_rejects_boolean_and_float_schema_versions(self) -> None:
        for schema_version in (True, 1.0, "unknown"):
            with self.subTest(schema_version=schema_version):
                payload = self._load_fixture_json()
                payload["schema_version"] = schema_version

                with self.assertRaisesRegex(ValueError, "schema_version"):
                    load_question_bank(
                        self._write_bank(payload),
                        allow_test_only=True,
                    )

    def test_rejects_missing_or_mismatched_root_metadata(self) -> None:
        for field in ("role", "role_version", "questions"):
            with self.subTest(field=field):
                payload = self._load_fixture_json()
                payload.pop(field)

                with self.assertRaises(ValueError):
                    load_question_bank(
                        self._write_bank(payload),
                        allow_test_only=True,
                    )

        payload = self._load_fixture_json()
        payload["role"] = "other_role"
        with self.assertRaisesRegex(ValueError, "role"):
            load_question_bank(
                self._write_bank(payload),
                allow_test_only=True,
            )

        payload = self._load_fixture_json()
        payload["role_version"] = "2026-H1"
        with self.assertRaisesRegex(ValueError, "role_version"):
            load_question_bank(
                self._write_bank(payload),
                allow_test_only=True,
            )

    def test_rejects_invalid_source_when_loading(self) -> None:
        payload = self._load_fixture_json()
        payload["questions"][0]["source_url"] = "not-a-url"

        with self.assertRaisesRegex(ValueError, "source"):
            load_question_bank(
                self._write_bank(payload),
                allow_test_only=True,
            )

    def test_audit_separates_lifecycle_states_and_source_findings(self) -> None:
        records = load_question_bank(FIXTURE_PATH, allow_test_only=True)
        as_of = date(2026, 8, 26)
        needs_review = records[0].model_copy(
            update={
                "status": "needs_review",
                "valid_until": as_of + timedelta(days=7),
            }
        )
        retired = records[1].model_copy(
            update={
                "status": "retired",
                "valid_until": as_of + timedelta(days=8),
            }
        )
        missing_source = records[2].model_copy(
            update={
                "source_id": "",
                "source_url": "",
                "valid_until": as_of + timedelta(days=9),
            }
        )
        invalid_source_url = records[3].model_copy(
            update={
                "source_url": "not-a-url",
                "valid_until": as_of + timedelta(days=10),
            }
        )
        invalid_source_type = records[4].model_copy(
            update={
                "source_type": "not_allowed",
                "valid_until": as_of + timedelta(days=11),
            }
        )

        report = audit_question_bank(
            [
                needs_review,
                retired,
                missing_source,
                invalid_source_url,
                invalid_source_type,
                records[5],
            ],
            as_of=as_of,
            expiring_within_days=30,
        )

        self.assertEqual(report.needs_review_question_ids, [needs_review.question_id])
        self.assertEqual(report.retired_question_ids, [retired.question_id])
        self.assertEqual(
            report.inactive_question_ids,
            sorted([needs_review.question_id, retired.question_id]),
        )
        self.assertEqual(
            report.expiring_soon_question_ids,
            sorted(
                [
                    needs_review.question_id,
                    retired.question_id,
                    missing_source.question_id,
                    invalid_source_url.question_id,
                    invalid_source_type.question_id,
                ]
            ),
        )
        self.assertEqual(
            report.missing_source_question_ids,
            [missing_source.question_id],
        )
        self.assertEqual(
            report.invalid_source_question_ids,
            sorted([invalid_source_url.question_id, invalid_source_type.question_id]),
        )
        self.assertEqual(
            report.eligible_question_ids,
            [records[5].question_id],
        )

    def test_audit_classifies_malformed_url_as_invalid_source(self) -> None:
        record = load_question_bank(FIXTURE_PATH, allow_test_only=True)[0]
        malformed_url = record.model_copy(update={"source_url": "http://["})

        report = audit_question_bank([malformed_url], as_of=date(2026, 8, 26))

        self.assertEqual(report.invalid_source_question_ids, [record.question_id])
        self.assertEqual(report.missing_source_question_ids, [])
        self.assertEqual(report.eligible_question_ids, [])

    def test_audit_rejects_structurally_invalid_raw_mappings_from_eligibility(self) -> None:
        payload = self._load_fixture_json()
        base = payload["questions"][0]
        variants = []

        missing_semantic_field = deepcopy(base)
        missing_semantic_field["question_id"] = "q_invalid_missing_field"
        missing_semantic_field.pop("expected_signals")
        variants.append(missing_semantic_field)

        wrong_role = deepcopy(base)
        wrong_role["question_id"] = "q_invalid_role"
        wrong_role["role"] = "java_engineer"
        variants.append(wrong_role)

        wrong_dimension = deepcopy(base)
        wrong_dimension["question_id"] = "q_invalid_dimension"
        wrong_dimension["dimension_id"] = "role_dim_99"
        variants.append(wrong_dimension)

        wrong_hash = deepcopy(base)
        wrong_hash["question_id"] = "q_invalid_hash"
        wrong_hash["content_hash"] = "sha256:not-the-canonical-hash"
        variants.append(wrong_hash)

        wrong_date = deepcopy(base)
        wrong_date["question_id"] = "q_invalid_date"
        wrong_date["valid_until"] = "not-a-date"
        variants.append(wrong_date)

        report = audit_question_bank(variants, as_of=date(2026, 8, 26))

        expected_ids = sorted(question["question_id"] for question in variants)
        self.assertEqual(report.eligible_question_ids, [])
        self.assertEqual(report.invalid_record_question_ids, expected_ids)
        self.assertTrue(
            all(report.invalid_record_reasons[question_id] for question_id in expected_ids)
        )

    def test_audit_revalidates_mutated_question_model_instances(self) -> None:
        record = load_question_bank(FIXTURE_PATH, allow_test_only=True)[0]
        variants = [
            record.model_copy(
                update={"question_id": "q_invalid_trust", "trust_level": "bogus"}
            ),
            record.model_copy(
                update={"question_id": "q_invalid_version", "version": 0}
            ),
            record.model_copy(
                update={
                    "question_id": "q_invalid_mode",
                    "question_mode": "unsupported_mode",
                }
            ),
            record.model_copy(
                update={
                    "question_id": "q_invalid_difficulty",
                    "difficulty": "unsupported_difficulty",
                }
            ),
            record.model_copy(
                update={"question_id": "q_invalid_signals", "expected_signals": None}
            ),
            record.model_copy(
                update={"question_id": "q_invalid_skill_item", "skills": ["   "]}
            ),
        ]
        # Recompute the hash for enum changes so the test proves schema
        # validation is independent from the semantic hash guard.
        for variant in variants[2:4]:
            variant.content_hash = build_question_content_hash(variant)

        report = audit_question_bank(variants, as_of=date(2026, 8, 26))

        expected_ids = sorted(variant.question_id for variant in variants)
        self.assertEqual(report.eligible_question_ids, [])
        self.assertEqual(report.invalid_record_question_ids, expected_ids)
        self.assertTrue(
            all(report.invalid_record_reasons[question_id] for question_id in expected_ids)
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
