"""Offline report data used by the read-only demo endpoint."""

from __future__ import annotations

from profile_agent.calibration.report_cases import (
    build_public_student_showcase_case,
    get_report_calibration_case,
)
from profile_agent.calibration.offline_runner import run_offline_calibration_case
from profile_agent.schemas.report_schema import CandidateOverview
from profile_agent.services.enterprise_report_service import (
    validate_enterprise_assessment,
)
from profile_agent.services.role_profile_service import load_role_profile
from profile_agent.web.report_view import ReportViewModel, build_report_view


_DEMO_CASES = {
    "C01": {
        "variant": "showcase",
        "title": "应届候选人完整演示",
        "description": (
            "一名应届候选人使用课程、竞赛与实习项目回答动态问题，"
            "展示已证明能力、部分证据和待复试验证项。"
        ),
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

    case = (
        build_public_student_showcase_case()
        if case_id == "C01"
        else get_report_calibration_case(case_id)
    )
    run = run_offline_calibration_case(case)
    if not run.passed:
        failures = [item.message for item in run.assertions if not item.passed]
        raise RuntimeError(
            f"演示案例 {case.id} 未通过确定性校准: {'; '.join(failures)}"
        )
    validate_enterprise_assessment(
        run.report.enterprise_assessment,
        run.report.score_snapshot,
        case.turns,
    )
    report = run.report
    if case.id == "DEMO_STUDENT":
        report = report.model_copy(
            update={
                "candidate_overview": CandidateOverview(
                    candidate_id="DEMO_STUDENT",
                    candidate_name=None,
                    target_role=case.target_role,
                    education_summary="匿名应届候选人",
                    experience_summary="课程、竞赛与实习项目",
                    jd_focus=[
                        "Agent 编排与工具边界",
                        "业务任务建模与验收",
                        "AI 协作交付与安全治理",
                    ],
                    interview_rounds=len(case.turns),
                    generated_at=run.report.candidate_overview.generated_at,
                )
            }
        )
    return build_report_view(
        report,
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
