from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from profile_agent.services.question_bank_service import compute_question_content_hash
from profile_agent.services.question_corpus_governance import (
    CorpusIssue,
    build_manifest_preview,
    compute_question_set_hash,
    compute_sidecar_set_hash,
    load_question_corpus_snapshot,
    validate_question_corpus,
)
from profile_agent.schemas.question_rag_schema import (
    CORPUS_AS_OF,
    InterviewQuestionRecord,
    QuestionBankManifest,
    QuestionCorpusSnapshot,
    QuestionDedupeRecord,
    QuestionDedupeSidecar,
    QuestionLocatorRecord,
    QuestionLocatorSidecar,
    QuestionReviewRecord,
    QuestionReviewSidecar,
    QuestionRightsRecord,
    QuestionRightsSidecar,
    QuestionSourceRegistry,
    QuestionSourceRegistryEntry,
)

from tests.test_question_corpus_schema import (
    QUESTION_IDS,
    valid_dedupe_kwargs,
    valid_locator_kwargs,
    valid_manifest_kwargs,
    valid_record_kwargs,
    valid_review_kwargs,
    valid_rights_kwargs,
    valid_source_kwargs,
)


class QuestionCorpusGovernanceTests(unittest.TestCase):
    def _complete_snapshot(self) -> QuestionCorpusSnapshot:
        dimensions = (
            ["role_dim_01"] * 6
            + ["role_dim_02"] * 5
            + ["role_dim_03"] * 6
            + ["role_dim_04"] * 4
            + ["role_dim_05"] * 6
            + ["role_dim_06"] * 3
        )
        modes = (
            ["foundation", "project_deep_dive", "scenario", "system_design", "follow_up", "scenario"]
            + ["foundation", "scenario", "project_deep_dive", "scenario", "follow_up"]
            + ["foundation", "scenario", "system_design", "coding", "follow_up", "project_deep_dive"]
            + ["project_deep_dive", "scenario", "coding", "follow_up"]
            + ["foundation", "scenario", "system_design", "scenario", "follow_up", "project_deep_dive"]
            + ["coding", "system_design", "follow_up"]
        )
        records: list[InterviewQuestionRecord] = []
        sources: list[QuestionSourceRegistryEntry] = []
        reviews: list[QuestionReviewRecord] = []
        dedupe: list[QuestionDedupeRecord] = []
        rights: list[QuestionRightsRecord] = []
        locators: list[QuestionLocatorRecord] = []
        for index, (dimension, mode) in enumerate(zip(dimensions, modes), start=1):
            question_id = f"q_agent_{index:03d}"
            source_group = (index - 1) // 3 + 1
            interview_id = f"src_interview_{source_group:02d}"
            official_id = f"src_official_{source_group:02d}"
            interview_url = f"https://interview.example.test/signals/{source_group:02d}"
            official_url = f"https://docs.example.test/agent/{source_group:02d}"
            values = valid_record_kwargs(question_id)
            values.update(
                {
                    "dimension_id": dimension,
                    "question_mode": mode,
                    "primary_mode": mode,
                    "compatible_modes": [],
                    "source_id": interview_id,
                    "source_ids": [interview_id, official_id],
                    "source_url": interview_url,
                    "source_title": f"Interview signal {source_group}",
                    "source_type": "public_interview_experience",
                    "published_at": date(2026, 7, 1),
                    "verified_at": date(2026, 8, 26),
                    "valid_until": date(2027, 2, 22),
                    "status": "active",
                    "trust_level": "high",
                    "content_hash": "sha256:placeholder",
                }
            )
            initial = InterviewQuestionRecord(**values)
            values["content_hash"] = compute_question_content_hash(initial)
            record = InterviewQuestionRecord(**values)
            records.append(record)
            reviews.append(
                QuestionReviewRecord(
                    question_id=question_id,
                    decision="approved",
                    reviewer_ids=["reviewer-1"],
                    reviewer_type="human",
                    approval_actor="human:reviewer-1",
                    approval_timestamp=date(2026, 8, 27),
                    reviewed_at=date(2026, 8, 27),
                    signal_source_ids=[interview_id],
                    cross_validation_source_ids=[official_id],
                    capability_summary="覆盖编排与失败恢复能力。",
                    business_constraint_summary="必须满足幂等和延迟约束。",
                    dimension_summary="验证对应 role dimension 的能力信号。",
                    mode_rationale="场景题可验证约束下的决策。",
                    originality_confirmed=True,
                    pii_scan_passed=True,
                    rights_review_passed=True,
                    difficulty_consistent=True,
                    review_class="dynamic",
                    review_due_at=date(2027, 2, 22),
                )
            )
            dedupe.append(
                QuestionDedupeRecord(
                    question_id=question_id,
                    semantic_hash=record.content_hash,
                    comparison_batch="batch-2026-08-27",
                    near_duplicate_decision="clear",
                    decision="unique",
                    reviewed_at=date(2026, 8, 27),
                )
            )
            for source_id, source_url, source_type, title in (
                (interview_id, interview_url, "public_interview_experience", "Interview"),
                (official_id, official_url, "official_technical_doc", "Official"),
            ):
                rights.append(
                    QuestionRightsRecord(
                        question_id=question_id,
                        source_id=source_id,
                        public_access=True,
                        paraphrase_only=True,
                        no_pii=True,
                        no_paid_content=True,
                        originality_confirmed=True,
                        decision="approved",
                        reviewed_at=date(2026, 8, 27),
                    )
                )
                locators.append(
                    QuestionLocatorRecord(
                        question_id=question_id,
                        source_id=source_id,
                        canonical_url=source_url,
                        section="Engineering context",
                        viewed_at=date(2026, 8, 27),
                        locator_hash=f"sha256:locator-{question_id}-{source_id}",
                    )
                )

        for source_group in range(1, 11):
            question_ids = [
                f"q_agent_{index:03d}"
                for index in range((source_group - 1) * 3 + 1, source_group * 3 + 1)
            ]
            sources.append(
                QuestionSourceRegistryEntry(
                    source_id=f"src_interview_{source_group:02d}",
                    source_type="public_interview_experience",
                    canonical_url=f"https://interview.example.test/signals/{source_group:02d}",
                    title="Interview signal",
                    publisher="Public platform",
                    published_at=date(2026, 7, 1),
                    verified_at=date(2026, 8, 26),
                    accessed_at=date(2026, 8, 26),
                    trust="high",
                    lifecycle="active",
                    question_ids=question_ids,
                    review_class="dynamic",
                    next_review_at=date(2027, 2, 22),
                    access_status="accessible",
                    rights_status="approved",
                )
            )
            sources.append(
                QuestionSourceRegistryEntry(
                    source_id=f"src_official_{source_group:02d}",
                    source_type="official_technical_doc",
                    canonical_url=f"https://docs.example.test/agent/{source_group:02d}",
                    title="Official engineering note",
                    publisher="Official docs",
                    published_at=date(2026, 6, 1),
                    verified_at=date(2026, 8, 26),
                    accessed_at=date(2026, 8, 26),
                    trust="high",
                    lifecycle="active",
                    question_ids=question_ids,
                    review_class="evergreen",
                    next_review_at=date(2027, 8, 26),
                    access_status="accessible",
                    rights_status="approved",
                )
            )
        manifest_values = valid_manifest_kwargs()
        manifest_values.update(
            {
                "question_ids": [record.question_id for record in records],
                "active_count": 30,
                "publication_status": "published",
                "published_at": CORPUS_AS_OF,
            }
        )
        snapshot = QuestionCorpusSnapshot(
            records=records,
            manifest=QuestionBankManifest(**manifest_values),
            source_registry=QuestionSourceRegistry(entries=sources),
            review=QuestionReviewSidecar(records=reviews),
            dedupe=QuestionDedupeSidecar(records=dedupe),
            rights=QuestionRightsSidecar(records=rights),
            locator=QuestionLocatorSidecar(records=locators),
        )
        manifest = snapshot.manifest.model_copy(
            update={
                "question_set_hash": compute_question_set_hash(snapshot.records),
                "sidecar_set_hash": compute_sidecar_set_hash(snapshot),
            }
        )
        return snapshot.model_copy(update={"manifest": manifest})
    def _write_snapshot(self, root: Path) -> None:
        records = []
        for question_id in QUESTION_IDS:
            values = valid_record_kwargs(question_id)
            values["content_hash"] = compute_question_content_hash(
                __import__(
                    "profile_agent.schemas.question_rag_schema",
                    fromlist=["InterviewQuestionRecord"],
                ).InterviewQuestionRecord(**values)
            )
            records.append(values)
        manifest = valid_manifest_kwargs()
        source = valid_source_kwargs()
        review = valid_review_kwargs()
        dedupe = valid_dedupe_kwargs()
        rights = valid_rights_kwargs()
        locator = valid_locator_kwargs()
        payloads = {
            "questions.json": {"schema_version": 2, "role": "ai_agent_engineer", "role_version": "2026-H2", "questions": records},
            "QuestionBankManifest.json": manifest,
            "QuestionSourceRegistry.json": {"entries": [source]},
            "review.json": {"records": [review]},
            "dedupe.json": {"records": [dedupe]},
            "rights.json": {"records": [rights]},
            "locator.json": {"records": [locator]},
        }
        for filename, payload in payloads.items():
            (root / filename).write_text(
                json.dumps(payload, ensure_ascii=False, default=str),
                encoding="utf-8",
            )

    def test_load_question_corpus_snapshot_reads_all_governance_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_snapshot(root)

            snapshot = load_question_corpus_snapshot(root, date(2026, 8, 27))

        self.assertIsInstance(snapshot, QuestionCorpusSnapshot)
        self.assertEqual(len(snapshot.records), 30)
        self.assertEqual(snapshot.manifest.question_count, 30)
        self.assertEqual(len(snapshot.source_registry.entries), 1)

    def test_validate_question_corpus_returns_fixed_structured_issues(self) -> None:
        issue = CorpusIssue(
            code="example",
            path="questions.json",
            message="example finding",
            severity="error",
        )

        self.assertEqual(
            (issue.code, issue.path, issue.message, issue.severity),
            ("example", "questions.json", "example finding", "error"),
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_snapshot(root)
            snapshot = load_question_corpus_snapshot(root, date(2026, 8, 27))

        issues = validate_question_corpus(snapshot, {}, date(2026, 8, 27))
        self.assertIsInstance(issues, (list, tuple))
        self.assertTrue(all(isinstance(item, CorpusIssue) for item in issues))

    def test_complete_snapshot_passes_all_governance_gates(self) -> None:
        snapshot = self._complete_snapshot()
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        issues = validate_question_corpus(snapshot, role_pack, date(2026, 8, 27))

        self.assertEqual(issues, [])
        preview = build_manifest_preview(snapshot, issues)
        self.assertTrue(str(preview["manifest_hash"]).startswith("sha256:"))

    def test_validator_reports_active_low_trust_and_missing_sidecars(self) -> None:
        snapshot = self._complete_snapshot()
        low_trust = snapshot.records[0].model_copy(update={"trust_level": "low"})
        without_rights = snapshot.rights.model_copy(
            update={"records": snapshot.rights.records[1:]}
        )
        malformed = snapshot.model_copy(
            update={
                "records": [low_trust, *snapshot.records[1:]],
                "rights": without_rights,
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("active_low_trust", codes)
        self.assertIn("rights_missing", codes)

    def test_canonical_url_tracking_parameters_are_not_independent_sources(self) -> None:
        first = "https://docs.example.test/agent/1?utm_source=mail"
        second = "https://docs.example.test/agent/1?fbclid=tracking"

        from profile_agent.services.question_corpus_governance import canonicalize_source_url

        self.assertEqual(canonicalize_source_url(first), canonicalize_source_url(second))

    def test_validator_rejects_source_taxonomy_and_unsafe_source_page(self) -> None:
        snapshot = self._complete_snapshot()
        source = snapshot.source_registry.entries[0].model_copy(
            update={
                "source_type": "other_source",
                "canonical_url": "https://search.example.test/results?q=agent",
            }
        )
        malformed = snapshot.model_copy(
            update={
                "source_registry": snapshot.source_registry.model_copy(
                    update={"entries": [source, *snapshot.source_registry.entries[1:]]}
                )
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("source_type", codes)
        self.assertIn("source_access", codes)

    def test_validator_rejects_orphaned_rights_and_locator_relations(self) -> None:
        snapshot = self._complete_snapshot()
        orphan_rights = QuestionRightsRecord(
            question_id="q_orphan",
            source_id="src_orphan",
            public_access=True,
            paraphrase_only=True,
            no_pii=True,
            no_paid_content=True,
            originality_confirmed=True,
            decision="approved",
            reviewed_at=CORPUS_AS_OF,
        )
        orphan_locator = QuestionLocatorRecord(
            question_id="q_orphan",
            source_id="src_orphan",
            canonical_url="https://orphan.example.test/source",
            section="Unknown",
            viewed_at=CORPUS_AS_OF,
            locator_hash="sha256:orphan",
        )
        malformed = snapshot.model_copy(
            update={
                "rights": snapshot.rights.model_copy(
                    update={"records": [*snapshot.rights.records, orphan_rights]}
                ),
                "locator": snapshot.locator.model_copy(
                    update={"records": [*snapshot.locator.records, orphan_locator]}
                ),
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("rights_fk", codes)
        self.assertIn("locator_fk", codes)

    def test_validator_checks_manifest_mode_policy_version(self) -> None:
        snapshot = self._complete_snapshot()
        malformed = snapshot.model_copy(
            update={
                "manifest": snapshot.manifest.model_copy(
                    update={"mode_policy_version": "old-policy"}
                )
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("mode_policy_version", codes)

    def test_fallback_count_uses_the_most_recent_interview_signal(self) -> None:
        snapshot = self._complete_snapshot()
        old_interview = snapshot.source_registry.entries[2].model_copy(
            update={"published_at": date(2025, 8, 1)}
        )
        sources = [*snapshot.source_registry.entries]
        sources[2] = old_interview
        review = snapshot.review.records[0].model_copy(
            update={"signal_source_ids": ["src_interview_01", "src_interview_02"]}
        )
        reviews = [*snapshot.review.records]
        reviews[0] = review
        malformed = snapshot.model_copy(
            update={
                "source_registry": snapshot.source_registry.model_copy(
                    update={"entries": sources}
                ),
                "review": snapshot.review.model_copy(update={"records": reviews}),
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertNotIn("fallback_count", codes)

    def test_validator_rejects_source_access_after_verification(self) -> None:
        snapshot = self._complete_snapshot()
        sources = [*snapshot.source_registry.entries]
        sources[0] = sources[0].model_copy(update={"accessed_at": CORPUS_AS_OF})
        malformed = snapshot.model_copy(
            update={
                "source_registry": snapshot.source_registry.model_copy(
                    update={"entries": sources}
                )
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("source_date_order", codes)

    def test_validator_rejects_reposted_source_markers(self) -> None:
        snapshot = self._complete_snapshot()
        source = snapshot.source_registry.entries[0].model_copy(
            update={"notes": "reposted copy"}
        )
        malformed = snapshot.model_copy(
            update={
                "source_registry": snapshot.source_registry.model_copy(
                    update={"entries": [source, *snapshot.source_registry.entries[1:]]}
                )
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("source_access", codes)

    def test_validator_rejects_legacy_and_primary_mode_drift(self) -> None:
        snapshot = self._complete_snapshot()
        drifted = snapshot.records[0].model_copy(update={"question_mode": "coding"})
        malformed = snapshot.model_copy(
            update={"records": [drifted, *snapshot.records[1:]]}
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("mode_invalid", codes)

    def test_validator_rejects_luna_approval_even_with_approval_fields(self) -> None:
        snapshot = self._complete_snapshot()
        review = snapshot.review.records[0].model_copy(
            update={
                "reviewer_type": "luna",
                "reviewer_ids": ["Luna-1", "Luna-2"],
                "approval_actor": "Luna-1",
                "approval_timestamp": CORPUS_AS_OF,
            }
        )
        malformed = snapshot.model_copy(
            update={
                "review": snapshot.review.model_copy(update={"records": [review, *snapshot.review.records[1:]]})
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("human_approval", codes)

    def test_validator_requires_canonical_semantic_hash_and_duplicate_group_fk(self) -> None:
        snapshot = self._complete_snapshot()
        dedupe = snapshot.dedupe.records[0].model_copy(
            update={
                "semantic_hash": "sha256:" + "0" * 64,
                "candidate_duplicate_group": ["q_missing"],
            }
        )
        malformed = snapshot.model_copy(
            update={
                "dedupe": snapshot.dedupe.model_copy(update={"records": [dedupe, *snapshot.dedupe.records[1:]]})
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("dedupe_semantic_hash", codes)
        self.assertIn("dedupe_candidate_fk", codes)

    def test_validator_rejects_active_duplicate_semantic_hash_and_unresolved_group(self) -> None:
        snapshot = self._complete_snapshot()
        first = snapshot.dedupe.records[0]
        second = snapshot.dedupe.records[1].model_copy(
            update={
                "semantic_hash": first.semantic_hash,
                "candidate_duplicate_group": [first.question_id],
                "decision": "pending",
                "near_duplicate_decision": "pending",
            }
        )
        malformed = snapshot.model_copy(
            update={
                "dedupe": snapshot.dedupe.model_copy(update={"records": [first, second, *snapshot.dedupe.records[2:]]})
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("dedupe_group_decision", codes)
        self.assertIn("active_duplicate_semantic_hash", codes)

    def test_validator_requires_source_review_deadlines_and_record_lifecycle_bound(self) -> None:
        snapshot = self._complete_snapshot()
        source = snapshot.source_registry.entries[0].model_copy(update={"next_review_at": None})
        record = snapshot.records[0].model_copy(update={"valid_until": date(2028, 1, 1)})
        malformed = snapshot.model_copy(
            update={
                "records": [record, *snapshot.records[1:]],
                "source_registry": snapshot.source_registry.model_copy(
                    update={"entries": [source, *snapshot.source_registry.entries[1:]]}
                ),
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("source_next_review", codes)
        self.assertIn("record_source_lifecycle", codes)

    def test_validator_requires_due_review_summaries_and_retirement_reason(self) -> None:
        snapshot = self._complete_snapshot()
        review = snapshot.review.records[0].model_copy(
            update={
                "review_due_at": CORPUS_AS_OF,
                "capability_summary": "",
                "business_constraint_summary": "",
                "dimension_summary": "",
                "mode_rationale": "",
                "decision": "retired",
            }
        )
        retired = snapshot.records[0].model_copy(update={"status": "active"})
        malformed = snapshot.model_copy(
            update={
                "records": [retired, *snapshot.records[1:]],
                "review": snapshot.review.model_copy(update={"records": [review, *snapshot.review.records[1:]]}),
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("review_due", codes)
        self.assertIn("review_summary", codes)
        self.assertIn("retirement_reason", codes)

    def test_validator_checks_fixed_manifest_contract_hashes_and_publication_dates(self) -> None:
        snapshot = self._complete_snapshot()
        manifest = snapshot.manifest.model_copy(
            update={
                "schema_version": "3",
                "embedding_contract_version": "legacy",
                "question_set_hash": "sha256:" + "0" * 64,
                "sidecar_set_hash": "sha256:" + "1" * 64,
                "generated_at": date(2026, 8, 28),
                "reviewed_at": date(2026, 8, 27),
                "published_at": date(2026, 8, 27),
            }
        )
        malformed = snapshot.model_copy(update={"manifest": manifest})
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("manifest_schema_version", codes)
        self.assertIn("embedding_contract_version", codes)
        self.assertIn("question_set_hash", codes)
        self.assertIn("sidecar_set_hash", codes)
        self.assertIn("manifest_dates", codes)

    def test_validator_rejects_structured_non_gap_fallback_reason(self) -> None:
        snapshot = self._complete_snapshot()
        source = snapshot.source_registry.entries[0].model_copy(
            update={"published_at": date(2025, 8, 1)}
        )
        review = snapshot.review.records[0].model_copy(
            update={
                "exception_reason_code": "coverage_gap",
                "exception_reason": "convenience because no data was available",
            }
        )
        malformed = snapshot.model_copy(
            update={
                "source_registry": snapshot.source_registry.model_copy(
                    update={"entries": [source, *snapshot.source_registry.entries[1:]]}
                ),
                "review": snapshot.review.model_copy(update={"records": [review, *snapshot.review.records[1:]]}),
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("fallback_exception", codes)

    def test_validator_counts_source_registry_question_ids_for_url_cap(self) -> None:
        snapshot = self._complete_snapshot()
        source = snapshot.source_registry.entries[0].model_copy(
            update={"question_ids": ["q_agent_001", "q_agent_002", "q_agent_003", "q_agent_004"]}
        )
        malformed = snapshot.model_copy(
            update={
                "source_registry": snapshot.source_registry.model_copy(
                    update={"entries": [source, *snapshot.source_registry.entries[1:] ]}
                )
            }
        )
        role_pack = json.loads(
            Path("profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json").read_text(
                encoding="utf-8"
            )
        )

        codes = {issue.code for issue in validate_question_corpus(malformed, role_pack, CORPUS_AS_OF)}

        self.assertIn("url_association_cap", codes)


if __name__ == "__main__":
    unittest.main()
