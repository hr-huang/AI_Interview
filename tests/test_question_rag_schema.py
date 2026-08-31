from datetime import date
import unittest

from pydantic import ValidationError

from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    QuestionRetrievalIntent,
    QuestionRetrievalResult,
    QuestionRetrievalTrace,
    RetrievedQuestion,
)


def valid_record_kwargs() -> dict:
    return {
        "question_id": "q_agent_001",
        "question_text": "工具执行成功但响应丢失，如何避免重复执行？",
        "role": "ai_agent_engineer",
        "role_version": "2026-H2",
        "dimension_id": "role_dim_01",
        "skills": ["幂等", "失败恢复"],
        "question_mode": "scenario",
        "difficulty": "intermediate",
        "expected_signals": ["幂等键", "执行状态查询"],
        "critical_errors": ["所有失败都直接重试"],
        "follow_up_seeds": ["首次已成功但响应丢失时怎么办？"],
        "company_tags": [],
        "source_id": "src_public_001",
        "source_url": "https://example.com/source",
        "source_title": "Public source",
        "source_type": "public_interview_experience",
        "published_at": date(2026, 7, 1),
        "verified_at": date(2026, 8, 26),
        "valid_until": date(2027, 2, 26),
        "trust_level": "medium",
        "status": "active",
        "version": 1,
        "content_hash": "sha256:abc",
    }


class InterviewQuestionRecordTests(unittest.TestCase):
    def test_accepts_a_valid_record(self) -> None:
        record = InterviewQuestionRecord(**valid_record_kwargs())

        self.assertEqual(record.question_id, "q_agent_001")
        self.assertEqual(record.question_mode, "scenario")
        self.assertIsInstance(record.question_mode, str)

    def test_rejects_empty_source_url(self) -> None:
        values = valid_record_kwargs()
        values["source_url"] = "   "

        with self.assertRaises(ValidationError):
            InterviewQuestionRecord(**values)

    def test_rejects_unsupported_role(self) -> None:
        values = valid_record_kwargs()
        values["role"] = "java_engineer"

        with self.assertRaises(ValidationError):
            InterviewQuestionRecord(**values)

    def test_rejects_invalid_lifecycle_dates(self) -> None:
        values = valid_record_kwargs()
        values["valid_until"] = date(2026, 8, 25)

        with self.assertRaises(ValidationError):
            InterviewQuestionRecord(**values)

    def test_rejects_empty_expected_signals(self) -> None:
        values = valid_record_kwargs()
        values["expected_signals"] = []

        with self.assertRaises(ValidationError):
            InterviewQuestionRecord(**values)

    def test_rejects_invalid_status(self) -> None:
        values = valid_record_kwargs()
        values["status"] = "draft"

        with self.assertRaises(ValidationError):
            InterviewQuestionRecord(**values)


class QuestionRetrievalSchemaTests(unittest.TestCase):
    def test_intent_reuses_the_existing_question_mode_contract(self) -> None:
        intent = QuestionRetrievalIntent(
            query_text="工具调用失败恢复与幂等设计",
            role="ai_agent_engineer",
            dimension_id="role_dim_01",
            question_mode="scenario",
            difficulty="intermediate",
        )

        self.assertEqual(intent.question_mode, "scenario")
        self.assertEqual(intent.excluded_question_ids, [])

    def test_hit_requires_a_selected_question_and_trace_ids(self) -> None:
        with self.assertRaises(ValidationError):
            QuestionRetrievalResult(status="hit")

        record = InterviewQuestionRecord(**valid_record_kwargs())
        selected = RetrievedQuestion(record=record, score=0.91, index_version="idx-v1")

        result = QuestionRetrievalResult(
            status="hit",
            selected_question=selected,
            trace=QuestionRetrievalTrace(
                status="hit",
                question_id=record.question_id,
                source_id=record.source_id,
                score=0.91,
                index_version="idx-v1",
            ),
            as_of=date(2026, 8, 26),
        )

        self.assertIs(result.selected_question, selected)
        self.assertEqual(result.trace.question_id, record.question_id)

    def test_hit_requires_as_of_and_an_active_non_expired_record(self) -> None:
        record = InterviewQuestionRecord(**valid_record_kwargs())
        selected = RetrievedQuestion(record=record, score=0.91, index_version="idx-v1")
        trace = QuestionRetrievalTrace(
            status="hit",
            question_id=record.question_id,
            source_id=record.source_id,
            score=0.91,
            index_version="idx-v1",
        )

        with self.assertRaises(ValidationError):
            QuestionRetrievalResult(
                status="hit",
                selected_question=selected,
                trace=trace,
            )

        for record_status in ("retired", "needs_review"):
            with self.subTest(record_status=record_status):
                non_active = InterviewQuestionRecord(
                    **{**valid_record_kwargs(), "status": record_status}
                )
                with self.assertRaises(ValidationError):
                    QuestionRetrievalResult(
                        status="hit",
                        selected_question=RetrievedQuestion(
                            record=non_active,
                            score=0.91,
                            index_version="idx-v1",
                        ),
                        trace=trace,
                        as_of=date(2026, 8, 26),
                    )

        expired = InterviewQuestionRecord(
            **{**valid_record_kwargs(), "valid_until": date(2026, 8, 26)}
        )
        with self.assertRaises(ValidationError):
            QuestionRetrievalResult(
                status="hit",
                selected_question=RetrievedQuestion(
                    record=expired,
                    score=0.91,
                    index_version="idx-v1",
                ),
                trace=trace,
                as_of=date(2026, 8, 27),
            )

    def test_non_hit_statuses_cannot_claim_a_selected_record(self) -> None:
        record = InterviewQuestionRecord(**valid_record_kwargs())
        selected = RetrievedQuestion(record=record, score=0.91, index_version="idx-v1")

        for status in ("no_match", "unavailable", "index_mismatch"):
            with self.subTest(status=status):
                result = QuestionRetrievalResult(status=status)
                self.assertIsNone(result.selected_question)
                self.assertEqual(result.trace.status, status)

                with self.assertRaises(ValidationError):
                    QuestionRetrievalResult(
                        status=status,
                        selected_question=selected,
                        trace=QuestionRetrievalTrace(status=status),
                    )

    def test_hit_rejects_trace_score_mismatch(self) -> None:
        record = InterviewQuestionRecord(**valid_record_kwargs())
        selected = RetrievedQuestion(record=record, score=0.91, index_version="idx-v1")

        with self.assertRaises(ValidationError):
            QuestionRetrievalResult(
                status="hit",
                selected_question=selected,
                trace=QuestionRetrievalTrace(
                    status="hit",
                    question_id=record.question_id,
                    source_id=record.source_id,
                    score=0.90,
                    index_version="idx-v1",
                ),
                as_of=date(2026, 8, 26),
            )

    def test_hit_rejects_trace_index_version_mismatch(self) -> None:
        record = InterviewQuestionRecord(**valid_record_kwargs())
        selected = RetrievedQuestion(record=record, score=0.91, index_version="idx-v1")

        with self.assertRaises(ValidationError):
            QuestionRetrievalResult(
                status="hit",
                selected_question=selected,
                trace=QuestionRetrievalTrace(
                    status="hit",
                    question_id=record.question_id,
                    source_id=record.source_id,
                    score=0.91,
                    index_version="idx-v2",
                ),
                as_of=date(2026, 8, 26),
            )

    def test_hit_rejects_trace_with_missing_id(self) -> None:
        record = InterviewQuestionRecord(**valid_record_kwargs())

        with self.assertRaises(ValidationError):
            QuestionRetrievalTrace(
                status="hit",
                source_id=record.source_id,
                score=0.91,
                index_version="idx-v1",
            )

    def test_hit_rejects_trace_id_mismatch(self) -> None:
        record = InterviewQuestionRecord(**valid_record_kwargs())
        selected = RetrievedQuestion(record=record, score=0.91, index_version="idx-v1")

        for question_id, source_id in (
            ("q_agent_other", record.source_id),
            (record.question_id, "src_public_other"),
        ):
            with self.subTest(question_id=question_id, source_id=source_id):
                with self.assertRaises(ValidationError):
                    QuestionRetrievalResult(
                        status="hit",
                        selected_question=selected,
                        trace=QuestionRetrievalTrace(
                            status="hit",
                            question_id=question_id,
                            source_id=source_id,
                            score=0.91,
                            index_version="idx-v1",
                        ),
                        as_of=date(2026, 8, 26),
                    )

    def test_trace_rejects_selected_ids_for_a_non_hit(self) -> None:
        with self.assertRaises(ValidationError):
            QuestionRetrievalTrace(
                status="no_match",
                question_id="q_agent_001",
                source_id="src_public_001",
            )


if __name__ == "__main__":
    unittest.main()
