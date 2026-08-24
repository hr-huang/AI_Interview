"""Offline report data used by the read-only demo endpoint."""

from __future__ import annotations

from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.calibration.offline_runner import run_offline_calibration_case
from profile_agent.services.role_profile_service import load_role_profile
from profile_agent.web.report_view import ReportViewModel, build_report_view


def build_demo_report_view() -> ReportViewModel:
    """Build the frozen C03 report without crossing a provider boundary."""

    case = get_report_calibration_case("C03")
    run = run_offline_calibration_case(case)
    return build_report_view(
        run.report,
        case.plan,
        case.turns,
        case.evidences,
        load_role_profile("ai_application_engineering", "2026-H2"),
        demo=True,
    )


__all__ = ["build_demo_report_view"]
