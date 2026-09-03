"""Build a private, replay-friendly artifact from one completed interview.

The artifact is for competition evidence collection and video rehearsal.  It
preserves the questions and answers that actually exist in the LangGraph
checkpoint; it never fabricates candidate answers or a per-turn gap history
that the runtime does not persist.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from profile_agent.schemas.report_schema import AssessmentReport
from profile_agent.schemas.runtime_schema import (
    Evidence,
    InterviewRuntimeState,
    InterviewTurn,
)
from profile_agent.web.schemas import AssessmentRecord, AssessmentStatus


_ARTIFACT_VERSION = 1


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _turn_payload(turn: InterviewTurn, evidences: list[Evidence]) -> dict[str, Any]:
    payload = turn.model_dump(mode="json")
    # These fields are intentionally excluded from ordinary/public Pydantic
    # dumps.  Competition artifacts are local/private audit material, so keep
    # the exact checkpoint provenance explicitly.
    payload["retrieval_trace"] = _json_value(turn.retrieval_trace)
    payload["question_provenance"] = _json_value(turn.question_provenance)
    payload["evidences"] = [
        evidence.model_dump(mode="json")
        for evidence in evidences
        if evidence.turn_id == turn.id
    ]
    return payload


def build_competition_session_artifact(
    record: AssessmentRecord | Mapping[str, Any],
    checkpoint_values: Mapping[str, Any],
    *,
    include_inputs: bool = False,
    exported_at: datetime | None = None,
) -> dict[str, Any]:
    """Build one completed-session artifact without mutating application data."""

    normalized_record = AssessmentRecord.model_validate(record)
    if normalized_record.status is not AssessmentStatus.COMPLETE:
        raise ValueError("只允许导出已经 COMPLETE 的比赛会话")
    if normalized_record.final_plan is None:
        raise ValueError("评估没有冻结后的 InterviewPlan")
    if normalized_record.scoring_blueprint is None:
        raise ValueError("评估没有 ScoringBlueprint")
    if normalized_record.report is None:
        raise ValueError("评估没有最终报告")

    turns = [
        InterviewTurn.model_validate(item)
        for item in list(checkpoint_values.get("interview_turns") or [])
    ]
    evidences = [
        Evidence.model_validate(item)
        for item in list(checkpoint_values.get("evidences") or [])
    ]
    runtime_value = checkpoint_values.get("runtime_state")
    if runtime_value is None:
        raise ValueError("checkpoint 缺少 InterviewRuntimeState")
    runtime = InterviewRuntimeState.model_validate(runtime_value)
    if not runtime.stop_requested:
        raise ValueError("面试运行状态尚未结束")
    if not turns:
        raise ValueError("checkpoint 没有 InterviewTurn")

    report = AssessmentReport.model_validate(normalized_record.report)
    export_time = exported_at or datetime.now(timezone.utc)

    inputs: dict[str, Any]
    if include_inputs:
        inputs = {
            "included": True,
            "jd_text": normalized_record.jd_text,
            "resume_text": normalized_record.resume_text,
        }
    else:
        inputs = {
            "included": False,
            "note": "默认不导出 JD / Resume 原文；需要私下归档时显式使用 --include-inputs。",
        }

    return {
        "artifact_type": "competition_golden_session",
        "artifact_version": _ARTIFACT_VERSION,
        "recording_status": "real_runtime_export",
        "assessment_id": normalized_record.id,
        "exported_at": export_time.isoformat(),
        "target_role": normalized_record.target_role,
        "role_profile_version": report.score_snapshot.role_profile_version,
        "scoring_engine_version": report.score_snapshot.scoring_engine_version,
        "inputs": inputs,
        "final_plan": normalized_record.final_plan,
        "scoring_blueprint": normalized_record.scoring_blueprint,
        "turns": [
            _turn_payload(turn, evidences)
            for turn in sorted(turns, key=lambda item: item.sequence_number)
        ],
        "final_requirement_progress": {
            requirement_id: progress.model_dump(mode="json")
            for requirement_id, progress in runtime.requirement_progress.items()
        },
        "stop_reason": runtime.stop_reason,
        "report": report.model_dump(mode="json"),
        "limitations": [
            "该文件只记录真实 checkpoint 中已经存在的问题、回答和 provenance。",
            "当前运行时只持久化每个 Requirement 的最新 gap tags，不保存每一轮历史 gap 快照；不要从本文件反推不存在的逐轮 gap 历史。",
            "如果用于演示视频，问题自然语言再次实时生成时可能变化；应以 Requirement、QuestionMode、Evidence 和 provenance 的语义路径一致为准。",
        ],
    }


__all__ = ["build_competition_session_artifact"]
