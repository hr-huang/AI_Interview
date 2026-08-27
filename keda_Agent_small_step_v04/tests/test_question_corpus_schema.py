from copy import deepcopy
from datetime import date
import unittest

from pydantic import ValidationError

from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    LabeledQuestionIntent,
    QuestionBankManifest,
    QuestionCorpusQuotas,
    QuestionCorpusSnapshot,
    QuestionDedupeRecord,
    QuestionDedupeSidecar,
    QuestionLocatorRecord,
    QuestionLocatorSidecar,
    QuestionModePolicy,
    QuestionReviewRecord,
    QuestionReviewSidecar,
    QuestionRightsRecord,
    QuestionRightsSidecar,
    QuestionSourceRegistry,
    QuestionSourceRegistryEntry,
)


CORPUS_AS_OF = date(2026, 8, 27)
QUESTION_IDS = [f"q_agent_{index:03d}" for index in range(1, 31)]


def valid_record_kwargs(question_id: str = QUESTION_IDS[0]) -> dict:
    return {
        "question_id": question_id,
        "question_text": "工具调用失败后如何保证状态一致并避免重复执行？",
        "business_constraint": "失败恢复必须保持幂等，且不能突破延迟预算。",
        "dimension_terms": ["失败恢复", "幂等", "状态一致性"],
        "role": "ai_agent_engineer",
        "role_version": "2026-H2",
        "dimension_id": "role_dim_01",
        "skills": ["幂等", "失败恢复"],
        "question_mode": "scenario",
        "primary_mode": "scenario",
        "compatible_modes": ["system_design", "follow_up"],
        "difficulty": "intermediate",
        "expected_signals": ["幂等键", "执行状态查询"],
        "critical_errors": ["所有失败都直接重试"],
        "follow_up_seeds": ["首次已成功但响应丢失时怎么办？"],
        "company_tags": [],
        "source_id": "src_public_001",
        "source_ids": ["src_public_001", "src_official_001"],
        "source_url": "https://example.com/interview",
        "source_title": "Public source",
        "source_type": "public_interview_experience",
        "published_at": date(2026, 7, 1),
        "verified_at": date(2026, 8, 26),
        "valid_until": date(2027, 2, 26),
        "trust_level": "medium",
        "status": "active",
        "version": 2,
        "content_hash": "sha256:record-001",
    }


def valid_source_kwargs(source_id: str = "src_public_001") -> dict:
    return {
        "source_id": source_id,
        "source_type": "public_interview_experience",
        "canonical_url": "https://example.com/interview",
        "title": "Public interview signal",
        "publisher": "Example publisher",
        "published_at": date(2026, 7, 1),
        "verified_at": CORPUS_AS_OF,
        "accessed_at": CORPUS_AS_OF,
        "trust": "medium",
        "lifecycle": "draft",
        "question_ids": [QUESTION_IDS[0]],
        "human_summary": "人工摘要只描述能力信号，不复制原文。",
        "review_class": "dynamic",
    }


def valid_manifest_kwargs() -> dict:
    return {
        "schema_version": "2",
        "bank_id": "ai_agent_engineer_2026_h2",
        "role": "ai_agent_engineer",
        "role_version": "2026-H2",
        "manifest_version": "2026-08-27.1",
        "question_count": 30,
        "question_ids": QUESTION_IDS,
        "dimension_quotas": {
            "role_dim_01": 6,
            "role_dim_02": 5,
            "role_dim_03": 6,
            "role_dim_04": 4,
            "role_dim_05": 6,
            "role_dim_06": 3,
        },
        "primary_mode_quotas": {
            "foundation": 4,
            "project_deep_dive": 5,
            "scenario": 8,
            "system_design": 4,
            "coding": 3,
            "follow_up": 6,
        },
        "mode_policy_version": "2026-H2",
        "min_independent_urls": 12,
        "max_questions_per_url": 3,
        "corpus_as_of": CORPUS_AS_OF,
        "signal_near_180_min_count": 18,
        "signal_near_365_min_count": 27,
        "signal_fallback_start": date(2025, 1, 1),
        "signal_fallback_max_count": 3,
        "dynamic_review_days": 180,
        "evergreen_review_days": 365,
        "evergreen_revalidation_days": 180,
        "current_jd_validation_days": 180,
        "active_count": 0,
        "active_trust_levels": ["medium", "high"],
        "generated_at": CORPUS_AS_OF,
        "reviewed_at": CORPUS_AS_OF,
        "publication_status": "draft",
        "question_set_hash": "sha256:questions",
        "sidecar_set_hash": "sha256:sidecars",
        "embedding_contract_version": "six-section-v1",
    }


def valid_review_kwargs(question_id: str = QUESTION_IDS[0]) -> dict:
    return {
        "question_id": question_id,
        "decision": "pending_human",
        "reviewer_ids": ["Luna-1", "Luna-2"],
        "reviewed_at": CORPUS_AS_OF,
        "signal_source_ids": ["src_public_001"],
        "cross_validation_source_ids": ["src_official_001"],
        "capability_summary": "覆盖失败恢复与编排边界。",
        "business_constraint_summary": "必须保持幂等和延迟预算。",
        "mode_rationale": "场景题能验证故障边界。",
        "originality_confirmed": True,
        "pii_scan_passed": True,
        "rights_review_passed": True,
        "difficulty_consistent": True,
        "review_class": "dynamic",
        "review_due_at": date(2027, 2, 23),
    }


def valid_dedupe_kwargs(question_id: str = QUESTION_IDS[0]) -> dict:
    return {
        "question_id": question_id,
        "semantic_hash": "sha256:semantic-001",
        "comparison_batch": "batch-2026-08-27",
        "candidate_duplicate_group": [],
        "near_duplicate_decision": "clear",
        "decision": "unique",
        "reviewed_at": CORPUS_AS_OF,
    }


def valid_rights_kwargs(
    question_id: str = QUESTION_IDS[0], source_id: str = "src_public_001"
) -> dict:
    return {
        "question_id": question_id,
        "source_id": source_id,
        "public_access": True,
        "paraphrase_only": True,
        "original_text_present": False,
        "answer_present": False,
        "no_pii": True,
        "no_paid_content": True,
        "originality_confirmed": True,
        "decision": "approved",
        "reviewed_at": CORPUS_AS_OF,
    }


def valid_locator_kwargs(
    question_id: str = QUESTION_IDS[0], source_id: str = "src_public_001"
) -> dict:
    return {
        "question_id": question_id,
        "source_id": source_id,
        "canonical_url": "https://example.com/interview",
        "section": "Failure recovery",
        "heading": "Idempotent tool execution",
        "published_date": date(2026, 7, 1),
        "page": 1,
        "time_range": "00:12-00:18",
        "viewed_at": CORPUS_AS_OF,
        "locator_hash": "sha256:locator-001",
    }


def valid_intent_kwargs() -> dict:
    return {
        "intent_id": "intent_001",
        "role": "ai_agent_engineer",
        "role_version": "2026-H2",
        "dimension_id": "role_dim_01",
        "requested_mode": "scenario",
        "query_text": "工具失败恢复和幂等设计",
        "gold_question_id": QUESTION_IDS[0],
        "acceptable_question_ids": [QUESTION_IDS[0]],
        "hard_negative_ids": ["q_agent_hard_001"],
        "label_notes": "gold 与请求 mode 精确匹配。",
    }


def valid_snapshot() -> QuestionCorpusSnapshot:
    source = QuestionSourceRegistryEntry(**valid_source_kwargs())
    registry = QuestionSourceRegistry(entries=[source])
    review_sidecar = QuestionReviewSidecar(
        records=[QuestionReviewRecord(**valid_review_kwargs())]
    )
    dedupe_sidecar = QuestionDedupeSidecar(
        records=[QuestionDedupeRecord(**valid_dedupe_kwargs())]
    )
    rights_sidecar = QuestionRightsSidecar(
        records=[QuestionRightsRecord(**valid_rights_kwargs())]
    )
    locator_sidecar = QuestionLocatorSidecar(
        records=[QuestionLocatorRecord(**valid_locator_kwargs())]
    )
    return QuestionCorpusSnapshot(
        records=[
            InterviewQuestionRecord(**valid_record_kwargs(question_id))
            for question_id in QUESTION_IDS
        ],
        manifest=QuestionBankManifest(**valid_manifest_kwargs()),
        source_registry=registry,
        review=review_sidecar,
        dedupe=dedupe_sidecar,
        rights=rights_sidecar,
        locator=locator_sidecar,
    )


class QuestionCorpusSchemaTests(unittest.TestCase):
    def test_accepts_v2_record_and_keeps_v1_question_mode(self) -> None:
        record = InterviewQuestionRecord(**valid_record_kwargs())

        self.assertEqual(record.question_mode, "scenario")
        self.assertEqual(record.primary_mode, "scenario")
        self.assertEqual(record.compatible_modes, ["system_design", "follow_up"])

    def test_accepts_all_governance_sidecars_and_snapshot(self) -> None:
        source = QuestionSourceRegistryEntry(**valid_source_kwargs())
        registry = QuestionSourceRegistry(entries=[source])
        review = QuestionReviewRecord(**valid_review_kwargs())
        review_sidecar = QuestionReviewSidecar(records=[review])
        dedupe = QuestionDedupeRecord(**valid_dedupe_kwargs())
        dedupe_sidecar = QuestionDedupeSidecar(records=[dedupe])
        rights = QuestionRightsRecord(**valid_rights_kwargs())
        rights_sidecar = QuestionRightsSidecar(records=[rights])
        locator = QuestionLocatorRecord(**valid_locator_kwargs())
        locator_sidecar = QuestionLocatorSidecar(records=[locator])
        manifest = QuestionBankManifest(**valid_manifest_kwargs())
        intent = LabeledQuestionIntent(**valid_intent_kwargs())

        snapshot = QuestionCorpusSnapshot(
            records=[
                InterviewQuestionRecord(**valid_record_kwargs(question_id))
                for question_id in QUESTION_IDS
            ],
            manifest=manifest,
            source_registry=registry,
            review=review_sidecar,
            dedupe=dedupe_sidecar,
            rights=rights_sidecar,
            locator=locator_sidecar,
        )

        self.assertEqual(snapshot.manifest.question_count, 30)
        self.assertEqual(intent.gold_question_id, QUESTION_IDS[0])

    def test_all_contract_models_forbid_unknown_fields(self) -> None:
        cases = [
            (InterviewQuestionRecord, valid_record_kwargs()),
            (QuestionSourceRegistryEntry, valid_source_kwargs()),
            (QuestionBankManifest, valid_manifest_kwargs()),
            (QuestionReviewRecord, valid_review_kwargs()),
            (QuestionDedupeRecord, valid_dedupe_kwargs()),
            (QuestionRightsRecord, valid_rights_kwargs()),
            (QuestionLocatorRecord, valid_locator_kwargs()),
            (LabeledQuestionIntent, valid_intent_kwargs()),
        ]
        for model, values in cases:
            with self.subTest(model=model.__name__):
                invalid = deepcopy(values)
                invalid["unexpected_field"] = "must fail"
                with self.assertRaises(ValidationError):
                    model(**invalid)

    def test_sidecars_forbid_unknown_fields(self) -> None:
        cases = [
            (QuestionSourceRegistry, {"entries": [valid_source_kwargs()]}),
            (QuestionReviewSidecar, {"records": [valid_review_kwargs()]}),
            (QuestionDedupeSidecar, {"records": [valid_dedupe_kwargs()]}),
            (QuestionRightsSidecar, {"records": [valid_rights_kwargs()]}),
            (QuestionLocatorSidecar, {"records": [valid_locator_kwargs()]}),
        ]
        for model, values in cases:
            with self.subTest(model=model.__name__):
                invalid = deepcopy(values)
                invalid["unexpected_field"] = "must fail"
                with self.assertRaises(ValidationError):
                    model(**invalid)

    def test_sidecars_reject_duplicate_primary_keys(self) -> None:
        duplicate_cases = [
            (QuestionSourceRegistry, [valid_source_kwargs(), valid_source_kwargs()]),
            (QuestionReviewSidecar, [valid_review_kwargs(), valid_review_kwargs()]),
            (QuestionDedupeSidecar, [valid_dedupe_kwargs(), valid_dedupe_kwargs()]),
            (QuestionRightsSidecar, [valid_rights_kwargs(), valid_rights_kwargs()]),
            (QuestionLocatorSidecar, [valid_locator_kwargs(), valid_locator_kwargs()]),
        ]
        for model, records in duplicate_cases:
            with self.subTest(model=model.__name__):
                with self.assertRaises(ValidationError):
                    model(records=records) if model is not QuestionSourceRegistry else model(entries=records)

    def test_contract_models_reject_wrong_date_types(self) -> None:
        cases = [
            (QuestionBankManifest, valid_manifest_kwargs(), "corpus_as_of"),
            (QuestionSourceRegistryEntry, valid_source_kwargs(), "verified_at"),
            (QuestionReviewRecord, valid_review_kwargs(), "reviewed_at"),
            (QuestionDedupeRecord, valid_dedupe_kwargs(), "reviewed_at"),
            (QuestionRightsRecord, valid_rights_kwargs(), "reviewed_at"),
            (QuestionLocatorRecord, valid_locator_kwargs(), "viewed_at"),
        ]
        for model, values, field in cases:
            with self.subTest(model=model.__name__, field=field):
                invalid = deepcopy(values)
                invalid[field] = "not-a-date"
                with self.assertRaises(ValidationError):
                    model(**invalid)

    def test_contract_models_reject_empty_primary_keys(self) -> None:
        cases = [
            (InterviewQuestionRecord, valid_record_kwargs(), "question_id"),
            (QuestionSourceRegistryEntry, valid_source_kwargs(), "source_id"),
            (QuestionBankManifest, valid_manifest_kwargs(), "bank_id"),
            (QuestionReviewRecord, valid_review_kwargs(), "question_id"),
            (QuestionDedupeRecord, valid_dedupe_kwargs(), "question_id"),
            (QuestionRightsRecord, valid_rights_kwargs(), "question_id"),
            (QuestionLocatorRecord, valid_locator_kwargs(), "question_id"),
            (LabeledQuestionIntent, valid_intent_kwargs(), "intent_id"),
        ]
        for model, values, field in cases:
            with self.subTest(model=model.__name__, field=field):
                invalid = deepcopy(values)
                invalid[field] = "  "
                with self.assertRaises(ValidationError):
                    model(**invalid)

    def test_rejects_invalid_enums(self) -> None:
        cases = [
            (InterviewQuestionRecord, valid_record_kwargs(), "primary_mode"),
            (QuestionSourceRegistryEntry, valid_source_kwargs(), "source_type"),
            (QuestionReviewRecord, valid_review_kwargs(), "decision"),
            (QuestionDedupeRecord, valid_dedupe_kwargs(), "decision"),
            (QuestionRightsRecord, valid_rights_kwargs(), "decision"),
            (LabeledQuestionIntent, valid_intent_kwargs(), "requested_mode"),
        ]
        for model, values, field in cases:
            with self.subTest(model=model.__name__, field=field):
                invalid = deepcopy(values)
                invalid[field] = "not-a-valid-enum"
                with self.assertRaises(ValidationError):
                    model(**invalid)

    def test_fixed_mode_policy_rejects_unsupported_assignments(self) -> None:
        policy = QuestionModePolicy()

        self.assertEqual(
            policy.modes,
            (
                "foundation",
                "project_deep_dive",
                "scenario",
                "system_design",
                "coding",
                "follow_up",
            ),
        )
        policy.validate_mode_assignment(
            "role_dim_01", "scenario", ["foundation", "follow_up"]
        )

        invalid_assignments = [
            ("role_dim_01", "project", []),
            ("role_dim_01", "coding", []),
            ("role_dim_01", "scenario", ["scenario"]),
            ("role_dim_01", "scenario", ["follow_up", "follow_up"]),
            ("role_dim_01", "scenario", ["coding"]),
            ("role_dim_07", "scenario", []),
        ]
        for dimension_id, primary_mode, compatible_modes in invalid_assignments:
            with self.subTest(
                dimension_id=dimension_id,
                primary_mode=primary_mode,
                compatible_modes=compatible_modes,
            ):
                with self.assertRaises(ValueError):
                    policy.validate_mode_assignment(
                        dimension_id, primary_mode, compatible_modes
                    )

    def test_fixed_quotas_reject_wrong_count_or_distribution(self) -> None:
        quotas = QuestionCorpusQuotas()
        self.assertEqual(quotas.question_count, 30)
        self.assertEqual(quotas.dimension_quotas["role_dim_01"], 6)
        self.assertEqual(quotas.primary_mode_quotas["project_deep_dive"], 5)

        with self.assertRaises(ValidationError):
            QuestionCorpusQuotas(question_count=29)
        with self.assertRaises(ValidationError):
            QuestionCorpusQuotas(
                dimension_quotas={
                    "role_dim_01": 5,
                    "role_dim_02": 5,
                    "role_dim_03": 6,
                    "role_dim_04": 4,
                    "role_dim_05": 6,
                    "role_dim_06": 3,
                }
            )
        with self.assertRaises(ValidationError):
            QuestionCorpusQuotas(
                primary_mode_quotas={
                    "foundation": 4,
                    "project_deep_dive": 5,
                    "scenario": 8,
                    "system_design": 4,
                    "coding": 4,
                    "follow_up": 5,
                }
            )

    def test_manifest_count_is_exactly_thirty(self) -> None:
        values = valid_manifest_kwargs()
        values["question_count"] = 29

        with self.assertRaises(ValidationError):
            QuestionBankManifest(**values)

    def test_record_and_intent_are_bound_to_the_corpus_role_version_and_dimensions(self) -> None:
        for model, values in (
            (InterviewQuestionRecord, valid_record_kwargs()),
            (LabeledQuestionIntent, valid_intent_kwargs()),
        ):
            for field, bad_value in (
                ("role_version", "2026-H1"),
                ("dimension_id", "role_dim_07"),
            ):
                with self.subTest(model=model.__name__, field=field):
                    invalid = deepcopy(values)
                    invalid[field] = bad_value
                    with self.assertRaises(ValidationError):
                        model(**invalid)

    def test_explicit_question_mode_and_primary_mode_mismatch_is_rejected(self) -> None:
        values = valid_record_kwargs()
        values["question_mode"] = "scenario"
        values["primary_mode"] = "coding"
        values.pop("business_constraint")
        values.pop("dimension_terms")
        values.pop("compatible_modes")
        values.pop("source_ids")

        with self.assertRaises(ValidationError):
            InterviewQuestionRecord(**values)

    def test_record_rejects_wrong_date_types(self) -> None:
        values = valid_record_kwargs()
        values["published_at"] = "not-a-date"

        with self.assertRaises(ValidationError):
            InterviewQuestionRecord(**values)

    def test_source_dates_enforce_public_signal_window_and_access_order(self) -> None:
        for field, bad_value in (
            ("published_at", None),
            ("published_at", date(2024, 12, 31)),
            ("published_at", date(2026, 8, 28)),
            ("accessed_at", date(2026, 6, 30)),
            ("verified_at", date(2026, 6, 30)),
        ):
            with self.subTest(field=field, bad_value=bad_value):
                values = valid_source_kwargs()
                values[field] = bad_value
                with self.assertRaises(ValidationError):
                    QuestionSourceRegistryEntry(**values)

        evergreen = valid_source_kwargs()
        evergreen["source_type"] = "official_technical_doc"
        evergreen["published_at"] = None
        evergreen["date_basis"] = "retrieved_at"
        self.assertIsNotNone(QuestionSourceRegistryEntry(**evergreen))

    def test_source_dates_reject_access_or_verification_after_corpus_as_of(self) -> None:
        future_access = valid_source_kwargs()
        future_access["accessed_at"] = date(2026, 8, 28)
        future_access["verified_at"] = date(2026, 8, 29)
        with self.assertRaises(ValidationError):
            QuestionSourceRegistryEntry(**future_access)

        future_verification = valid_source_kwargs()
        future_verification["verified_at"] = date(2026, 8, 28)
        with self.assertRaises(ValidationError):
            QuestionSourceRegistryEntry(**future_verification)

    def test_snapshot_requires_exactly_thirty_unique_manifest_ids(self) -> None:
        snapshot = valid_snapshot()

        self.assertEqual(len(snapshot.records), 30)
        self.assertEqual(
            {record.question_id for record in snapshot.records},
            set(snapshot.manifest.question_ids),
        )

        with self.assertRaises(ValidationError):
            QuestionCorpusSnapshot(
                **{
                    **snapshot.model_dump(),
                    "records": snapshot.records[:-1],
                }
            )

        duplicate_records = [*snapshot.records]
        duplicate_records[-1] = duplicate_records[0]
        with self.assertRaises(ValidationError):
            QuestionCorpusSnapshot(
                **{
                    **snapshot.model_dump(),
                    "records": duplicate_records,
                }
            )

        mismatched_records = [*snapshot.records]
        mismatched_records[-1] = InterviewQuestionRecord(
            **valid_record_kwargs("q_agent_other")
        )
        with self.assertRaises(ValidationError):
            QuestionCorpusSnapshot(
                **{
                    **snapshot.model_dump(),
                    "records": mismatched_records,
                }
            )

    def test_policy_and_manifest_use_the_same_fixed_policy_version(self) -> None:
        policy = QuestionModePolicy()
        manifest = QuestionBankManifest(**valid_manifest_kwargs())
        self.assertEqual(policy.mode_policy_version, manifest.mode_policy_version)

        with self.assertRaises(ValidationError):
            QuestionModePolicy(mode_policy_version="future-policy")
        invalid_manifest = valid_manifest_kwargs()
        invalid_manifest["mode_policy_version"] = "future-policy"
        with self.assertRaises(ValidationError):
            QuestionBankManifest(**invalid_manifest)

    def test_approved_review_requires_evidence_and_all_safety_checks(self) -> None:
        values = valid_review_kwargs()
        values["decision"] = "approved"
        for field, bad_value in (
            ("reviewer_ids", []),
            ("signal_source_ids", []),
            ("cross_validation_source_ids", []),
            ("originality_confirmed", False),
            ("pii_scan_passed", False),
            ("rights_review_passed", False),
            ("difficulty_consistent", False),
        ):
            with self.subTest(field=field):
                invalid = deepcopy(values)
                invalid[field] = bad_value
                with self.assertRaises(ValidationError):
                    QuestionReviewRecord(**invalid)

    def test_approved_review_does_not_treat_corpus_human_fields_as_trust(self) -> None:
        values = valid_review_kwargs()
        values.update(
            {
                "decision": "approved",
                "reviewer_ids": ["reviewer-1"],
                "reviewer_type": "human",
                "approval_actor": "human:reviewer-1",
                "approval_timestamp": CORPUS_AS_OF,
            }
        )
        review = QuestionReviewRecord(**values)
        self.assertEqual(review.reviewer_type, "human")
        self.assertEqual(review.approval_actor, "human:reviewer-1")

        for field, bad_value in (
            ("reviewer_type", "agent"),
            ("reviewer_type", "model"),
            ("reviewer_type", "luna"),
        ):
            with self.subTest(field=field, bad_value=bad_value):
                invalid = deepcopy(values)
                invalid[field] = bad_value
                with self.assertRaises(ValidationError):
                    QuestionReviewRecord(**invalid)

        without_corpus_approval_fields = deepcopy(values)
        without_corpus_approval_fields.pop("approval_actor")
        without_corpus_approval_fields.pop("approval_timestamp")
        review_without_trusted_fields = QuestionReviewRecord(
            **without_corpus_approval_fields
        )
        self.assertIsNone(review_without_trusted_fields.approval_actor)
        self.assertIsNone(review_without_trusted_fields.approval_timestamp)

    def test_fallback_reason_code_is_a_strict_coverage_gap_enum(self) -> None:
        values = valid_review_kwargs()
        values.update(
            {
                "exception_reason_code": "coverage_gap",
                "exception_reason": "coverage gap: no recent interview signal",
            }
        )
        review = QuestionReviewRecord(**values)
        self.assertEqual(review.exception_reason_code, "coverage_gap")

        invalid = deepcopy(values)
        invalid["exception_reason_code"] = "convenience"
        with self.assertRaises(ValidationError):
            QuestionReviewRecord(**invalid)

    def test_approved_rights_rejects_contradictory_access_and_safety_flags(self) -> None:
        values = valid_rights_kwargs()
        for field, bad_value in (
            ("public_access", False),
            ("paraphrase_only", False),
            ("no_pii", False),
            ("no_paid_content", False),
            ("original_text_present", True),
            ("answer_present", True),
        ):
            with self.subTest(field=field):
                invalid = deepcopy(values)
                invalid[field] = bad_value
                with self.assertRaises(ValidationError):
                    QuestionRightsRecord(**invalid)


if __name__ == "__main__":
    unittest.main()
