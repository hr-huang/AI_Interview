"""动态面试运行状态。

这里只保存 Supervisor 决策所需的最小进度，不复制 Plan、Turn 或 Evidence 全文。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from profile_agent.schemas.interview_schema import QuestionMode
from profile_agent.schemas.question_rag_schema import QuestionRetrievalTrace


RequirementStatus = Literal[
    "not_started",
    "in_progress",
    "sufficient",
    "contradictory",
    "skipped",
]


class InterviewTurn(BaseModel):
    id: str
    sequence_number: int = Field(ge=1)
    target_id: str
    primary_requirement_id: str
    question_mode: QuestionMode
    question: str
    answer: str | None = None
    asked_at: datetime
    answered_at: datetime | None = None
    # Retrieval provenance is checkpoint-private.  Candidate-facing adapters
    # project the turn down to id/question/answer and never serialize this
    # field.
    retrieval_trace: QuestionRetrievalTrace | None = None


class Evidence(BaseModel):
    id: str
    turn_id: str
    requirement_ids: list[str] = Field(min_length=1)
    related_claim_ids: list[str] = Field(default_factory=list)
    polarity: Literal["supporting", "contradicting"]
    strength: Literal["weak", "medium", "strong"]
    observation: str
    source_excerpt: str


class EvidenceDraft(BaseModel):
    requirement_ids: list[str] = Field(min_length=1)
    related_claim_ids: list[str] = Field(default_factory=list)
    polarity: Literal["supporting", "contradicting"]
    strength: Literal["weak", "medium", "strong"]
    observation: str
    source_excerpt: str


class RequirementAssessment(BaseModel):
    requirement_id: str
    recommended_status: Literal[
        "in_progress",
        "sufficient",
        "contradictory",
    ]
    rationale: str


class TurnAssessment(BaseModel):
    answer_relevance: Literal["low", "medium", "high"]
    evidence_drafts: list[EvidenceDraft] = Field(default_factory=list)
    requirement_assessments: list[RequirementAssessment] = Field(
        default_factory=list
    )


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
    def validate_runtime_invariants(self) -> "InterviewRuntimeState":
        for key, progress in self.requirement_progress.items():
            if key != progress.requirement_id:
                raise ValueError(
                    "requirement_progress key 与 requirement_id 不一致: "
                    f"{key} != {progress.requirement_id}"
                )

        if self.stop_requested and not (self.stop_reason or "").strip():
            raise ValueError("stop_requested=True 时必须提供 stop_reason")

        if not self.stop_requested and self.stop_reason is not None:
            raise ValueError("stop_requested=False 时 stop_reason 必须为 None")

        return self


class AnswerProcessingResult(BaseModel):
    new_evidences: list[Evidence]
    runtime_state: InterviewRuntimeState
