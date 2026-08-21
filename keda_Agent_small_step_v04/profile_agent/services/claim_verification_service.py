"""Aggregate Claim evidence without an LLM.

Claim verification is a report-stage reduction over the immutable ClaimRegistry
and Evidence facts.  All claim references are checked before any result is
returned so an unknown reference cannot silently become an unverified claim.
"""

from __future__ import annotations

from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.report_schema import ClaimVerification
from profile_agent.schemas.runtime_schema import Evidence


class ClaimVerificationError(ValueError):
    """Raised when Evidence cannot be reconciled with the ClaimRegistry."""


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _explanation(
    status: str,
    supporting_count: int,
    contradicting_count: int,
) -> str:
    counts = (
        f"支持证据 {supporting_count} 条，"
        f"矛盾证据 {contradicting_count} 条"
    )
    if status == "supported":
        return f"存在强支持证据（{counts}）。"
    if status == "partially_supported":
        return f"存在中等支持证据，但尚未达到强支持（{counts}）。"
    if status == "insufficient":
        return f"仅有弱支持证据，证据不足（{counts}）。"
    if status == "contradictory":
        return f"存在相互矛盾的证据（{counts}）。"
    return f"未发现相关证据（{counts}）。"


def aggregate_claim_verifications(
    claim_registry: ClaimRegistry,
    evidence: list[Evidence],
) -> list[ClaimVerification]:
    """Return one deterministic verification for each registered Claim.

    Evidence is grouped by its related claim IDs in input order, while output
    follows the registry order.  Contradicting Evidence always has precedence
    over supporting Evidence; otherwise support strength determines the status.
    """

    known_claim_ids = {claim.id for claim in claim_registry.claims}
    for item in evidence:
        unknown_claim_ids = sorted(
            set(item.related_claim_ids) - known_claim_ids
        )
        if unknown_claim_ids:
            raise ClaimVerificationError(
                f"Evidence {item.id} 引用了不存在的 Claim ID: "
                + ", ".join(unknown_claim_ids)
            )

    evidence_by_claim: dict[str, dict[str, list[str]]] = {
        claim.id: {"supporting": [], "contradicting": []}
        for claim in claim_registry.claims
    }
    for item in evidence:
        for claim_id in dict.fromkeys(item.related_claim_ids):
            ids = evidence_by_claim[claim_id][item.polarity]
            _append_once(ids, item.id)

    results: list[ClaimVerification] = []
    for claim in claim_registry.claims:
        grouped = evidence_by_claim[claim.id]
        supporting_ids = grouped["supporting"]
        contradicting_ids = grouped["contradicting"]

        if not supporting_ids and not contradicting_ids:
            status = "unverified"
        elif contradicting_ids:
            status = "contradictory"
        else:
            supporting_strengths = {
                item.strength
                for item in evidence
                if item.id in supporting_ids
                and item.polarity == "supporting"
                and claim.id in item.related_claim_ids
            }
            if "strong" in supporting_strengths:
                status = "supported"
            elif "medium" in supporting_strengths:
                status = "partially_supported"
            else:
                status = "insufficient"

        results.append(
            ClaimVerification(
                claim_id=claim.id,
                status=status,
                supporting_evidence_ids=supporting_ids,
                contradicting_evidence_ids=contradicting_ids,
                explanation=_explanation(
                    status,
                    len(supporting_ids),
                    len(contradicting_ids),
                ),
            )
        )

    return results
