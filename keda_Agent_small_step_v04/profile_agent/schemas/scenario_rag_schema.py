"""Contracts for the reviewed scenario-module retrieval path.

The scenario bank is deliberately separate from the legacy fixed-question
bank.  Scenario JSON is the source of truth; the retrieval index only returns
stable module identities.  These models therefore keep candidate-facing text
small while retaining enough metadata for deterministic validation and audit.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from math import isfinite
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from profile_agent.schemas.interview_schema import QuestionMode, TargetType
from profile_agent.schemas.question_rag_schema import QuestionDifficulty


SCENARIO_ROLE_FAMILY = "ai_application_engineering"
SCENARIO_ROLE_PROFILE_VERSION = "2026-H2"

OfficialDimensionId = Literal[
    "role_dim_01",
    "role_dim_02",
    "role_dim_03",
    "role_dim_04",
    "role_dim_05",
    "role_dim_06",
]
ScenarioLifecycleStatus = Literal["active", "needs_review", "retired"]
ScenarioRetrievalStatus = Literal[
    "hit",
    "fallback",
    "bypass",
    "no_match",
    "unavailable",
    "invalid_result",
    "index_mismatch",
]


def _non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be blank")
    return value


def _clean_list(values: Any, field_name: str) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence")
    try:
        result = [
            _non_blank(value, f"{field_name}[{index}]")
            for index, value in enumerate(values)
        ]
    except TypeError:
        raise TypeError(f"{field_name} must be a sequence") from None
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


class _ScenarioBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ScenarioCard(_ScenarioBase):
    """One complete, reviewed enterprise business world."""

    scenario_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    role_family: Literal["ai_application_engineering"] = SCENARIO_ROLE_FAMILY
    role_profile_version: Literal["2026-H2"] = SCENARIO_ROLE_PROFILE_VERSION
    business_goal: str = Field(min_length=1)
    actors: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    base_constraints: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    status: ScenarioLifecycleStatus = "active"
    valid_from: date
    valid_until: date | None = None
    version: int = Field(default=1, ge=1)
    content_hash: str | None = None

    @field_validator(
        "scenario_id",
        "title",
        "business_goal",
        "role_family",
        "role_profile_version",
        mode="before",
    )
    @classmethod
    def validate_text(cls, value: str, info: Any) -> str:
        return _non_blank(value, info.field_name)

    @field_validator("actors", "tools", "base_constraints", "modules", "source_ids", "source_refs", mode="before")
    @classmethod
    def validate_lists(cls, value: Any, info: Any) -> list[str]:
        return _clean_list(value, info.field_name)

    @model_validator(mode="after")
    def validate_dates(self) -> "ScenarioCard":
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("valid_until must not be before valid_from")
        if self.content_hash is not None:
            _non_blank(self.content_hash, "content_hash")
        return self


class ScenarioModule(_ScenarioBase):
    """One scenario × one official radar dimension retrieval unit."""

    module_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    role_family: Literal["ai_application_engineering"] = SCENARIO_ROLE_FAMILY
    role_profile_version: Literal["2026-H2"] = SCENARIO_ROLE_PROFILE_VERSION
    primary_dimension_id: OfficialDimensionId
    supported_requirement_types: list[TargetType] = Field(min_length=1)
    supported_modes: list[QuestionMode] = Field(min_length=1)
    difficulties: list[QuestionDifficulty] = Field(
        default_factory=list,
        validation_alias=AliasChoices("difficulties", "difficulty"),
    )
    opening_goal: str = Field(min_length=1)
    semantic_text: str = Field(min_length=1, max_length=1000)
    evidence_signals: list[str] = Field(min_length=1)
    critical_errors: list[str] = Field(default_factory=list)
    constraint_ids: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    source_question_ids: list[str] = Field(default_factory=list)
    default_for_dimension: bool = False
    status: ScenarioLifecycleStatus = "active"
    valid_from: date
    valid_until: date | None = None
    version: int = Field(default=1, ge=1)
    content_hash: str | None = None

    @field_validator(
        "module_id",
        "scenario_id",
        "role_family",
        "role_profile_version",
        "opening_goal",
        "semantic_text",
        mode="before",
    )
    @classmethod
    def validate_text(cls, value: str, info: Any) -> str:
        return _non_blank(value, info.field_name)

    @field_validator(
        "supported_requirement_types",
        "supported_modes",
        "difficulties",
        "evidence_signals",
        "critical_errors",
        "constraint_ids",
        "source_refs",
        "source_question_ids",
        mode="before",
    )
    @classmethod
    def validate_lists(cls, value: Any, info: Any) -> list[Any]:
        # Enum lists are validated by Pydantic after blank/duplicate checking.
        if value is None:
            return []
        if isinstance(value, (str, bytes)):
            raise TypeError(f"{info.field_name} must be a sequence")
        try:
            values = list(value)
        except TypeError:
            raise TypeError(f"{info.field_name} must be a sequence") from None
        if len(values) != len(set(values)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        if info.field_name not in {"supported_requirement_types", "supported_modes", "difficulties"}:
            return _clean_list(values, info.field_name)
        return values

    @model_validator(mode="after")
    def validate_module(self) -> "ScenarioModule":
        if not self.difficulties:
            raise ValueError("difficulties must not be empty")
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("valid_until must not be before valid_from")
        if self.content_hash is not None:
            _non_blank(self.content_hash, "content_hash")
        return self

    @property
    def retrieval_unit_id(self) -> str:
        return f"{self.scenario_id}::{self.module_id}"

class ScenarioConstraint(_ScenarioBase):
    """One reviewed, selectively released scenario fact."""

    constraint_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    module_id: str = Field(min_length=1)
    evidence_gap_tags: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("evidence_gap_tags", "gap_tags"),
    )
    difficulty: QuestionDifficulty = "intermediate"
    # ``description`` is the operator-facing label; ``fact`` is the
    # candidate-safe reviewed fact used by QuestionGenerator.  Both names are
    # accepted because the design and inventory use both terms.
    description: str = ""
    fact: str | None = None
    expected_signals: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    source_question_ids: list[str] = Field(default_factory=list)
    status: ScenarioLifecycleStatus = "active"
    valid_from: date | None = None
    valid_until: date | None = None
    version: int = Field(default=1, ge=1)
    content_hash: str | None = None

    @field_validator(
        "constraint_id",
        "scenario_id",
        "module_id",
        "description",
        mode="before",
    )
    @classmethod
    def validate_text(cls, value: str, info: Any) -> str:
        if value is None:
            return ""
        return value if info.field_name == "description" else _non_blank(value, info.field_name)

    @field_validator("fact", mode="before")
    @classmethod
    def validate_fact(cls, value: str | None) -> str | None:
        return None if value is None else _non_blank(value, "fact")

    @field_validator("evidence_gap_tags", "expected_signals", "source_refs", "source_question_ids", mode="before")
    @classmethod
    def validate_lists(cls, value: Any, info: Any) -> list[str]:
        return _clean_list(value, info.field_name)

    @model_validator(mode="after")
    def complete_fact_and_validate_dates(self) -> "ScenarioConstraint":
        description = self.description.strip()
        fact = (self.fact or "").strip()
        if not description and not fact:
            raise ValueError("constraint requires description or fact")
        if not description:
            object.__setattr__(self, "description", fact)
        if not fact:
            object.__setattr__(self, "fact", description)
        if self.valid_from is not None and self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("valid_until must not be before valid_from")
        if self.content_hash is not None:
            _non_blank(self.content_hash, "content_hash")
        return self

    @property
    def gap_tags(self) -> list[str]:
        return self.evidence_gap_tags


class ScenarioRetrievalUnit(_ScenarioBase):
    """The index projection for one ScenarioModule."""

    retrieval_unit_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    module_id: str = Field(min_length=1)
    role_family: Literal["ai_application_engineering"] = SCENARIO_ROLE_FAMILY
    role_profile_version: Literal["2026-H2"] = SCENARIO_ROLE_PROFILE_VERSION
    primary_dimension_id: OfficialDimensionId
    supported_modes: list[QuestionMode] = Field(min_length=1)
    supported_requirement_types: list[TargetType] = Field(min_length=1)
    difficulties: list[QuestionDifficulty] = Field(min_length=1)
    status: ScenarioLifecycleStatus = "active"
    valid_from: date
    valid_until: date | None = None
    semantic_text: str = Field(min_length=1, max_length=1000)
    version: int = Field(default=1, ge=1)

    @classmethod
    def from_module(cls, module: ScenarioModule) -> "ScenarioRetrievalUnit":
        return cls(
            retrieval_unit_id=module.retrieval_unit_id,
            scenario_id=module.scenario_id,
            module_id=module.module_id,
            role_family=module.role_family,
            role_profile_version=module.role_profile_version,
            primary_dimension_id=module.primary_dimension_id,
            supported_modes=list(module.supported_modes),
            supported_requirement_types=list(module.supported_requirement_types),
            difficulties=list(module.difficulties),
            status=module.status,
            valid_from=module.valid_from,
            valid_until=module.valid_until,
            semantic_text=module.semantic_text,
            version=module.version,
        )


class ScenarioRetrievalRequest(_ScenarioBase):
    """Deterministic, narrow request emitted by PrepareQuestionContext."""

    role_family: Literal["ai_application_engineering"] = SCENARIO_ROLE_FAMILY
    role_profile_version: Literal["2026-H2"] = SCENARIO_ROLE_PROFILE_VERSION
    primary_dimension_id: OfficialDimensionId
    requirement_type: TargetType
    question_mode: QuestionMode
    difficulty: QuestionDifficulty
    objective: str = Field(min_length=1)
    evidence_gap: list[str] = Field(default_factory=list)
    semantic_query: str = ""
    excluded_retrieval_unit_ids: list[str] = Field(default_factory=list)
    excluded_scenario_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "role_family",
        "role_profile_version",
        "objective",
        mode="before",
    )
    @classmethod
    def validate_text(cls, value: str, info: Any) -> str:
        return _non_blank(value, info.field_name)

    @field_validator("evidence_gap", "excluded_retrieval_unit_ids", "excluded_scenario_ids", mode="before")
    @classmethod
    def validate_lists(cls, value: Any, info: Any) -> list[str]:
        return _clean_list(value, info.field_name)

    @field_validator("semantic_query", mode="before")
    @classmethod
    def normalize_query(cls, value: Any) -> str:
        return "" if value is None else str(value).strip()

    @model_validator(mode="after")
    def build_query_when_omitted(self) -> "ScenarioRetrievalRequest":
        if not self.semantic_query:
            parts = [self.objective, *self.evidence_gap]
            object.__setattr__(self, "semantic_query", "\n".join(part for part in parts if part).strip())
        if not self.semantic_query:
            raise ValueError("semantic_query must not be blank")
        return self


class ScenarioCandidate(_ScenarioBase):
    retrieval_unit_id: str = Field(min_length=1)
    scenario_id: str | None = None
    module_id: str | None = None
    score: float | None = None
    dense_score: float | None = None
    lexical_score: float | None = None
    index_version: str | None = None

    @field_validator("retrieval_unit_id", mode="before")
    @classmethod
    def validate_unit_id(cls, value: str) -> str:
        return _non_blank(value, "retrieval_unit_id")

    @field_validator("score", "dense_score", "lexical_score")
    @classmethod
    def validate_score(cls, value: float | None) -> float | None:
        if value is not None and (not isinstance(value, (int, float)) or not isfinite(float(value))):
            raise ValueError("candidate score must be finite")
        return None if value is None else float(value)

    @model_validator(mode="after")
    def complete_identity(self) -> "ScenarioCandidate":
        scenario_id, separator, module_id = self.retrieval_unit_id.partition("::")
        if not separator or not scenario_id or not module_id:
            raise ValueError("retrieval_unit_id must be scenario_id::module_id")
        if self.scenario_id is None:
            object.__setattr__(self, "scenario_id", scenario_id)
        if self.module_id is None:
            object.__setattr__(self, "module_id", module_id)
        if self.scenario_id != scenario_id or self.module_id != module_id:
            raise ValueError("candidate identity does not match retrieval_unit_id")
        return self


class ScenarioCandidateSet(_ScenarioBase):
    status: Literal["hit", "no_match", "unavailable", "index_mismatch"]
    candidates: list[ScenarioCandidate] = Field(
        default_factory=list,
        validation_alias=AliasChoices("candidates", "hits", "results"),
    )
    index_version: str | None = None
    hard_filter: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_status(self) -> "ScenarioCandidateSet":
        if self.status == "hit" and not self.candidates:
            raise ValueError("hit candidate set requires candidates")
        if self.status != "hit" and self.candidates:
            raise ValueError("non-hit candidate set must not contain candidates")
        return self

class ScenarioSelection(_ScenarioBase):
    """A validated module selection, optionally carrying canonical objects."""

    status: ScenarioRetrievalStatus
    retrieval_unit_id: str | None = None
    scenario_id: str | None = None
    module_id: str | None = None
    score: float | None = None
    index_version: str | None = None
    fallback_reason: str | None = None
    scenario: ScenarioCard | None = None
    module: ScenarioModule | None = None
    selected_constraint: ScenarioConstraint | None = None
    revealed_constraint_ids: list[str] = Field(default_factory=list)

    @field_validator("retrieval_unit_id", "scenario_id", "module_id", "fallback_reason", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any, info: Any) -> Any:
        if value is None:
            return None
        return _non_blank(value, info.field_name)

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: float | None) -> float | None:
        if value is not None and (not isinstance(value, (int, float)) or not isfinite(float(value))):
            raise ValueError("selection score must be finite")
        return None if value is None else float(value)

    @field_validator("revealed_constraint_ids", mode="before")
    @classmethod
    def validate_revealed_ids(cls, value: Any) -> list[str]:
        return _clean_list(value, "revealed_constraint_ids")

    @model_validator(mode="after")
    def validate_selection_identity(self) -> "ScenarioSelection":
        if self.status in {"hit", "fallback"}:
            if self.retrieval_unit_id is None:
                if self.scenario_id and self.module_id:
                    object.__setattr__(self, "retrieval_unit_id", f"{self.scenario_id}::{self.module_id}")
                else:
                    raise ValueError("selected scenario requires retrieval_unit_id")
            scenario_id, separator, module_id = self.retrieval_unit_id.partition("::")
            if not separator:
                raise ValueError("retrieval_unit_id must be scenario_id::module_id")
            if self.scenario_id is None:
                object.__setattr__(self, "scenario_id", scenario_id)
            if self.module_id is None:
                object.__setattr__(self, "module_id", module_id)
            if self.scenario_id != scenario_id or self.module_id != module_id:
                raise ValueError("selection identity does not match retrieval_unit_id")
        elif any(value is not None for value in (self.retrieval_unit_id, self.scenario_id, self.module_id, self.scenario, self.module)):
            raise ValueError("non-selected scenario must not carry module identity")
        if self.status == "fallback" and not (self.fallback_reason or "").strip():
            raise ValueError("fallback selection requires fallback_reason")
        return self

class QuestionProvenance(_ScenarioBase):
    """Checkpoint-private provenance copied to every InterviewTurn."""

    target_requirement_id: str = Field(min_length=1)
    primary_dimension_id: OfficialDimensionId
    retrieval_unit_id: str | None = None
    scenario_id: str | None = None
    module_id: str | None = None
    selected_constraint_id: str | None = None
    revealed_constraint_ids: list[str] = Field(default_factory=list)
    retrieval_status: ScenarioRetrievalStatus
    fallback_reason: str | None = None

    @field_validator(
        "target_requirement_id",
        "retrieval_unit_id",
        "scenario_id",
        "module_id",
        "selected_constraint_id",
        "fallback_reason",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: Any, info: Any) -> Any:
        if value is None:
            return None
        return _non_blank(value, info.field_name)

    @field_validator("revealed_constraint_ids", mode="before")
    @classmethod
    def normalize_ids(cls, value: Any) -> list[str]:
        return _clean_list(value, "revealed_constraint_ids")

    @model_validator(mode="after")
    def validate_identity(self) -> "QuestionProvenance":
        if self.retrieval_status in {"hit", "fallback"}:
            if not self.retrieval_unit_id or not self.scenario_id or not self.module_id:
                raise ValueError("selected provenance requires scenario identities")
            if self.retrieval_unit_id != f"{self.scenario_id}::{self.module_id}":
                raise ValueError("retrieval_unit_id does not match provenance IDs")
        if self.selected_constraint_id is not None and self.selected_constraint_id not in self.revealed_constraint_ids:
            raise ValueError("selected constraint must be present in revealed_constraint_ids")
        if self.retrieval_status == "fallback" and not (self.fallback_reason or "").strip():
            raise ValueError("fallback provenance requires fallback_reason")
        return self

class LockedScenarioContext(_ScenarioBase):
    """Candidate-safe context passed from PrepareQuestionContext to the generator."""

    scenario_id: str = Field(min_length=1)
    module_id: str = Field(min_length=1)
    retrieval_unit_id: str = Field(min_length=1)
    business_goal: str = Field(min_length=1)
    opening_goal: str = Field(min_length=1)
    selected_constraint: ScenarioConstraint | None = None
    revealed_constraint_ids: list[str] = Field(default_factory=list)
    retrieval_status: ScenarioRetrievalStatus
    fallback_reason: str | None = None
    scenario: ScenarioCard | None = None
    module: ScenarioModule | None = None
    provenance: QuestionProvenance | None = None

    @field_validator(
        "scenario_id",
        "module_id",
        "retrieval_unit_id",
        "business_goal",
        "opening_goal",
        mode="before",
    )
    @classmethod
    def validate_text(cls, value: str, info: Any) -> str:
        return _non_blank(value, info.field_name)

    @field_validator("revealed_constraint_ids", mode="before")
    @classmethod
    def validate_revealed_ids(cls, value: Any) -> list[str]:
        return _clean_list(value, "revealed_constraint_ids")

    @model_validator(mode="after")
    def validate_context(self) -> "LockedScenarioContext":
        if self.retrieval_unit_id != f"{self.scenario_id}::{self.module_id}":
            raise ValueError("retrieval_unit_id does not match scenario/module")
        if self.selected_constraint is not None and self.selected_constraint.constraint_id not in self.revealed_constraint_ids:
            raise ValueError("selected constraint must be present in revealed_constraint_ids")
        if self.retrieval_status == "fallback" and not (self.fallback_reason or "").strip():
            raise ValueError("fallback context requires fallback_reason")
        return self

    @property
    def selected_constraint_id(self) -> str | None:
        return self.selected_constraint.constraint_id if self.selected_constraint else None


class ScenarioBankManifest(_ScenarioBase):
    role_family: Literal["ai_application_engineering"] = SCENARIO_ROLE_FAMILY
    role_profile_version: Literal["2026-H2"] = SCENARIO_ROLE_PROFILE_VERSION
    scenario_count: int = Field(default=0, ge=0)
    retrieval_module_count: int = Field(default=0, ge=0)
    version: int = Field(default=1, ge=1)
    scenario_ids: list[str] = Field(default_factory=list)
    module_ids: list[str] = Field(default_factory=list)
    source_registry_ids: list[str] = Field(default_factory=list)
    manifest_hash: str | None = None

    @field_validator("scenario_ids", "module_ids", "source_registry_ids", mode="before")
    @classmethod
    def validate_ids(cls, value: Any, info: Any) -> list[str]:
        return _clean_list(value, info.field_name)

    @field_validator("manifest_hash", mode="before")
    @classmethod
    def normalize_hash(cls, value: Any) -> str | None:
        return None if value is None else _non_blank(value, "manifest_hash")


class ScenarioSourceRecord(_ScenarioBase):
    source_id: str = Field(min_length=1)
    title: str = ""
    source_type: str = "internal_review"
    status: ScenarioLifecycleStatus = "active"
    source_url: str | None = None
    notes: str = ""

    @field_validator("source_id", mode="before")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        return _non_blank(value, "source_id")

    @field_validator("title", "source_type", "notes", "source_url", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any, info: Any) -> Any:
        if value is None:
            return None if info.field_name == "source_url" else ""
        return str(value).strip()


class ScenarioSourceRegistry(_ScenarioBase):
    sources: list[ScenarioSourceRecord] = Field(
        default_factory=list,
        validation_alias=AliasChoices("sources", "records", "entries"),
    )

    @property
    def by_id(self) -> dict[str, ScenarioSourceRecord]:
        return {source.source_id: source for source in self.sources}


__all__ = [
    "OfficialDimensionId",
    "LockedScenarioContext",
    "QuestionProvenance",
    "SCENARIO_ROLE_FAMILY",
    "SCENARIO_ROLE_PROFILE_VERSION",
    "ScenarioBankManifest",
    "ScenarioCandidate",
    "ScenarioCandidateSet",
    "ScenarioCard",
    "ScenarioConstraint",
    "ScenarioLifecycleStatus",
    "ScenarioModule",
    "ScenarioRetrievalRequest",
    "ScenarioRetrievalStatus",
    "ScenarioRetrievalUnit",
    "ScenarioSelection",
    "ScenarioSourceRecord",
    "ScenarioSourceRegistry",
]
