from datetime import datetime, timezone

from profile_agent.schemas.report_schema import (
    AssessmentReport,
    CandidateOverview,
    EnterpriseAssessment,
    JobMatchResult,
    ReportNarrativeDraft,
    ScoreSnapshot,
)


def make_test_candidate_overview(
    target_role: str = "测试岗位",
    *,
    candidate_id: str = "test_candidate",
    interview_rounds: int = 0,
) -> CandidateOverview:
    return CandidateOverview(
        candidate_id=candidate_id,
        target_role=target_role,
        interview_rounds=interview_rounds,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def make_test_enterprise_assessment() -> EnterpriseAssessment:
    return EnterpriseAssessment(
        decision="INSUFFICIENT_EVIDENCE",
        decision_label="证据不足，暂缓岗位判断",
        provisional_score=None,
        confidence="low",
        decision_reasons=["测试报告未提供岗位证据。"],
        overall_assessment="测试报告仅用于图流程 schema 验证。",
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
        candidate_overview=make_test_candidate_overview(target_role),
        enterprise_assessment=make_test_enterprise_assessment(),
    )
