"""动态面试运行状态。

这里只保存 Supervisor 决策所需的最小进度，不复制 Plan、Turn 或 Evidence 全文。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


RequirementStatus = Literal[
    "not_started",
    "in_progress",
    "sufficient",
    "contradictory",
    "skipped",
]


class RequirementProgress(BaseModel):
    requirement_id: str
    status: RequirementStatus = "not_started"
    attempt_count: int = Field(default=0, ge=0)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)


class InterviewRuntimeState(BaseModel):
    question_count: int = Field(default=0, ge=0)
    started_at: datetime
    current_target_id: str | None = None
    requirement_progress: dict[str, RequirementProgress] = Field(
        default_factory=dict
    )
    visited_target_ids: list[str] = Field(default_factory=list)
    stop_requested: bool = False
    stop_reason: str | None = None

    @model_validator(mode="after")
    def validate_stop_fields(self) -> "InterviewRuntimeState":
        if self.stop_requested and not (self.stop_reason or "").strip():
            raise ValueError("stop_requested=True 时必须提供 stop_reason")

        if not self.stop_requested and self.stop_reason is not None:
            raise ValueError("stop_requested=False 时 stop_reason 必须为 None")

        return self
