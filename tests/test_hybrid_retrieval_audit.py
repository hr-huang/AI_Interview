from __future__ import annotations

import unittest

from run_hybrid_retrieval_audit import summarize_rank_trace


class HybridRetrievalAuditTests(unittest.TestCase):
    def test_summary_ignores_null_reasons_without_sorting_failure(self) -> None:
        summary = summarize_rank_trace(
            [
                {"question_id": "q004", "rejection_reason": None, "truncation_reason": None},
                {"question_id": "q003", "rejection_reason": "not_top_rank", "truncation_reason": None},
                {"question_id": "q005", "rejection_reason": "below_threshold", "truncation_reason": "not_in_top20"},
            ]
        )

        self.assertEqual(summary["candidate_count"], 3)
        self.assertEqual(
            summary["rejection_reasons"], ["below_threshold", "not_top_rank"]
        )
        self.assertEqual(summary["truncation_reasons"], ["not_in_top20"])

    def test_summary_does_not_echo_untrusted_trace_fields(self) -> None:
        summary = summarize_rank_trace(
            [{"question_id": "q1", "source_id": "secret", "rerank_score": 0.5}]
        )

        self.assertEqual(set(summary), {"candidate_count", "rejection_reasons", "truncation_reasons"})


if __name__ == "__main__":
    unittest.main()
