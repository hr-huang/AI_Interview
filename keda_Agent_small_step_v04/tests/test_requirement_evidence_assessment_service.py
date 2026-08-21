from __future__ import annotations

import unittest

from profile_agent.schemas.report_schema import (
    RequirementScoringBinding,
    RubricMatch,
    RubricMatchBatch,
    RubricQuality,
    ScoringBlueprint,
)
from profile_agent.schemas.runtime_schema import Evidence, InterviewTurn
from profile_agent.services.requirement_evidence_assessment_service import (
    RequirementEvidenceAssessmentError,
    build_requirement_evidence_assessments,
)
from tests.fixtures.report_golden_cases import (
    GoldenCase,
    make_conflicting_transfer_case,
    make_critical_safety_error_case,
    make_deep_but_non_exhaustive_case,
    make_keyword_only_case,
    make_unverified_case,
)


def _quality(
    *,
    correctness: str = "strong",
    specificity: str = "strong",
    reasoning: str = "strong",
    tradeoff_awareness: str = "strong",
    transferability: str = "unverified",
) -> RubricQuality:
    return RubricQuality(
        correctness=correctness,
        specificity=specificity,
        reasoning=reasoning,
        tradeoff_awareness=tradeoff_awareness,
        transferability=transferability,
    )


def _build(case: GoldenCase):
    return build_requirement_evidence_assessments(
        case.role_profile,
        case.blueprint,
        case.matches,
        case.evidences,
        case.turns,
    )


class RequirementEvidenceAssessmentServiceTest(unittest.TestCase):
    def test_every_assessment_follows_blueprint_requirement_order(self) -> None:
        case = make_deep_but_non_exhaustive_case()
        second_turn = case.turns[1].model_copy(
            update={"id": "turn_03", "primary_requirement_id": "req_02"}
        )
        second_evidence = case.evidences[1].model_copy(
            update={
                "id": "ev_03",
                "turn_id": "turn_03",
                "requirement_ids": ["req_02"],
            }
        )
        second_match = case.matches.matches[1].model_copy(
            update={"evidence_id": "ev_03", "requirement_id": "req_02"}
        )
        blueprint = ScoringBlueprint(
            role_family=case.blueprint.role_family,
            role_profile_version=case.blueprint.role_profile_version,
            bindings=[
                RequirementScoringBinding(
                    requirement_id="req_01",
                    primary_dimension_id="role_dim_01",
                    weight_within_dimension=0.5,
                    rubric_id="role_dim_01",
                ),
                RequirementScoringBinding(
                    requirement_id="req_02",
                    primary_dimension_id="role_dim_01",
                    weight_within_dimension=0.5,
                    rubric_id="role_dim_01",
                ),
            ],
        )
        assessments = build_requirement_evidence_assessments(
            case.role_profile,
            blueprint,
            RubricMatchBatch(
                matches=[second_match, case.matches.matches[0]]
            ),
            [second_evidence, case.evidences[0]],
            [second_turn, case.turns[0]],
        )

        self.assertEqual(
            [assessment.requirement_id for assessment in assessments],
            ["req_01", "req_02"],
        )

    def test_supporting_evidence_requires_validated_rubric_hit(self) -> None:
        case = make_deep_but_non_exhaustive_case()
        unmatched = case.evidences[0].model_copy(
            update={"id": "ev_unmatched", "source_excerpt": "没有命中 rubric。"}
        )
        assessment = _build(
            GoldenCase(
                case.role_profile,
                case.blueprint,
                case.matches,
                [*case.evidences, unmatched],
                case.turns,
            )
        )[0]

        self.assertNotIn("ev_unmatched", assessment.supporting_evidence_ids)

    def test_limiting_evidence_requires_explicit_negative_match(self) -> None:
        case = make_unverified_case()
        omitted = case.evidences[0].model_copy(
            update={"id": "ev_omission", "polarity": "contradicting"}
        )
        without_match = _build(
            GoldenCase(
                case.role_profile,
                case.blueprint,
                RubricMatchBatch(matches=[]),
                [omitted],
                case.turns,
            )
        )[0]
        with_match = _build(
            GoldenCase(
                case.role_profile,
                case.blueprint,
                RubricMatchBatch(
                    matches=[
                        RubricMatch(
                            evidence_id="ev_omission",
                            requirement_id="req_01",
                            matched_minimum_criteria=["min_01"],
                            quality=_quality(),
                        )
                    ]
                ),
                [omitted],
                case.turns,
            )
        )[0]

        self.assertEqual(without_match.limiting_evidence_ids, [])
        self.assertEqual(with_match.limiting_evidence_ids, ["ev_omission"])

    def test_unknown_evidence_or_rubric_id_is_rejected_again(self) -> None:
        case = make_deep_but_non_exhaustive_case()
        unknown_evidence = case.matches.matches[0].model_copy(
            update={"evidence_id": "ev_missing"}
        )
        unknown_rubric = case.matches.matches[0].model_copy(
            update={"matched_minimum_criteria": ["min_missing"]}
        )

        for matches in (
            RubricMatchBatch(matches=[unknown_evidence]),
            RubricMatchBatch(matches=[unknown_rubric]),
        ):
            with self.subTest(matches=matches):
                with self.assertRaises(RequirementEvidenceAssessmentError):
                    build_requirement_evidence_assessments(
                        case.role_profile,
                        case.blueprint,
                        matches,
                        case.evidences,
                        case.turns,
                    )

    def test_duplicate_evidence_content_does_not_raise_coverage_or_confidence(
        self,
    ) -> None:
        case = make_deep_but_non_exhaustive_case()
        baseline = _build(case)[0]
        duplicate_evidence = case.evidences[0].model_copy(update={"id": "ev_dup"})
        duplicate_match = case.matches.matches[0].model_copy(
            update={"evidence_id": "ev_dup"}
        )
        duplicate = _build(
            GoldenCase(
                case.role_profile,
                case.blueprint,
                RubricMatchBatch(matches=[*case.matches.matches, duplicate_match]),
                [*case.evidences, duplicate_evidence],
                case.turns,
            )
        )[0]

        self.assertEqual(duplicate.coverage, baseline.coverage)
        self.assertEqual(duplicate.confidence, baseline.confidence)

    def test_quality_uses_highest_verified_supporting_value_per_axis(self) -> None:
        case = make_deep_but_non_exhaustive_case()
        weak_match = case.matches.matches[0].model_copy(
            update={
                "quality": _quality(
                    correctness="weak",
                    specificity="weak",
                    reasoning="weak",
                    tradeoff_awareness="weak",
                )
            }
        )
        strong_match = case.matches.matches[1].model_copy(
            update={"quality": _quality()}
        )
        assessment = _build(
            GoldenCase(
                case.role_profile,
                case.blueprint,
                RubricMatchBatch(matches=[weak_match, strong_match]),
                case.evidences,
                case.turns,
            )
        )[0]

        self.assertEqual(assessment.quality.correctness, "strong")
        self.assertEqual(assessment.quality.reasoning, "strong")

    def test_conflict_preserves_both_sides_and_lowers_confidence(self) -> None:
        assessment = _build(make_conflicting_transfer_case())[0]

        self.assertEqual(assessment.supporting_evidence_ids, ["ev_project"])
        self.assertEqual(assessment.limiting_evidence_ids, ["ev_migration"])
        self.assertNotEqual(assessment.confidence, "high")

    def test_unverified_case_has_no_score_and_no_negative_reason(self) -> None:
        assessment = _build(make_unverified_case())[0]

        self.assertEqual(assessment.level, "UNVERIFIED")
        self.assertEqual(assessment.coverage, 0.0)
        self.assertEqual(
            [reason.reason_type for reason in assessment.assessment_reasons],
            ["unverified"],
        )
        self.assertNotIn("base_score", assessment.model_dump())
        self.assertNotIn("display_score", assessment.model_dump())

    def test_keyword_only_case_is_l1(self) -> None:
        assessment = _build(make_keyword_only_case())[0]

        self.assertEqual(assessment.level, "L1")
        self.assertLess(assessment.coverage, 1.0)

    def test_deep_non_exhaustive_case_is_l3(self) -> None:
        assessment = _build(make_deep_but_non_exhaustive_case())[0]

        self.assertEqual(assessment.level, "L3")
        self.assertEqual(assessment.quality.correctness, "strong")
        self.assertGreaterEqual(
            sum(
                value == "strong"
                for value in (
                    assessment.quality.specificity,
                    assessment.quality.reasoning,
                    assessment.quality.tradeoff_awareness,
                )
            ),
            2,
        )

    def test_l4_requires_independent_transfer_success(self) -> None:
        case = make_deep_but_non_exhaustive_case()
        same_mode_turn = case.turns[0].model_copy(update={"id": "turn_same"})
        same_mode_evidence = case.evidences[0].model_copy(update={"id": "ev_same"})
        same_mode_match = case.matches.matches[0].model_copy(
            update={"evidence_id": "ev_same", "quality": _quality(transferability="strong")}
        )
        same_mode_assessment = _build(
            GoldenCase(
                case.role_profile,
                case.blueprint,
                RubricMatchBatch(matches=[*case.matches.matches, same_mode_match]),
                [*case.evidences, same_mode_evidence],
                [*case.turns, same_mode_turn],
            )
        )[0]

        transfer_turn = case.turns[0].model_copy(
            update={"id": "turn_transfer", "question_mode": "scenario"}
        )
        transfer_evidence = case.evidences[0].model_copy(update={"id": "ev_transfer", "turn_id": "turn_transfer"})
        transfer_match = case.matches.matches[0].model_copy(
            update={"evidence_id": "ev_transfer", "quality": _quality(transferability="strong")}
        )
        transfer_assessment = _build(
            GoldenCase(
                case.role_profile,
                case.blueprint,
                RubricMatchBatch(matches=[*case.matches.matches, transfer_match]),
                [*case.evidences, transfer_evidence],
                [*case.turns, transfer_turn],
            )
        )[0]

        self.assertEqual(same_mode_assessment.level, "L3")
        self.assertEqual(transfer_assessment.level, "L4")
        self.assertEqual(transfer_assessment.transfer_evidence_ids, ["ev_transfer"])

    def test_medium_or_strong_unresolved_critical_error_is_l0(self) -> None:
        for strength in ("medium", "strong"):
            with self.subTest(strength=strength):
                case = make_critical_safety_error_case()
                evidence = case.evidences[0].model_copy(update={"strength": strength})
                assessment = _build(
                    GoldenCase(
                        case.role_profile,
                        case.blueprint,
                        case.matches,
                        [evidence],
                        case.turns,
                    )
                )[0]
                self.assertEqual(assessment.level, "L0")

    def test_weak_unresolved_critical_error_does_not_alone_trigger_l0(self) -> None:
        case = make_critical_safety_error_case()
        weak_evidence = case.evidences[0].model_copy(update={"strength": "weak"})
        assessment = _build(
            GoldenCase(
                case.role_profile,
                case.blueprint,
                case.matches,
                [weak_evidence],
                case.turns,
            )
        )[0]

        self.assertNotEqual(assessment.level, "L0")
        self.assertEqual(assessment.limiting_evidence_ids, ["ev_error"])

    def test_evidence_order_does_not_change_assessment_dump(self) -> None:
        case = make_deep_but_non_exhaustive_case()
        baseline = _build(case)[0].model_dump()
        reversed_case = GoldenCase(
            case.role_profile,
            case.blueprint,
            RubricMatchBatch(matches=list(reversed(case.matches.matches))),
            list(reversed(case.evidences)),
            list(reversed(case.turns)),
        )

        self.assertEqual(_build(reversed_case)[0].model_dump(), baseline)


if __name__ == "__main__":
    unittest.main()
