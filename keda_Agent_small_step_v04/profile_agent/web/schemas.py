from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AssessmentStatus(StrEnum):
    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    PLAN_REVIEW = "PLAN_REVIEW"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    REPORTING = "REPORTING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


_ALLOWED_TRANSITIONS: dict[AssessmentStatus, set[AssessmentStatus]] = {
    AssessmentStatus.DRAFT: {AssessmentStatus.ANALYZING},
    AssessmentStatus.ANALYZING: {
        AssessmentStatus.PLAN_REVIEW,
        AssessmentStatus.FAILED,
    },
    AssessmentStatus.PLAN_REVIEW: {
        AssessmentStatus.READY,
        AssessmentStatus.FAILED,
    },
    AssessmentStatus.READY: {AssessmentStatus.IN_PROGRESS},
    AssessmentStatus.IN_PROGRESS: {
        AssessmentStatus.REPORTING,
        AssessmentStatus.FAILED,
    },
    AssessmentStatus.REPORTING: {
        AssessmentStatus.COMPLETE,
        AssessmentStatus.FAILED,
    },
    AssessmentStatus.COMPLETE: set(),
    AssessmentStatus.FAILED: {
        AssessmentStatus.ANALYZING,
        AssessmentStatus.PLAN_REVIEW,
        AssessmentStatus.IN_PROGRESS,
        AssessmentStatus.REPORTING,
    },
}


class AssessmentRecord(BaseModel):
    id: str
    status: AssessmentStatus
    target_role: str
    jd_text: str
    resume_text: str
    pre_interview_state: dict[str, Any] | None = None
    original_plan: dict[str, Any] | None = None
    final_plan: dict[str, Any] | None = None
    scoring_blueprint: dict[str, Any] | None = None
    report: dict[str, Any] | None = None
    candidate_token_hash: str | None = None
    failed_stage: str | None = None
    error_message: str | None = None
    retryable: bool = False
    created_at: datetime
    updated_at: datetime
    version: int = Field(default=1, ge=1)

    @classmethod
    def new(
        cls,
        *,
        assessment_id: str,
        target_role: str,
        jd_text: str,
        resume_text: str,
    ) -> AssessmentRecord:
        now = datetime.now(timezone.utc)
        return cls(
            id=assessment_id,
            status=AssessmentStatus.DRAFT,
            target_role=target_role.strip(),
            jd_text=jd_text.strip(),
            resume_text=resume_text.strip(),
            created_at=now,
            updated_at=now,
        )

    def transition_to(self, status: AssessmentStatus) -> AssessmentRecord:
        if status not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"非法评估状态转换: {self.status} -> {status}")
        return self.model_copy(
            update={
                "status": status,
                "updated_at": datetime.now(timezone.utc),
                "version": self.version + 1,
            }
        )


def transition_assessment(
    record: AssessmentRecord,
    target_status: AssessmentStatus,
) -> AssessmentRecord:
    return record.transition_to(target_status)
