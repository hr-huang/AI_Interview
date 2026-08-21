import unittest

from profile_agent.schemas.claim_schema import ClaimItem, ClaimRegistry
from profile_agent.schemas.report_schema import ClaimVerification
from profile_agent.schemas.runtime_schema import Evidence
from profile_agent.services.claim_verification_service import (
    ClaimVerificationError,
    aggregate_claim_verifications,
)


def make_claim(claim_id: str) -> ClaimItem:
    return ClaimItem(
        id=claim_id,
        text=f"声明 {claim_id}",
        source_section="project",
        claim_type="experience",
    )


def make_registry(*claim_ids: str) -> ClaimRegistry:
    return ClaimRegistry(claims=[make_claim(claim_id) for claim_id in claim_ids])


def make_evidence(
    evidence_id: str,
    *,
    claim_ids: list[str],
    polarity: str = "supporting",
    strength: str = "strong",
) -> Evidence:
    return Evidence(
        id=evidence_id,
        turn_id=f"turn_{evidence_id}",
        requirement_ids=["req_01"],
        related_claim_ids=claim_ids,
        polarity=polarity,
        strength=strength,
        observation=f"观察 {evidence_id}",
        source_excerpt=f"原文 {evidence_id}",
    )


class ClaimVerificationServiceTest(unittest.TestCase):
    def test_strong_support_without_conflict_is_supported(self) -> None:
        result = aggregate_claim_verifications(
            make_registry("claim_01"),
            [make_evidence("ev_01", claim_ids=["claim_01"])],
        )

        self.assertEqual(
            result,
            [
                ClaimVerification(
                    claim_id="claim_01",
                    status="supported",
                    supporting_evidence_ids=["ev_01"],
                    contradicting_evidence_ids=[],
                    explanation="存在强支持证据（支持证据 1 条，矛盾证据 0 条）。",
                )
            ],
        )

    def test_medium_support_is_partially_supported(self) -> None:
        result = aggregate_claim_verifications(
            make_registry("claim_01"),
            [
                make_evidence(
                    "ev_01",
                    claim_ids=["claim_01"],
                    strength="medium",
                )
            ],
        )

        self.assertEqual(result[0].status, "partially_supported")

    def test_any_explicit_contradiction_is_contradictory(self) -> None:
        result = aggregate_claim_verifications(
            make_registry("claim_01"),
            [
                make_evidence("ev_support", claim_ids=["claim_01"]),
                make_evidence(
                    "ev_contradict",
                    claim_ids=["claim_01"],
                    polarity="contradicting",
                    strength="weak",
                ),
            ],
        )

        self.assertEqual(result[0].status, "contradictory")
        self.assertEqual(result[0].supporting_evidence_ids, ["ev_support"])
        self.assertEqual(
            result[0].contradicting_evidence_ids,
            ["ev_contradict"],
        )

    def test_weak_support_only_is_insufficient(self) -> None:
        result = aggregate_claim_verifications(
            make_registry("claim_01"),
            [
                make_evidence(
                    "ev_01",
                    claim_ids=["claim_01"],
                    strength="weak",
                )
            ],
        )

        self.assertEqual(result[0].status, "insufficient")

    def test_no_related_evidence_is_unverified(self) -> None:
        result = aggregate_claim_verifications(
            make_registry("claim_01"),
            [make_evidence("ev_01", claim_ids=[])],
        )

        self.assertEqual(result[0].status, "unverified")
        self.assertEqual(result[0].supporting_evidence_ids, [])
        self.assertEqual(result[0].contradicting_evidence_ids, [])

    def test_unknown_claim_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ClaimVerificationError, "claim_missing"):
            aggregate_claim_verifications(
                make_registry("claim_01"),
                [make_evidence("ev_01", claim_ids=["claim_missing"])],
            )

    def test_registry_order_is_preserved(self) -> None:
        result = aggregate_claim_verifications(
            make_registry("claim_02", "claim_01"),
            [
                make_evidence("ev_01", claim_ids=["claim_01"]),
                make_evidence("ev_02", claim_ids=["claim_02"]),
            ],
        )

        self.assertEqual(
            [verification.claim_id for verification in result],
            ["claim_02", "claim_01"],
        )


if __name__ == "__main__":
    unittest.main()
