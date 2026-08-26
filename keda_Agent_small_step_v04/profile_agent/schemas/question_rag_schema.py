"""Strict, secret-free contracts for interview-question retrieval.

The question bank is intentionally represented separately from the retrieval
trace.  A record is the auditable source data, while a trace describes what a
runtime retrieval attempt selected (or why it could not select anything).
"""

from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any, Literal, Mapping

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from profile_agent.schemas.interview_schema import QuestionMode


QuestionDifficulty = Literal["foundation", "intermediate", "advanced"]
QuestionTrustLevel = Literal["high", "medium", "low"]
QuestionLifecycleStatus = Literal["active", "needs_review", "retired"]
QuestionStatus = QuestionLifecycleStatus
RetrievalStatus = Literal[
    "hit",
    "no_match",
    "unavailable",
    "index_mismatch",
]


def _require_non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


class InterviewQuestionRecord(BaseModel):
    """One reviewed interview question in the versioned question bank."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    question_text: str = Field(min_length=1)
    role: Literal["ai_agent_engineer"]
    role_version: str = Field(min_length=1)
    dimension_id: str = Field(min_length=1)
    skills: list[str] = Field(min_length=1)
    question_mode: QuestionMode
    difficulty: QuestionDifficulty
    expected_signals: list[str] = Field(min_length=1)
    critical_errors: list[str]
    follow_up_seeds: list[str]
    company_tags: list[str]
    source_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    published_at: date
    verified_at: date
    valid_until: date
    trust_level: QuestionTrustLevel
    status: QuestionLifecycleStatus
    version: int = Field(ge=1)
    content_hash: str = Field(min_length=1)

    @field_validator(
        "question_id",
        "question_text",
        "role_version",
        "dimension_id",
        "source_id",
        "source_url",
        "source_title",
        "source_type",
        "content_hash",
    )
    @classmethod
    def validate_non_blank_text(cls, value: str, info: Any) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator(
        "skills",
        "expected_signals",
        "critical_errors",
        "follow_up_seeds",
        "company_tags",
    )
    @classmethod
    def validate_list_items(cls, values: list[str], info: Any) -> list[str]:
        for index, value in enumerate(values):
            if not value.strip():
                raise ValueError(f"{info.field_name}[{index}] must not be blank")
        return values

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "InterviewQuestionRecord":
        if self.published_at > self.verified_at:
            raise ValueError("published_at must not be after verified_at")
        if self.valid_until < self.verified_at:
            raise ValueError("valid_until must not be before verified_at")
        if not self.content_hash.startswith("sha256:"):
            raise ValueError("content_hash must use the sha256: prefix")
        if not self.content_hash.removeprefix("sha256:").strip():
            raise ValueError("content_hash must contain a digest")
        return self


class QuestionRetrievalIntent(BaseModel):
    """Deterministic retrieval input derived from a Supervisor decision."""

    model_config = ConfigDict(extra="forbid")

    query_text: str = Field(min_length=1)
    role: Literal["ai_agent_engineer"]
    dimension_id: str = Field(min_length=1)
    question_mode: QuestionMode
    difficulty: QuestionDifficulty
    excluded_question_ids: list[str] = Field(default_factory=list)

    @field_validator("query_text", "dimension_id")
    @classmethod
    def validate_non_blank_text(cls, value: str, info: Any) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("excluded_question_ids")
    @classmethod
    def validate_exclusions(cls, values: list[str]) -> list[str]:
        for index, value in enumerate(values):
            if not value.strip():
                raise ValueError(f"excluded_question_ids[{index}] must not be blank")
        return values


class RetrievedQuestion(BaseModel):
    """A selected question plus retrieval metadata.

    ``record`` is the canonical field.  ``question`` is accepted as an input
    alias because callers often use that term for the same source record.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    record: InterviewQuestionRecord = Field(
        validation_alias=AliasChoices("record", "question")
    )
    score: float | None = None
    index_version: str | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_record_instance(cls, data: Any) -> Any:
        if isinstance(data, InterviewQuestionRecord):
            return {"record": data}
        return data

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("score must be finite")
        return value

    @field_validator("index_version")
    @classmethod
    def validate_index_version(cls, value: str | None) -> str | None:
        if value is not None:
            _require_non_blank(value, "index_version")
        return value

    @property
    def question(self) -> InterviewQuestionRecord:
        """Compatibility accessor for the selected source record."""

        return self.record

    @property
    def question_id(self) -> str:
        return self.record.question_id

    @property
    def source_id(self) -> str:
        return self.record.source_id


class QuestionRetrievalTrace(BaseModel):
    """Internal provenance for one retrieval attempt."""

    model_config = ConfigDict(extra="forbid")

    status: RetrievalStatus
    question_id: str | None = None
    source_id: str | None = None
    score: float | None = None
    index_version: str | None = None

    @field_validator("question_id", "source_id", "index_version")
    @classmethod
    def validate_optional_text(cls, value: str | None, info: Any) -> str | None:
        if value is not None:
            _require_non_blank(value, info.field_name)
        return value

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: float | None) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("score must be finite")
        return value

    @model_validator(mode="after")
    def validate_selected_ids(self) -> "QuestionRetrievalTrace":
        if self.status == "hit":
            if self.question_id is None or self.source_id is None:
                raise ValueError("hit trace requires question_id and source_id")
        elif self.question_id is not None or self.source_id is not None:
            raise ValueError("non-hit trace must not claim a selected question")
        return self


class QuestionRetrievalResult(BaseModel):
    """The selected question, or an explicit honest retrieval failure."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: RetrievalStatus
    selected_question: RetrievedQuestion | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "selected_question",
            "retrieved_question",
            "question",
            "selected_record",
        ),
    )
    trace: QuestionRetrievalTrace | None = Field(
        default=None,
        validation_alias=AliasChoices("trace", "retrieval_trace"),
    )

    @model_validator(mode="after")
    def validate_result_consistency(self) -> "QuestionRetrievalResult":
        trace = self.trace
        if trace is None:
            if self.status == "hit":
                raise ValueError("hit result requires a retrieval trace")
            trace = QuestionRetrievalTrace(status=self.status)
            object.__setattr__(self, "trace", trace)

        if trace.status != self.status:
            raise ValueError("result status and trace status must match")

        if self.status == "hit":
            selected = self.selected_question
            if selected is None:
                raise ValueError("hit result requires selected_question")
            if trace.question_id != selected.question_id:
                raise ValueError("trace question_id must match selected_question")
            if trace.source_id != selected.source_id:
                raise ValueError("trace source_id must match selected_question")
        elif self.selected_question is not None:
            raise ValueError("non-hit result must not contain selected_question")

        return self

    @property
    def retrieved_question(self) -> RetrievedQuestion | None:
        return self.selected_question

    @property
    def question(self) -> RetrievedQuestion | None:
        """Compatibility accessor for the selected question."""

        return self.selected_question

    @property
    def selected_record(self) -> RetrievedQuestion | None:
        return self.selected_question

    @property
    def retrieval_trace(self) -> QuestionRetrievalTrace:
        # The consistency validator always materializes a trace.
        assert self.trace is not None
        return self.trace
