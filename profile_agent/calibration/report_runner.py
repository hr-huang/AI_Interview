"""Run report calibration cases while capturing semantic stage outputs."""

from __future__ import annotations

from typing import Any

from profile_agent.calibration.report_assertions import evaluate_report_calibration
from profile_agent.calibration.schemas import (
    ReportCalibrationCase,
    ReportCalibrationRun,
)
from profile_agent.schemas.report_schema import RubricMatchBatch, ScoringBlueprint
from profile_agent.services.assessment_report_service import (
    DEFAULT_ROLE_FAMILY,
    DEFAULT_ROLE_PROFILE_VERSION,
    _resolve_service,
    generate_assessment_report,
)
from profile_agent.services.rubric_matcher_service import match_evidence_to_rubric
from profile_agent.services.scoring_blueprint_service import build_scoring_blueprint


def run_report_calibration_case(
    case: ReportCalibrationCase,
    *,
    runs: int = 1,
    semantic_services: object | None = None,
) -> list[ReportCalibrationRun]:
    """Run one frozen case repeatedly and capture its semantic outputs."""

    if runs <= 0:
        raise ValueError("runs 必须大于 0")

    blueprint_service = _resolve_service(
        semantic_services,
        None,
        "blueprint_builder",
        build_scoring_blueprint,
    )
    rubric_service = _resolve_service(
        semantic_services,
        None,
        "rubric_matcher",
        match_evidence_to_rubric,
    )

    results: list[ReportCalibrationRun] = []
    for run_number in range(1, runs + 1):
        captured_blueprint: ScoringBlueprint | None = None
        captured_rubric_matches: RubricMatchBatch | None = None

        def capture_blueprint(plan: Any, role_profile: Any) -> ScoringBlueprint:
            nonlocal captured_blueprint
            captured_blueprint = ScoringBlueprint.model_validate(
                blueprint_service(plan, role_profile)
            )
            return captured_blueprint

        def capture_rubric_matches(
            plan: Any,
            blueprint: Any,
            role_profile: Any,
            turns: Any,
            evidences: Any,
        ) -> RubricMatchBatch:
            nonlocal captured_rubric_matches
            captured_rubric_matches = RubricMatchBatch.model_validate(
                rubric_service(plan, blueprint, role_profile, turns, evidences)
            )
            return captured_rubric_matches

        report = generate_assessment_report(
            plan=case.plan,
            runtime_state=case.runtime_state,
            turns=case.turns,
            evidences=case.evidences,
            claim_registry=case.claim_registry,
            role_family=DEFAULT_ROLE_FAMILY,
            role_profile_version=DEFAULT_ROLE_PROFILE_VERSION,
            target_role=case.target_role,
            semantic_services=semantic_services,
            blueprint_builder=capture_blueprint,
            rubric_matcher=capture_rubric_matches,
        )

        if captured_blueprint is None or captured_rubric_matches is None:
            raise RuntimeError("报告流水线未返回 Blueprint 或 RubricMatchBatch")

        assertions = evaluate_report_calibration(
            case,
            captured_blueprint,
            captured_rubric_matches,
            report,
        )
        results.append(
            ReportCalibrationRun(
                case_id=case.id,
                run_number=run_number,
                blueprint=captured_blueprint,
                rubric_matches=captured_rubric_matches,
                report=report,
                assertions=assertions,
            )
        )

    return results


__all__ = ["run_report_calibration_case"]
