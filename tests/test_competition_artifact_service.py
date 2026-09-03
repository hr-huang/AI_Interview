from __future__ import annotations

from datetime import datetime, timezone

import pytest

from profile_agent.schemas.report_schema import (
    AssessmentReport,
    CandidateOverview,
    EnterpriseAssessment,
    JobMatchResult,
    ReportNarrativeDraft,
    ScoreSnapshot,
)
from profile_agent.schemas.runtime_schema import (
    Evidence,
    InterviewRuntimeState,
    InterviewTurn,
    RequirementProgress,
)
from profile_agent.schemas.scenario_rag_schema import QuestionProvenance
from profile_agent.web.competition_artifact import (
    build_competition_session_artifact,
)
from profile_agent.web.schemas import AssessmentRecord, AssessmentStatus


NOW = datetime(2026, 9, 4, 1, 0, tzinfo=timezone.utc)


def _report() -> AssessmentReport:
    return AssessmentReport(
        target_role="AI Agent应用工程师（校招/初级）",
        score_snapshot=ScoreSnapshot(
            role_family="ai_application_engineering",
            role_profile_version="2026-H2",
            scoring_engine_version="test-v1",
            job_match=JobMatchResult(
                published=False,
                coverage=0.4,
                confidence="low",
            ),
        ),
        narrative=ReportNarrativeDraft(executive_summary="当前证据仍需补充。"),
        candidate_overview=CandidateOverview(
            candidate_id="candidate_test",
            target_role="AI Agent应用工程师（校招/初级）",
            interview_rounds=1,
            generated_at=NOW,
        ),
        enterprise_assessment=EnterpriseAssessment(
            decision="INSUFFICIENT_EVIDENCE",
            decision_label="证据不足，继续人工核验",
            confidence="low",
            decision_reasons=["当前覆盖不足。"],
            overall_assessment="本次面试留下了可回溯证据，但仍有未验证项。",
        ),
    )


def _record() -> AssessmentRecord:
    return AssessmentRecord(
        id="ast_competition_001",
        status=AssessmentStatus.COMPLETE,
        target_role="AI Agent应用工程师（校招/初级）",
        jd_text="真实 JD 原文（测试）",
        resume_text="真实 Resume 原文（测试）",
        interview_duration_minutes=30,
        final_plan={"duration_minutes": 30, "targets": []},
        scoring_blueprint={
            "role_family": "ai_application_engineering",
            "role_profile_version": "2026-H2",
            "bindings": [],
        },
        report=_report().model_dump(mode="json"),
        created_at=NOW,
        updated_at=NOW,
    )


def _checkpoint() -> dict[str, object]:
    provenance = QuestionProvenance(
        target_requirement_id="req_knowledge",
        primary_dimension_id="role_dim_03",
        retrieval_unit_id="enterprise_knowledge_assistant::knowledge_rag_memory",
        scenario_id="enterprise_knowledge_assistant",
        module_id="knowledge_rag_memory",
        selected_constraint_id="knowledge_policy_version_stale",
        revealed_constraint_ids=["knowledge_policy_version_stale"],
        retrieval_status="hit",
    )
    turn = InterviewTurn(
        id="turn_001",
        sequence_number=1,
        target_id="target_knowledge",
        primary_requirement_id="req_knowledge",
        question_mode="follow_up",
        question="制度已经更新但旧版本仍存在时，你会如何保证答案可追溯？",
        answer="我会记录版本并在答案中保留来源引用。",
        asked_at=NOW,
        answered_at=NOW,
        question_provenance=provenance,
    )
    evidence = Evidence(
        id="ev_001",
        turn_id=turn.id,
        requirement_ids=["req_knowledge"],
        polarity="supporting",
        strength="medium",
        observation="回答提到了版本与引用。",
        source_excerpt="记录版本并在答案中保留来源引用",
    )
    runtime = InterviewRuntimeState(
        question_count=1,
        started_at=NOW,
        current_target_id="target_knowledge",
        requirement_progress={
            "req_knowledge": RequirementProgress(
                requirement_id="req_knowledge",
                status="sufficient",
                attempt_count=1,
                supporting_evidence_ids=["ev_001"],
                latest_gap_tags=[],
            )
        },
        visited_target_ids=["target_knowledge"],
        stop_requested=True,
        stop_reason="all must_cover requirements are sufficient",
    )
    return {
        "interview_turns": [turn],
        "evidences": [evidence],
        "runtime_state": runtime,
    }


def test_artifact_preserves_real_question_answer_and_private_scenario_provenance() -> None:
    artifact = build_competition_session_artifact(
        _record(),
        _checkpoint(),
        exported_at=NOW,
    )

    assert artifact["recording_status"] == "real_runtime_export"
    assert artifact["inputs"]["included"] is False
    assert "jd_text" not in artifact["inputs"]
    assert "resume_text" not in artifact["inputs"]
    turn = artifact["turns"][0]
    assert turn["question"].startswith("制度已经更新")
    assert turn["answer"] == "我会记录版本并在答案中保留来源引用。"
    assert turn["question_provenance"]["module_id"] == "knowledge_rag_memory"
    assert (
        turn["question_provenance"]["selected_constraint_id"]
        == "knowledge_policy_version_stale"
    )
    assert turn["evidences"][0]["id"] == "ev_001"
    assert artifact["final_requirement_progress"]["req_knowledge"]["status"] == "sufficient"


def test_artifact_includes_inputs_only_when_explicitly_requested() -> None:
    artifact = build_competition_session_artifact(
        _record(),
        _checkpoint(),
        include_inputs=True,
        exported_at=NOW,
    )

    assert artifact["inputs"] == {
        "included": True,
        "jd_text": "真实 JD 原文（测试）",
        "resume_text": "真实 Resume 原文（测试）",
    }


def test_artifact_rejects_unfinished_assessment() -> None:
    record = _record().model_copy(update={"status": AssessmentStatus.IN_PROGRESS})

    with pytest.raises(ValueError, match="COMPLETE"):
        build_competition_session_artifact(record, _checkpoint(), exported_at=NOW)
