from profile_agent.schemas.report_schema import (
    AssessmentReport,
    JobMatchResult,
    ReportNarrativeDraft,
    ScoreSnapshot,
)


def make_test_report(target_role: str = "测试岗位") -> AssessmentReport:
    return AssessmentReport(
        target_role=target_role,
        score_snapshot=ScoreSnapshot(
            role_family="test_role",
            role_profile_version="test-version",
            scoring_engine_version="test-engine",
            job_match=JobMatchResult(
                published=False,
                coverage=0.0,
                confidence="low",
            ),
        ),
        narrative=ReportNarrativeDraft(executive_summary="测试报告摘要"),
    )
