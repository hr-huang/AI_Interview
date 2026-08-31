"""Strict contracts for the provider-independent Scenario RAG calibration.

The calibration bank is intentionally small and reviewable.  It describes
queries and expected module identities; it never contains provider output or
candidate-facing profile data.  Runtime results are kept separate so the
same cases can be evaluated against a fake retriever in unit tests and a real
retriever from the paid CLI.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from profile_agent.schemas.interview_schema import QuestionMode, TargetType
from profile_agent.schemas.question_rag_schema import QuestionDifficulty
from profile_agent.schemas.scenario_rag_schema import (
    OfficialDimensionId,
    ScenarioRetrievalStatus,
)


ScenarioCalibrationStatus = ScenarioRetrievalStatus


class _CalibrationBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _non_blank(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be blank")
    return value


def _unique_text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{field_name} must be a sequence")
    try:
        values = [_non_blank(item, f"{field_name}[{index}]") for index, item in enumerate(value)]
    except TypeError:
        raise TypeError(f"{field_name} must be a sequence") from None
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


class ScenarioRetrievalCase(_CalibrationBase):
    """One reviewed query and its acceptable/forbidden retrieval worlds."""

    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    primary_dimension_id: OfficialDimensionId
    requirement_type: TargetType
    question_mode: QuestionMode
    difficulty: QuestionDifficulty
    acceptable_module_ids: list[str] = Field(min_length=1)
    forbidden_module_ids: list[str] = Field(default_factory=list)

    @field_validator("case_id", "query", mode="before")
    @classmethod
    def validate_text(cls, value: Any, info: Any) -> str:
        return _non_blank(value, info.field_name)

    @field_validator("acceptable_module_ids", "forbidden_module_ids", mode="before")
    @classmethod
    def validate_module_ids(cls, value: Any, info: Any) -> list[str]:
        return _unique_text_list(value, info.field_name)

    @model_validator(mode="after")
    def reject_conflicting_expectations(self) -> "ScenarioRetrievalCase":
        overlap = sorted(
            set(self.acceptable_module_ids) & set(self.forbidden_module_ids)
        )
        if overlap:
            raise ValueError(
                "acceptable_module_ids and forbidden_module_ids must be disjoint: "
                + ", ".join(overlap)
            )
        return self


class ScenarioCalibrationCaseResult(_CalibrationBase):
    """Normalized result for one calibration case.

    ``top3_module_ids`` contains at most the first three ranked module IDs.
    For a fallback or unavailable result it is empty, while
    ``top1_module_id`` may still carry the explicitly selected fallback.
    """

    case_id: str = Field(min_length=1)
    status: ScenarioCalibrationStatus
    top1_module_id: str | None = None
    top3_module_ids: list[str] = Field(default_factory=list)
    top1_acceptable: bool = False
    top1_forbidden: bool = False
    acceptable_in_top3: bool = False
    forbidden_hits: list[str] = Field(default_factory=list)
    fallback: bool = False
    error: str | None = None

    @field_validator("case_id", mode="before")
    @classmethod
    def validate_case_id(cls, value: Any) -> str:
        return _non_blank(value, "case_id")

    @field_validator("top1_module_id", "error", mode="before")
    @classmethod
    def validate_optional_text(cls, value: Any, info: Any) -> str | None:
        return None if value is None else _non_blank(value, info.field_name)

    @field_validator("top3_module_ids", "forbidden_hits", mode="before")
    @classmethod
    def validate_result_lists(cls, value: Any, info: Any) -> list[str]:
        return _unique_text_list(value, info.field_name)

    @model_validator(mode="after")
    def validate_result_identity(self) -> "ScenarioCalibrationCaseResult":
        if self.top1_module_id is not None and self.top3_module_ids:
            if self.top1_module_id != self.top3_module_ids[0]:
                raise ValueError("top1_module_id must be the first top3_module_ids entry")
        if self.status == "fallback" and not self.fallback:
            object.__setattr__(self, "fallback", True)
        if self.status != "fallback" and self.fallback:
            raise ValueError("fallback can only be true for fallback status")
        return self


class ScenarioCalibrationRunMetadata(_CalibrationBase):
    """Safe, non-secret identity for one real calibration run.

    The report records provider/model and index identity only. Credentials,
    endpoint URLs, local paths, and arbitrary provider configuration are
    intentionally not part of this contract.
    """

    embedding_provider: str
    embedding_model: str
    reranker_provider: str
    reranker_model: str
    qdrant_collection: str
    qdrant_index_version: str
    bank_version: int = Field(ge=1)
    role_family: str
    role_profile_version: str
    as_of: date
    created_at: datetime
    bank_manifest_hash: str | None = None

    @field_validator(
        "embedding_provider",
        "embedding_model",
        "reranker_provider",
        "reranker_model",
        "qdrant_collection",
        "qdrant_index_version",
        "role_family",
        "role_profile_version",
        mode="before",
    )
    @classmethod
    def validate_required_text(cls, value: Any, info: Any) -> str:
        return _non_blank(value, info.field_name)

    @field_validator("bank_manifest_hash", mode="before")
    @classmethod
    def validate_optional_hash(cls, value: Any) -> str | None:
        return None if value is None else _non_blank(value, "bank_manifest_hash")

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: Any) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("created_at must be an ISO datetime") from exc
        if not isinstance(value, datetime):
            raise TypeError("created_at must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(timezone.utc)


class ScenarioCalibrationReport(_CalibrationBase):
    """Aggregate retrieval metrics for one complete calibration run."""

    case_count: int = Field(ge=0)
    top1_acceptable_rate: float = Field(ge=0.0, le=1.0)
    top3_recall: float = Field(ge=0.0, le=1.0)
    forbidden_hit_count: int = Field(ge=0)
    forbidden_top1_hit_count: int = Field(default=0, ge=0)
    fallback_count: int = Field(ge=0)
    case_results: list[ScenarioCalibrationCaseResult] = Field(default_factory=list)
    metadata: ScenarioCalibrationRunMetadata | None = None

    @field_validator("top1_acceptable_rate", "top3_recall")
    @classmethod
    def validate_rates(cls, value: float) -> float:
        if not isfinite(float(value)):
            raise ValueError("calibration rates must be finite")
        return float(value)

    @model_validator(mode="after")
    def validate_case_count(self) -> "ScenarioCalibrationReport":
        if self.case_count != len(self.case_results):
            raise ValueError("case_count must equal len(case_results)")
        return self


__all__ = [
    "ScenarioCalibrationCaseResult",
    "ScenarioCalibrationReport",
    "ScenarioCalibrationRunMetadata",
    "ScenarioCalibrationStatus",
    "ScenarioRetrievalCase",
]
