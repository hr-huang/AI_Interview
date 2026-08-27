from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import BaseModel, ConfigDict

from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    QuestionRetrievalResult,
    QuestionRetrievalTrace,
    RetrievedQuestion,
)
from profile_agent.state.checkpoint_serialization import InterviewCheckpointSerializer
from profile_agent.services.question_bank_service import (
    audit_question_bank,
    build_question_content_hash,
    classify_question_record,
    load_question_bank,
    normalize_question_text,
    normalize_project_mode,
    project_v1_record_to_v2,
    project_question_retrieval_result_to_v1,
    project_v2_record_to_v1,
)


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "question_rag" / "minimal_question_bank.json"
)
LEGACY_V1_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "question_rag" / "legacy_v1_question.json"
)
V2_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "question_rag" / "question_v2.json"
)
LEGACY_CHECKPOINT_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "question_rag" / "legacy_checkpoint.json"
)
PUBLIC_PROJECTION_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "question_rag" / "public_projection.json"
)

V1_FIELDS = {
    "question_id",
    "question_text",
    "role",
    "role_version",
    "dimension_id",
    "skills",
    "question_mode",
    "difficulty",
    "expected_signals",
    "critical_errors",
    "follow_up_seeds",
    "company_tags",
    "source_id",
    "source_url",
    "source_title",
    "source_type",
    "published_at",
    "verified_at",
    "valid_until",
    "trust_level",
    "status",
    "version",
    "content_hash",
}


class StrictV1Record(BaseModel):
    """Consumer fixture representing the pre-v2 record contract."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    question_text: str
    role: str
    role_version: str
    dimension_id: str
    skills: list[str]
    question_mode: str
    difficulty: str
    expected_signals: list[str]
    critical_errors: list[str]
    follow_up_seeds: list[str]
    company_tags: list[str]
    source_id: str
    source_url: str
    source_title: str
    source_type: str
    published_at: date
    verified_at: date
    valid_until: date
    trust_level: str
    status: str
    version: int
    content_hash: str


class StrictV1RetrievedQuestion(BaseModel):
    """Nested v1 fixture used by old retrieval/report consumers."""

    model_config = ConfigDict(extra="forbid")

    record: StrictV1Record
    score: float | None = None
    index_version: str | None = None


class StrictV1Trace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    question_id: str | None = None
    source_id: str | None = None
    score: float | None = None
    index_version: str | None = None


class StrictV1Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    as_of: date | None = None
    selected_question: StrictV1RetrievedQuestion | None = None
    trace: StrictV1Trace | None = None


class QuestionBankServiceTests(unittest.TestCase):
    def _load_fixture_json(self) -> dict:
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def _write_bank(self, payload: object) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "question_bank.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _v2_fixture(self) -> InterviewQuestionRecord:
        record = load_question_bank(FIXTURE_PATH, allow_test_only=True)[0]
        payload = record.model_dump(mode="python")
        payload.update(
            {
                "business_constraint": "数据新鲜度和权限边界",
                "dimension_terms": ["失败恢复", "任务编排"],
                "primary_mode": record.question_mode,
                "compatible_modes": ["foundation"],
                "source_ids": [record.source_id],
            }
        )
        return InterviewQuestionRecord.model_validate(payload)

    def test_normalize_question_text_collapses_whitespace(self) -> None:
        self.assertEqual(
            normalize_question_text("  Agent  \n  失败恢复  "),
            "Agent 失败恢复",
        )

    def test_v1_record_projects_to_explicit_v2_defaults(self) -> None:
        v1_record = load_question_bank(FIXTURE_PATH, allow_test_only=True)[0]

        projected = project_v1_record_to_v2(v1_record)

        self.assertEqual(projected.question_mode, v1_record.question_mode)
        self.assertEqual(projected.primary_mode, v1_record.question_mode)
        self.assertEqual(projected.compatible_modes, [])
        self.assertEqual(projected.business_constraint, "")
        self.assertEqual(projected.dimension_terms, [])
        self.assertEqual(projected.source_ids, [v1_record.source_id])
        self.assertTrue(
            {
                "business_constraint",
                "dimension_terms",
                "primary_mode",
                "compatible_modes",
                "source_ids",
            }.issubset(projected.model_fields_set)
        )

    def test_v1_projection_hash_is_v2_and_survives_v2_json_reload(self) -> None:
        v1_record = load_question_bank(FIXTURE_PATH, allow_test_only=True)[0]

        projected = project_v1_record_to_v2(v1_record)

        self.assertEqual(classify_question_record(projected), "v2")
        self.assertEqual(build_question_content_hash(projected), projected.content_hash)

        v2_bank = json.loads(V2_FIXTURE_PATH.read_text(encoding="utf-8"))
        v2_bank["questions"] = [json.loads(projected.model_dump_json())]
        path = self._write_bank(v2_bank)
        loaded = load_question_bank(path, allow_test_only=True)[0]

        self.assertEqual(classify_question_record(loaded), "v2")
        self.assertEqual(loaded.content_hash, projected.content_hash)
        self.assertEqual(build_question_content_hash(loaded), loaded.content_hash)

    def test_record_classification_survives_dump_json_and_checkpoint_roundtrip(self) -> None:
        v1_record = load_question_bank(LEGACY_V1_FIXTURE_PATH, allow_test_only=True)[0]
        self.assertEqual(classify_question_record(v1_record), "v1")

        dumped = v1_record.model_dump(mode="python")
        self.assertEqual(classify_question_record(dumped), "v1")
        self.assertEqual(build_question_content_hash(dumped), v1_record.content_hash)
        json_payload = json.loads(v1_record.model_dump_json())
        self.assertEqual(classify_question_record(json_payload), "v1")
        self.assertEqual(build_question_content_hash(json_payload), v1_record.content_hash)
        json_roundtrip = InterviewQuestionRecord.model_validate_json(
            v1_record.model_dump_json()
        )
        self.assertEqual(classify_question_record(json_roundtrip), "v1")
        self.assertEqual(
            build_question_content_hash(json_roundtrip),
            v1_record.content_hash,
        )

        serializer = InterviewCheckpointSerializer()
        serialized = serializer.dumps_typed(v1_record)
        checkpoint_roundtrip = serializer.loads_typed(serialized)
        self.assertIsInstance(checkpoint_roundtrip, InterviewQuestionRecord)
        self.assertEqual(classify_question_record(checkpoint_roundtrip), "v1")
        self.assertEqual(
            build_question_content_hash(checkpoint_roundtrip),
            v1_record.content_hash,
        )

        legacy_checkpoint = json.loads(
            LEGACY_CHECKPOINT_FIXTURE_PATH.read_text(encoding="utf-8")
        )
        self.assertEqual(
            legacy_checkpoint["interview_turns"][0]["question_mode"],
            v1_record.question_mode,
        )
        public_projection = json.loads(
            PUBLIC_PROJECTION_FIXTURE_PATH.read_text(encoding="utf-8")
        )
        self.assertNotIn("retrieval_trace", public_projection[0])

        v2_record = load_question_bank(V2_FIXTURE_PATH, allow_test_only=True)[0]
        self.assertEqual(classify_question_record(v2_record), "v2")
        self.assertEqual(
            classify_question_record(v2_record.model_dump(mode="python")),
            "v2",
        )

    def test_classification_keeps_explicit_primary_only_v2_shape(self) -> None:
        payload = project_v1_record_to_v2(
            load_question_bank(FIXTURE_PATH, allow_test_only=True)[0]
        ).model_dump(mode="python")
        for field_name in (
            "question_mode",
            "business_constraint",
            "dimension_terms",
            "compatible_modes",
            "source_ids",
        ):
            payload.pop(field_name, None)
        payload["primary_mode"] = "scenario"
        self.assertEqual(classify_question_record(payload), "v2")
        v2_record = InterviewQuestionRecord.model_validate(payload)

        self.assertEqual(classify_question_record(v2_record), "v2")

    def test_v2_fixture_hash_and_v1_fixture_hash_remain_readable(self) -> None:
        v1_record = load_question_bank(LEGACY_V1_FIXTURE_PATH, allow_test_only=True)[0]
        v2_record = load_question_bank(V2_FIXTURE_PATH, allow_test_only=True)[0]

        self.assertEqual(build_question_content_hash(v1_record), v1_record.content_hash)
        self.assertEqual(build_question_content_hash(v2_record), v2_record.content_hash)

        roundtripped_bank = self._load_fixture_json()
        roundtripped_bank["questions"] = [
            json.loads(v1_record.model_dump_json())
        ]
        path = self._write_bank(roundtripped_bank)
        loaded_roundtrip = load_question_bank(path, allow_test_only=True)[0]
        self.assertEqual(classify_question_record(loaded_roundtrip), "v1")
        self.assertEqual(loaded_roundtrip.content_hash, v1_record.content_hash)

    def test_v1_projection_rejects_mode_not_allowed_by_frozen_dimension_policy(self) -> None:
        v1_record = load_question_bank(FIXTURE_PATH, allow_test_only=True)[0].model_copy(
            update={
                "dimension_id": "role_dim_01",
                "question_mode": "coding",
            }
        )

        with self.assertRaises(ValueError):
            project_v1_record_to_v2(v1_record)

    def test_v2_projects_to_strict_v1_record_and_nested_json_boundary(self) -> None:
        v2_record = self._v2_fixture()

        projected = project_v2_record_to_v1(v2_record)

        self.assertEqual(set(projected), V1_FIELDS)
        self.assertNotIn("business_constraint", projected)
        self.assertNotIn("dimension_terms", projected)
        self.assertNotIn("primary_mode", projected)
        self.assertNotIn("compatible_modes", projected)
        self.assertNotIn("source_ids", projected)
        self.assertEqual(set(projected.model_dump()), V1_FIELDS)
        self.assertEqual(
            set(json.loads(projected.model_dump_json())),
            V1_FIELDS,
        )

        strict_record = StrictV1Record.model_validate(projected)
        self.assertEqual(set(strict_record.model_dump()), V1_FIELDS)
        self.assertEqual(
            set(json.loads(strict_record.model_dump_json())),
            V1_FIELDS,
        )

        nested = project_v2_record_to_v1(
            RetrievedQuestion(record=v2_record, score=0.87, index_version="idx-v2")
        )
        strict_nested = StrictV1RetrievedQuestion.model_validate(nested)
        self.assertEqual(strict_nested.record.question_mode, v2_record.primary_mode)
        self.assertEqual(set(strict_nested.record.model_dump()), V1_FIELDS)
        self.assertNotIn("business_constraint", nested["record"])

        result = QuestionRetrievalResult(
            status="hit",
            as_of=date(2026, 8, 26),
            selected_question=RetrievedQuestion(
                record=v2_record,
                score=0.87,
                index_version="idx-v2",
            ),
            trace=QuestionRetrievalTrace(
                status="hit",
                question_id=v2_record.question_id,
                source_id=v2_record.source_id,
                score=0.87,
                index_version="idx-v2",
            ),
        )
        strict_result = StrictV1Result.model_validate(
            project_question_retrieval_result_to_v1(result)
        )
        self.assertEqual(
            set(strict_result.selected_question.record.model_dump()), V1_FIELDS
        )

    def test_v1_and_v2_json_fixtures_reject_unknown_role_version_and_mode(self) -> None:
        v1_payload = self._load_fixture_json()["questions"][0]
        for field, value in (
            ("role", "untrusted_role"),
            ("role_version", "2025-H1"),
            ("question_mode", "unsupported_mode"),
        ):
            with self.subTest(field=field):
                invalid = dict(v1_payload)
                invalid[field] = value
                with self.assertRaises(ValueError):
                    project_v1_record_to_v2(invalid)

        self.assertEqual(normalize_project_mode("project"), "project_deep_dive")
        for invalid_mode in ("", "unknown", "PROJECT", None, 1):
            with self.subTest(invalid_mode=invalid_mode):
                with self.assertRaises((TypeError, ValueError)):
                    normalize_project_mode(invalid_mode)

    def test_v2_content_hash_includes_all_additive_semantic_fields(self) -> None:
        baseline = self._v2_fixture()
        baseline_hash = build_question_content_hash(baseline)

        updates = {
            "business_constraint": "延迟预算和权限边界",
            "dimension_terms": ["新的维度语义"],
            "primary_mode": "system_design",
            "compatible_modes": ["project_deep_dive"],
        }
        for field, value in updates.items():
            with self.subTest(field=field):
                changed = baseline.model_copy(update={field: value})
                self.assertNotEqual(build_question_content_hash(changed), baseline_hash)

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
