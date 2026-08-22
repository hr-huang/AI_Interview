from __future__ import annotations

import unittest

from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.calibration.schemas import ReportCalibrationRun
from profile_agent.calibration.report_runner import run_report_calibration_case
from profile_agent.schemas.report_schema import (
    RequirementScoringBinding,
    RubricMatch,
    RubricMatchBatch,
    RubricQuality,
    ScoringBlueprint,
)
from profile_agent.services.report_writer_service import fallback_report_narrative


def _blueprint_for(case) -> ScoringBlueprint:
    return ScoringBlueprint(
        role_family="ai_application_engineering",
        role_profile_version="2026-H2",
        bindings=[
            RequirementScoringBinding(
                requirement_id=requirement.id,
                primary_dimension_id=f"role_dim_{requirement.id.rsplit('_', 1)[1]}",
                weight_within_dimension=1.0,
                rubric_id=f"role_dim_{requirement.id.rsplit('_', 1)[1]}",
            )
            for target in case.plan.targets
            for requirement in target.evidence_requirements
        ],
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


def _matches_for(case) -> RubricMatchBatch:
    matches = []
    for evidence in case.evidences:
        requirement_id = evidence.requirement_ids[0]
        criterion_ids = case.expectation.required_rubric_hits.get(
            requirement_id,
            [],
        )
        matches.append(
            RubricMatch(
                evidence_id=evidence.id,
                requirement_id=requirement_id,
                **_match_fields(criterion_ids),
                quality=RubricQuality(
                    correctness="strong",
                    specificity="strong",
                    reasoning="strong",
                    tradeoff_awareness="strong",
                    transferability="unverified",
                ),
            )
        )
    return RubricMatchBatch(matches=matches)


class FakeSemanticServices:
    def __init__(self, case) -> None:
        self.calls: list[str] = []
        self.blueprint = _blueprint_for(case)
        self.rubric_matches = _matches_for(case)

    def blueprint_builder(self, plan, role_profile):
        self.calls.append("blueprint")
        return self.blueprint

    def rubric_matcher(self, plan, blueprint, role_profile, turns, evidences):
        self.calls.append("rubric")
        return self.rubric_matches

    def narrative_writer(self, snapshot, evidences, role_profile):
        self.calls.append("writer")
        return fallback_report_narrative(snapshot, evidences, role_profile)


class ReportCalibrationRunnerTest(unittest.TestCase):
    def test_runs_capture_stages_and_preserve_case_inputs(self) -> None:
        case = get_report_calibration_case("C04")
        before = case.model_dump()
        fakes = FakeSemanticServices(case)

        runs = run_report_calibration_case(
            case,
            runs=2,
            semantic_services=fakes,
        )

        self.assertEqual(len(runs), 2)
        self.assertTrue(all(isinstance(run, ReportCalibrationRun) for run in runs))
        self.assertEqual([run.case_id for run in runs], ["C04", "C04"])
        self.assertEqual([run.run_number for run in runs], [1, 2])
        self.assertEqual(fakes.calls, ["blueprint", "rubric", "writer"] * 2)
        self.assertEqual(
            [run.blueprint.model_dump() for run in runs],
            [fakes.blueprint.model_dump()] * 2,
        )
        self.assertEqual(
            [run.rubric_matches.model_dump() for run in runs],
            [fakes.rubric_matches.model_dump()] * 2,
        )
        self.assertTrue(all(run.assertions for run in runs))
        self.assertTrue(all(run.passed for run in runs))
        self.assertEqual(case.model_dump(), before)

    def test_rejects_non_positive_run_count(self) -> None:
        case = get_report_calibration_case("C04")

        with self.assertRaisesRegex(ValueError, "runs"):
            run_report_calibration_case(case, runs=0)


if __name__ == "__main__":
    unittest.main()
