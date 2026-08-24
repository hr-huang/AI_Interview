"""Offline report data used by the read-only demo endpoint."""

from __future__ import annotations

from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.calibration.offline_runner import run_offline_calibration_case
from profile_agent.services.role_profile_service import load_role_profile
from profile_agent.web.report_view import ReportViewModel, build_report_view


_DEMO_CASES = {
    "C01": {
        "variant": "showcase",
        "title": "企业完整演示",
        "description": "六项核心能力均有面试证据，展示从动态追问到岗位匹配结论的完整链路。",
    },
    "C03": {
        "variant": "boundary",
        "title": "评分边界案例",
        "description": "同一能力在项目深挖与迁移场景出现分歧，用于说明限制性证据如何影响结论。",
    },
}


def build_demo_report_view(case_id: str = "C01") -> ReportViewModel:
    """Build a frozen demo report without crossing a provider boundary.

    C01 is the public showcase. C03 remains available as a deliberately
    imperfect boundary case for explaining conservative scoring.
    """

    metadata = _DEMO_CASES.get(case_id)
    if metadata is None:
        raise ValueError(f"未知的演示案例: {case_id}")

    case = get_report_calibration_case(case_id)
    run = run_offline_calibration_case(case)
    return build_report_view(
        run.report,
        case.plan,
        case.turns,
        case.evidences,
        load_role_profile("ai_application_engineering", "2026-H2"),
        demo=True,
        demo_variant=metadata["variant"],
        demo_case_title=metadata["title"],
        demo_case_description=metadata["description"],
    )


__all__ = ["build_demo_report_view"]
