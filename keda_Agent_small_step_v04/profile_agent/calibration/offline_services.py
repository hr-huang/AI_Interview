"""Frozen semantic boundaries for zero-API assessment replay."""

from __future__ import annotations

from collections.abc import Callable

from profile_agent.calibration.schemas import ReportCalibrationCase
from profile_agent.schemas.report_schema import (
    RubricMatch,
    RubricMatchBatch,
    RubricQuality,
)
from profile_agent.services.report_writer_service import (
    fallback_report_narrative,
)


def _match_fields(criterion_ids: list[str]) -> dict[str, list[str]]:
    fields = {
        "matched_minimum_criteria": [],
        "matched_excellence_signals": [],
        "matched_critical_errors": [],
        "accepted_alternative_ids": [],
    }
    for criterion_id in criterion_ids:
        if "_min_" in criterion_id:
            fields["matched_minimum_criteria"].append(criterion_id)
        elif "_exc_" in criterion_id:
            fields["matched_excellence_signals"].append(criterion_id)
        elif "_err_" in criterion_id:
            fields["matched_critical_errors"].append(criterion_id)
        elif "_alt_" in criterion_id:
            fields["accepted_alternative_ids"].append(criterion_id)
    return fields


def _offline_matches(case: ReportCalibrationCase) -> RubricMatchBatch:
    turn_by_id = {turn.id: turn for turn in case.turns}
    matches: list[RubricMatch] = []
    for evidence in case.evidences:
        turn = turn_by_id[evidence.turn_id]
        for requirement_id in evidence.requirement_ids:
            criterion_ids = case.expectation.required_rubric_hits.get(
                requirement_id,
                [],
            )
            supporting = evidence.polarity == "supporting"
            matches.append(
                RubricMatch(
                    evidence_id=evidence.id,
                    requirement_id=requirement_id,
                    **_match_fields(criterion_ids),
                    quality=RubricQuality(
                        correctness="strong" if supporting else "unverified",
                        specificity="strong" if supporting else "unverified",
                        reasoning="strong" if supporting else "unverified",
                        tradeoff_awareness=(
                            "strong" if supporting else "unverified"
                        ),
                        transferability=(
                            "strong"
                            if supporting and turn.question_mode == "scenario"
                            else "unverified"
                        ),
                    ),
                )
            )
    return RubricMatchBatch(matches=matches)


def build_offline_semantic_services(
    case: ReportCalibrationCase,
) -> dict[str, Callable]:
    """Return only semantic substitutes; Blueprint and scoring stay production."""

    def rubric_matcher(plan, blueprint, role_profile, turns, evidences):
        return _offline_matches(case)

    return {
        "rubric_matcher": rubric_matcher,
        "narrative_writer": fallback_report_narrative,
    }


__all__ = ["build_offline_semantic_services"]
