"""Strict, secret-free contracts for interview-question retrieval.

The question bank is intentionally represented separately from the retrieval
trace.  A record is the auditable source data, while a trace describes what a
runtime retrieval attempt selected (or why it could not select anything).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from math import isfinite
from typing import Any, Literal
from urllib.parse import urlparse

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

QuestionSourceType = Literal[
    "public_interview_experience",
    "official_technical_doc",
    "current_enterprise_jd",
]
QuestionSourceLifecycle = Literal["draft", "active", "needs_review", "retired"]
QuestionReviewClass = Literal["dynamic", "evergreen"]
QuestionPublicationStatus = Literal["draft", "ready", "published", "retired"]
QuestionReviewDecision = Literal[
    "pending_human",
    "approved",
    "rejected",
    "needs_revision",
    "retired",
]
QuestionDedupeDecision = Literal[
    "unique",
    "duplicate",
    "pending",
    "needs_review",
    "rejected",
]
QuestionNearDuplicateDecision = Literal[
    "clear",
    "unique",
    "duplicate",
    "pending",
]
QuestionRightsDecision = Literal["approved", "rejected", "pending_human"]

QUESTION_MODES: tuple[QuestionMode, ...] = (
    "foundation",
    "project_deep_dive",
    "scenario",
    "system_design",
    "coding",
    "follow_up",
)
QUESTION_DIMENSIONS: tuple[str, ...] = (
    "role_dim_01",
    "role_dim_02",
    "role_dim_03",
    "role_dim_04",
    "role_dim_05",
    "role_dim_06",
)
QuestionDimensionId = Literal[
    "role_dim_01",
    "role_dim_02",
    "role_dim_03",
    "role_dim_04",
    "role_dim_05",
    "role_dim_06",
]
DEFAULT_DIMENSION_QUOTAS: dict[str, int] = {
    "role_dim_01": 6,
    "role_dim_02": 5,
    "role_dim_03": 6,
    "role_dim_04": 4,
    "role_dim_05": 6,
    "role_dim_06": 3,
}
DEFAULT_PRIMARY_MODE_QUOTAS: dict[QuestionMode, int] = {
    "foundation": 4,
    "project_deep_dive": 5,
    "scenario": 8,
    "system_design": 4,
    "coding": 3,
    "follow_up": 6,
}
CORPUS_ROLE = "ai_agent_engineer"
CORPUS_ROLE_VERSION = "2026-H2"
MODE_POLICY_VERSION = "2026-H2"
CORPUS_AS_OF = date(2026, 8, 27)


def _require_non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


class InterviewQuestionRecord(BaseModel):
    """One reviewed interview question in the versioned question bank.

    The original fields are deliberately retained as the v1 wire contract.
    The v2 semantic fields are additive, so an old record can still be loaded
    and is projected to ``primary_mode=question_mode`` with no compatible
    modes.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    question_text: str = Field(min_length=1)
    role: Literal["ai_agent_engineer"]
    role_version: Literal["2026-H2"]
    dimension_id: QuestionDimensionId
    skills: list[str] = Field(min_length=1)
    # ``question_mode`` remains the compatibility/wire field.  It is optional
    # only when a v2 caller supplies the additive ``primary_mode`` field.
    question_mode: QuestionMode | None = None
    business_constraint: str = ""
    dimension_terms: list[str] = Field(default_factory=list)
    primary_mode: QuestionMode | None = None
    compatible_modes: list[QuestionMode] = Field(default_factory=list)
    difficulty: QuestionDifficulty
    expected_signals: list[str] = Field(min_length=1)
    critical_errors: list[str]
    follow_up_seeds: list[str]
    company_tags: list[str]
    source_id: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)
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
        "dimension_terms",
        "compatible_modes",
        "source_ids",
    )
    @classmethod
    def validate_list_items(cls, values: list[str], info: Any) -> list[str]:
        for index, value in enumerate(values):
            if not value.strip():
                raise ValueError(f"{info.field_name}[{index}] must not be blank")
        return values

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "InterviewQuestionRecord":
        # Empty is the safe default for a v1 record that has no additive v2
        # field.  If supplied, however, whitespace-only text is still invalid.
        if self.business_constraint and not self.business_constraint.strip():
            raise ValueError("business_constraint must not be blank")

        if self.question_mode is None and self.primary_mode is None:
            raise ValueError("record requires question_mode or primary_mode")

        # Keep both names materialized.  This makes the projection explicit
        # while preserving the old field for existing consumers.  A mismatch
        # is only tolerated when one side is a derived compatibility value;
        # two explicitly supplied values must never be silently overwritten.
        fields_set = self.model_fields_set
        question_mode_explicit = "question_mode" in fields_set
        primary_mode_explicit = "primary_mode" in fields_set
        if self.question_mode is None:
            object.__setattr__(self, "question_mode", self.primary_mode)
        if self.primary_mode is None:
            object.__setattr__(self, "primary_mode", self.question_mode)
        if self.question_mode != self.primary_mode:
            if question_mode_explicit and primary_mode_explicit:
                raise ValueError("question_mode and primary_mode must match")
            if question_mode_explicit:
                object.__setattr__(self, "primary_mode", self.question_mode)
            else:
                object.__setattr__(self, "question_mode", self.primary_mode)

        if len(set(self.compatible_modes)) != len(self.compatible_modes):
            raise ValueError("compatible_modes must not contain duplicates")
        if self.primary_mode in self.compatible_modes:
            raise ValueError("compatible_modes must not contain primary_mode")

        if not self.source_ids:
            object.__setattr__(self, "source_ids", [self.source_id])
        elif len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source_ids must not contain duplicates")

        if self.published_at > self.verified_at:
            raise ValueError("published_at must not be after verified_at")
        if self.valid_until < self.verified_at:
            raise ValueError("valid_until must not be before verified_at")
        if not self.content_hash.startswith("sha256:"):
            raise ValueError("content_hash must use the sha256: prefix")
        if not self.content_hash.removeprefix("sha256:").strip():
            raise ValueError("content_hash must contain a digest")
        return self

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Keep derived v2 fields out of the legacy v1 wire projection.

        ``primary_mode`` is materialized in memory for a v1 record, but it was
        not explicitly supplied by that caller.  Omitting that derived value
        on serialization lets old ``model_copy(update={"question_mode": ...})``
        paths round-trip through a strict validation boundary without turning
        the derived value into a conflicting explicit field.
        """

        payload = super().model_dump(*args, **kwargs)
        if "primary_mode" not in self.model_fields_set:
            payload.pop("primary_mode", None)
        return payload


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
    as_of: date | None = None
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
            if self.as_of is None:
                raise ValueError("hit result requires as_of")
            selected = self.selected_question
            if selected is None:
                raise ValueError("hit result requires selected_question")
            if selected.record.status != "active":
                raise ValueError("hit result requires an active selected question")
            if selected.record.valid_until < self.as_of:
                raise ValueError("selected question is expired as of retrieval")
            if trace.question_id != selected.question_id:
                raise ValueError("trace question_id must match selected_question")
            if trace.source_id != selected.source_id:
                raise ValueError("trace source_id must match selected_question")
            if trace.score != selected.score:
                raise ValueError("trace score must match selected_question")
            if trace.index_version != selected.index_version:
                raise ValueError("trace index_version must match selected_question")
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


class QuestionDimensionModePolicy(BaseModel):
    """Allowlist and deterministic order for one role dimension."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    allowed_primary_modes: tuple[QuestionMode, ...]
    allowed_compatible_modes: tuple[QuestionMode, ...]
    preferred_order: tuple[QuestionMode, ...]

    @model_validator(mode="after")
    def validate_policy_shape(self) -> "QuestionDimensionModePolicy":
        allowed_primary = set(self.allowed_primary_modes)
        allowed_compatible = set(self.allowed_compatible_modes)
        preferred = tuple(self.preferred_order)
        if len(self.allowed_primary_modes) != len(allowed_primary):
            raise ValueError("allowed_primary_modes must not contain duplicates")
        if len(self.allowed_compatible_modes) != len(allowed_compatible):
            raise ValueError("allowed_compatible_modes must not contain duplicates")
        if len(preferred) != len(set(preferred)):
            raise ValueError("preferred_order must not contain duplicates")
        if not allowed_primary.issubset(allowed_compatible):
            raise ValueError(
                "allowed_primary_modes must be a subset of allowed_compatible_modes"
            )
        if set(preferred) != allowed_compatible:
            raise ValueError(
                "preferred_order must contain every allowed compatible mode exactly once"
            )
        return self


def _default_dimension_mode_policies() -> dict[str, QuestionDimensionModePolicy]:
    first_five: tuple[QuestionMode, ...] = (
        "foundation",
        "project_deep_dive",
        "scenario",
        "system_design",
        "follow_up",
    )
    all_modes = QUESTION_MODES
    return {
        "role_dim_01": QuestionDimensionModePolicy(
            allowed_primary_modes=first_five,
            allowed_compatible_modes=first_five,
            preferred_order=(
                "system_design",
                "scenario",
                "project_deep_dive",
                "foundation",
                "follow_up",
            ),
        ),
        "role_dim_02": QuestionDimensionModePolicy(
            allowed_primary_modes=first_five,
            allowed_compatible_modes=first_five,
            preferred_order=(
                "scenario",
                "project_deep_dive",
                "system_design",
                "foundation",
                "follow_up",
            ),
        ),
        "role_dim_03": QuestionDimensionModePolicy(
            allowed_primary_modes=all_modes,
            allowed_compatible_modes=all_modes,
            preferred_order=(
                "scenario",
                "system_design",
                "coding",
                "foundation",
                "project_deep_dive",
                "follow_up",
            ),
        ),
        "role_dim_04": QuestionDimensionModePolicy(
            allowed_primary_modes=all_modes,
            allowed_compatible_modes=all_modes,
            preferred_order=(
                "project_deep_dive",
                "coding",
                "scenario",
                "system_design",
                "foundation",
                "follow_up",
            ),
        ),
        "role_dim_05": QuestionDimensionModePolicy(
            allowed_primary_modes=all_modes,
            allowed_compatible_modes=all_modes,
            preferred_order=(
                "scenario",
                "system_design",
                "coding",
                "foundation",
                "project_deep_dive",
                "follow_up",
            ),
        ),
        "role_dim_06": QuestionDimensionModePolicy(
            allowed_primary_modes=all_modes,
            allowed_compatible_modes=all_modes,
            preferred_order=(
                "scenario",
                "system_design",
                "coding",
                "project_deep_dive",
                "foundation",
                "follow_up",
            ),
        ),
    }


class QuestionModePolicy(BaseModel):
    """Frozen six-mode policy used by the v2 corpus and retrieval router."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mode_policy_version: Literal["2026-H2"] = Field(
        default=MODE_POLICY_VERSION,
        validation_alias=AliasChoices("mode_policy_version", "policy_version"),
    )
    modes: tuple[QuestionMode, ...] = Field(
        default=QUESTION_MODES,
        validation_alias=AliasChoices("modes", "allowed_modes"),
    )
    dimension_policies: dict[str, QuestionDimensionModePolicy] = Field(
        default_factory=_default_dimension_mode_policies,
        validation_alias=AliasChoices("dimension_policies", "dimensions"),
    )

    @model_validator(mode="after")
    def validate_frozen_policy(self) -> "QuestionModePolicy":
        if tuple(self.modes) != QUESTION_MODES:
            raise ValueError("QuestionModePolicy must contain exactly the six supported modes")
        if set(self.dimension_policies) != set(QUESTION_DIMENSIONS):
            raise ValueError(
                "QuestionModePolicy must contain exactly the six role dimensions"
            )
        expected = _default_dimension_mode_policies()
        for dimension_id in QUESTION_DIMENSIONS:
            actual = self.dimension_policies[dimension_id]
            frozen = expected[dimension_id]
            if actual != frozen:
                raise ValueError(
                    f"QuestionModePolicy for {dimension_id} does not match the frozen policy"
                )
        return self

    @classmethod
    def default(cls) -> "QuestionModePolicy":
        """Return a fresh copy of the frozen policy."""

        return cls()

    @property
    def allowed_modes(self) -> tuple[QuestionMode, ...]:
        return self.modes

    @property
    def allowed_primary_modes(self) -> dict[str, tuple[QuestionMode, ...]]:
        return {
            dimension_id: policy.allowed_primary_modes
            for dimension_id, policy in self.dimension_policies.items()
        }

    @property
    def allowed_compatible_modes(self) -> dict[str, tuple[QuestionMode, ...]]:
        return {
            dimension_id: policy.allowed_compatible_modes
            for dimension_id, policy in self.dimension_policies.items()
        }

    @property
    def compatible_order(self) -> dict[str, tuple[QuestionMode, ...]]:
        return {
            dimension_id: policy.preferred_order
            for dimension_id, policy in self.dimension_policies.items()
        }

    def compatible_order_for(self, dimension_id: str) -> tuple[QuestionMode, ...]:
        try:
            return self.dimension_policies[dimension_id].preferred_order
        except KeyError as exc:
            raise ValueError(f"unsupported role dimension_id: {dimension_id}") from exc

    @property
    def mode_order(self) -> tuple[QuestionMode, ...]:
        return self.modes

    @property
    def preferred_order(self) -> dict[str, tuple[QuestionMode, ...]]:
        return self.compatible_order

    def validate_mode_assignment(
        self,
        dimension_id: str,
        primary_mode: QuestionMode,
        compatible_modes: Sequence[QuestionMode] | None = None,
    ) -> bool:
        """Validate one primary mode and its ordered compatible modes.

        The method returns ``True`` for callers that want a boolean guard and
        raises ``ValueError`` for every invalid assignment, keeping failures
        closed and deterministic.
        """

        if dimension_id not in self.dimension_policies:
            raise ValueError(f"unsupported role dimension_id: {dimension_id}")
        if primary_mode not in self.modes:
            raise ValueError(f"unsupported question mode: {primary_mode}")
        policy = self.dimension_policies[dimension_id]
        if primary_mode not in policy.allowed_primary_modes:
            raise ValueError(
                f"primary mode {primary_mode} is not allowed for {dimension_id}"
            )

        normalized_compatible = list(compatible_modes or ())
        if len(normalized_compatible) != len(set(normalized_compatible)):
            raise ValueError("compatible_modes must not contain duplicates")
        if primary_mode in normalized_compatible:
            raise ValueError("compatible_modes must not contain primary_mode")
        if any(mode not in policy.allowed_compatible_modes for mode in normalized_compatible):
            raise ValueError(
                f"compatible mode is not allowed for {dimension_id}"
            )
        expected_order = tuple(
            mode
            for mode in policy.preferred_order
            if mode in normalized_compatible
        )
        if tuple(normalized_compatible) != expected_order:
            raise ValueError(
                f"compatible_modes must follow the frozen preferred order for {dimension_id}"
            )
        return True


class QuestionCorpusQuotas(BaseModel):
    """Exact v2 corpus size and marginal quotas."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_count: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "question_count", "total_questions", "record_count"
        ),
    )
    dimension_quotas: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_DIMENSION_QUOTAS),
        validation_alias=AliasChoices("dimension_quotas", "dimension_counts"),
    )
    primary_mode_quotas: dict[QuestionMode, int] = Field(
        default_factory=lambda: dict(DEFAULT_PRIMARY_MODE_QUOTAS),
        validation_alias=AliasChoices("primary_mode_quotas", "mode_quotas"),
    )

    @model_validator(mode="after")
    def validate_exact_quotas(self) -> "QuestionCorpusQuotas":
        if self.question_count != 30:
            raise ValueError("question_count must be exactly 30")
        if self.dimension_quotas != DEFAULT_DIMENSION_QUOTAS:
            raise ValueError(
                "dimension_quotas must be role_dim_01..06 = 6/5/6/4/6/3"
            )
        if self.primary_mode_quotas != DEFAULT_PRIMARY_MODE_QUOTAS:
            raise ValueError(
                "primary_mode_quotas must be foundation/project_deep_dive/scenario/"
                "system_design/coding/follow_up = 4/5/8/4/3/6"
            )
        return self

    @property
    def mode_quotas(self) -> dict[QuestionMode, int]:
        return self.primary_mode_quotas

    @property
    def primary_mode_counts(self) -> dict[QuestionMode, int]:
        return self.primary_mode_quotas

    @property
    def dimension_counts(self) -> dict[str, int]:
        return self.dimension_quotas


def _validate_http_url(value: str, field_name: str = "canonical_url") -> str:
    value = _require_non_blank(value, field_name)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute http(s) URL")
    if parsed.fragment:
        raise ValueError(f"{field_name} must not contain a fragment")
    return value


def _validate_unique_strings(values: list[str], field_name: str) -> list[str]:
    for index, value in enumerate(values):
        _require_non_blank(value, f"{field_name}[{index}]")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


class QuestionBankManifest(BaseModel):
    """Global, exact-count publication gate for one question-bank snapshot."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = Field(default="2", min_length=1)
    bank_id: str = Field(min_length=1)
    role: Literal["ai_agent_engineer"] = CORPUS_ROLE
    role_version: Literal["2026-H2"] = CORPUS_ROLE_VERSION
    manifest_version: str = Field(min_length=1)
    question_count: int = Field(
        default=30,
        validation_alias=AliasChoices(
            "question_count", "total_questions", "record_count"
        ),
    )
    question_ids: list[str] = Field(min_length=30)
    dimension_quotas: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_DIMENSION_QUOTAS),
        validation_alias=AliasChoices("dimension_quotas", "dimension_counts"),
    )
    primary_mode_quotas: dict[QuestionMode, int] = Field(
        default_factory=lambda: dict(DEFAULT_PRIMARY_MODE_QUOTAS),
        validation_alias=AliasChoices("primary_mode_quotas", "mode_quotas"),
    )
    mode_policy_version: Literal["2026-H2"] = MODE_POLICY_VERSION
    min_independent_urls: int = 12
    max_questions_per_url: int = 3
    corpus_as_of: date = CORPUS_AS_OF
    signal_near_180_min_count: int = 18
    signal_near_365_min_count: int = 27
    signal_fallback_start: date = date(2025, 1, 1)
    signal_fallback_max_count: int = 3
    dynamic_review_days: int = 180
    evergreen_review_days: int = 365
    evergreen_revalidation_days: int = 180
    current_jd_validation_days: int = 180
    active_count: int = Field(default=0, ge=0, le=30)
    active_trust_levels: list[QuestionTrustLevel] = Field(
        default_factory=lambda: ["medium", "high"]
    )
    generated_at: date | None = Field(
        default=None,
        validation_alias=AliasChoices("generated_at", "created_at"),
    )
    reviewed_at: date | None = Field(
        default=None,
        validation_alias=AliasChoices("reviewed_at", "approved_at"),
    )
    published_at: date | None = None
    publication_status: QuestionPublicationStatus = Field(
        default="draft",
        validation_alias=AliasChoices("publication_status", "release_status"),
    )
    question_set_hash: str = Field(
        default="",
        validation_alias=AliasChoices("question_set_hash", "records_hash"),
    )
    sidecar_set_hash: str = Field(
        default="",
        validation_alias=AliasChoices("sidecar_set_hash", "sidecars_hash"),
    )
    embedding_contract_version: str = ""

    @field_validator("bank_id", "manifest_version", "mode_policy_version")
    @classmethod
    def validate_manifest_text(cls, value: str, info: Any) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("question_ids")
    @classmethod
    def validate_manifest_question_ids(cls, values: list[str]) -> list[str]:
        _validate_unique_strings(values, "question_ids")
        if len(values) != 30:
            raise ValueError("question_ids must contain exactly 30 IDs")
        return values

    @field_validator("question_set_hash", "sidecar_set_hash", "embedding_contract_version")
    @classmethod
    def validate_optional_manifest_text(cls, value: str, info: Any) -> str:
        if value:
            return _require_non_blank(value, info.field_name)
        return value

    @model_validator(mode="after")
    def validate_manifest_constraints(self) -> "QuestionBankManifest":
        if self.question_count != 30:
            raise ValueError("question_count must be exactly 30")
        if self.dimension_quotas != DEFAULT_DIMENSION_QUOTAS:
            raise ValueError("manifest dimension_quotas do not match the frozen quotas")
        if self.primary_mode_quotas != DEFAULT_PRIMARY_MODE_QUOTAS:
            raise ValueError(
                "manifest primary_mode_quotas do not match the frozen quotas"
            )
        if self.min_independent_urls != 12 or self.max_questions_per_url != 3:
            raise ValueError("manifest URL distribution limits are fixed at 12 and 3")
        if self.corpus_as_of != CORPUS_AS_OF:
            raise ValueError("corpus_as_of must be 2026-08-27")
        if self.signal_near_180_min_count != 18:
            raise ValueError("signal_near_180_min_count must be 18")
        if self.signal_near_365_min_count != 27:
            raise ValueError("signal_near_365_min_count must be 27")
        if self.signal_fallback_start != date(2025, 1, 1):
            raise ValueError("signal_fallback_start must be 2025-01-01")
        if self.signal_fallback_max_count != 3:
            raise ValueError("signal_fallback_max_count must be 3")
        if (
            self.dynamic_review_days != 180
            or self.evergreen_review_days != 365
            or self.evergreen_revalidation_days != 180
            or self.current_jd_validation_days != 180
        ):
            raise ValueError("manifest review windows do not match the fixed contract")
        if set(self.active_trust_levels) - {"medium", "high"}:
            raise ValueError("active_trust_levels may only contain medium and high")
        return self

    @property
    def mode_quotas(self) -> dict[QuestionMode, int]:
        return self.primary_mode_quotas


class QuestionSourceRegistryEntry(BaseModel):
    """One normalized, public source metadata record."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_id: str = Field(min_length=1)
    source_type: QuestionSourceType = Field(
        validation_alias=AliasChoices("source_type", "source_class")
    )
    canonical_url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    publisher: str = ""
    published_at: date | None = None
    verified_at: date
    accessed_at: date = Field(
        validation_alias=AliasChoices("accessed_at", "retrieved_at"),
    )
    trust: QuestionTrustLevel = Field(
        validation_alias=AliasChoices("trust", "trust_level")
    )
    lifecycle: QuestionSourceLifecycle = "draft"
    question_ids: list[str] = Field(default_factory=list)
    human_summary: str = ""
    review_class: QuestionReviewClass = "dynamic"
    date_basis: Literal["published_at", "retrieved_at"] = "published_at"
    role_level: str = ""
    dimension_ids: list[str] = Field(default_factory=list)
    access_status: Literal["accessible", "inaccessible", "unknown"] = "accessible"
    next_review_at: date | None = None
    rights_status: Literal["approved", "rejected", "pending"] = "pending"
    notes: str = ""

    @field_validator("source_id", "title", "publisher", "human_summary", "role_level", "notes")
    @classmethod
    def validate_source_text(cls, value: str, info: Any) -> str:
        if value:
            return _require_non_blank(value, info.field_name)
        return value

    @field_validator("canonical_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        return _validate_http_url(value)

    @field_validator("question_ids", "dimension_ids")
    @classmethod
    def validate_source_lists(cls, values: list[str], info: Any) -> list[str]:
        return _validate_unique_strings(values, info.field_name)

    @model_validator(mode="after")
    def validate_source_dates(self) -> "QuestionSourceRegistryEntry":
        if self.published_at is None and self.date_basis != "retrieved_at":
            raise ValueError(
                "date_basis=retrieved_at is required when published_at is missing"
            )
        if self.source_type == "public_interview_experience":
            if self.published_at is None:
                raise ValueError(
                    "public_interview_experience requires published_at"
                )
            if not date(2025, 1, 1) <= self.published_at <= CORPUS_AS_OF:
                raise ValueError(
                    "public interview published_at must be between "
                    "2025-01-01 and 2026-08-27"
                )
        elif self.published_at is not None and self.published_at > CORPUS_AS_OF:
            raise ValueError("published_at must not be after corpus_as_of")
        if self.published_at is not None and self.published_at > self.accessed_at:
            raise ValueError("published_at must not be after accessed_at")
        if self.accessed_at > self.verified_at:
            raise ValueError("accessed_at must not be after verified_at")
        if self.accessed_at > CORPUS_AS_OF:
            raise ValueError("accessed_at must not be after corpus_as_of")
        if self.verified_at > CORPUS_AS_OF:
            raise ValueError("verified_at must not be after corpus_as_of")
        return self

    @property
    def source_class(self) -> QuestionSourceType:
        return self.source_type

    @property
    def retrieved_at(self) -> date | None:
        return self.accessed_at


class QuestionSourceRegistry(BaseModel):
    """Source registry with a unique source_id primary key."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    entries: list[QuestionSourceRegistryEntry] = Field(
        default_factory=list,
        validation_alias=AliasChoices("entries", "sources"),
    )

    @model_validator(mode="after")
    def validate_unique_source_ids(self) -> "QuestionSourceRegistry":
        ids = [entry.source_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("source registry source_id values must be unique")
        return self

    @property
    def sources(self) -> list[QuestionSourceRegistryEntry]:
        return self.entries


class QuestionReviewRecord(BaseModel):
    """Human/agent review facts for one question."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_id: str = Field(min_length=1)
    decision: QuestionReviewDecision = Field(
        default="pending_human",
        validation_alias=AliasChoices("decision", "review_status"),
    )
    reviewer_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("reviewer_ids", "reviewers"),
    )
    reviewer: str | None = None
    reviewed_at: date
    signal_source_ids: list[str] = Field(default_factory=list)
    cross_validation_source_ids: list[str] = Field(default_factory=list)
    capability_summary: str = Field(
        default="",
        validation_alias=AliasChoices(
            "capability_summary", "signal_summary", "ability_summary"
        ),
    )
    business_constraint_summary: str = Field(
        default="",
        validation_alias=AliasChoices(
            "business_constraint_summary", "constraint_summary"
        ),
    )
    dimension_summary: str = ""
    mode_rationale: str = Field(
        default="",
        validation_alias=AliasChoices("mode_rationale", "mode_summary"),
    )
    originality_confirmed: bool = False
    pii_scan_passed: bool = False
    rights_review_passed: bool = False
    difficulty_consistent: bool = False
    pii_scan: bool | Literal["passed", "failed", "unknown"] | None = None
    rights_conclusion: str | None = None
    review_class: QuestionReviewClass = "dynamic"
    review_due_at: date | None = None
    exception_reason: str | None = None
    rejection_reason: str | None = None
    retirement_reason: str | None = None

    @field_validator("question_id")
    @classmethod
    def validate_review_question_id(cls, value: str) -> str:
        return _require_non_blank(value, "question_id")

    @field_validator("reviewer_ids", "signal_source_ids", "cross_validation_source_ids")
    @classmethod
    def validate_review_ids(cls, values: list[str], info: Any) -> list[str]:
        return _validate_unique_strings(values, info.field_name)

    @field_validator(
        "capability_summary",
        "business_constraint_summary",
        "dimension_summary",
        "mode_rationale",
        "exception_reason",
        "rejection_reason",
        "retirement_reason",
        "reviewer",
        "rights_conclusion",
    )
    @classmethod
    def validate_review_optional_text(cls, value: str | None, info: Any) -> str | None:
        if value is not None and value:
            return _require_non_blank(value, info.field_name)
        return value

    @model_validator(mode="after")
    def validate_approved_review(self) -> "QuestionReviewRecord":
        if self.decision != "approved":
            return self
        if not self.reviewer_ids and not (self.reviewer and self.reviewer.strip()):
            raise ValueError("approved review requires reviewer identity")
        if not self.signal_source_ids:
            raise ValueError("approved review requires signal evidence")
        if not self.cross_validation_source_ids:
            raise ValueError("approved review requires cross-validation evidence")
        pii_passed = self.pii_scan_passed or self.pii_scan == "passed"
        if "pii_scan_passed" in self.model_fields_set and not self.pii_scan_passed:
            pii_passed = False
        if self.pii_scan in {False, "failed", "unknown"}:
            pii_passed = False
        rights_passed = self.rights_review_passed or self.rights_conclusion in {
            "approved",
            "clear",
        }
        if (
            "rights_review_passed" in self.model_fields_set
            and not self.rights_review_passed
        ):
            rights_passed = False
        if self.rights_conclusion and self.rights_conclusion.lower() in {
            "rejected",
            "denied",
            "failed",
            "unknown",
        }:
            rights_passed = False
        if not self.originality_confirmed:
            raise ValueError("approved review requires originality confirmation")
        if not pii_passed:
            raise ValueError("approved review requires a passed PII check")
        if not rights_passed:
            raise ValueError("approved review requires a passed rights check")
        if not self.difficulty_consistent:
            raise ValueError("approved review requires difficulty consistency")
        return self


class QuestionReviewSidecar(BaseModel):
    """Review sidecar with one record per question_id."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    records: list[QuestionReviewRecord] = Field(
        default_factory=list,
        validation_alias=AliasChoices("records", "reviews"),
    )

    @model_validator(mode="after")
    def validate_unique_review_ids(self) -> "QuestionReviewSidecar":
        ids = [record.question_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("review question_id values must be unique")
        return self

    @property
    def reviews(self) -> list[QuestionReviewRecord]:
        return self.records


class QuestionDedupeRecord(BaseModel):
    """Exact/semantic duplicate decision for one question."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_id: str = Field(min_length=1)
    semantic_hash: str = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "semantic_hash", "normalized_semantic_hash", "content_hash"
        ),
    )
    comparison_batch: str = Field(min_length=1)
    candidate_duplicate_group: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "candidate_duplicate_group", "candidate_duplicate_ids", "duplicate_group_ids"
        ),
    )
    near_duplicate_decision: QuestionNearDuplicateDecision = "pending"
    decision: QuestionDedupeDecision = Field(
        default="pending",
        validation_alias=AliasChoices("decision", "resolution", "handling_decision"),
    )
    reviewed_at: date

    @field_validator("question_id", "semantic_hash", "comparison_batch")
    @classmethod
    def validate_dedupe_text(cls, value: str, info: Any) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("candidate_duplicate_group")
    @classmethod
    def validate_duplicate_group(cls, values: list[str]) -> list[str]:
        return _validate_unique_strings(values, "candidate_duplicate_group")

    @property
    def normalized_semantic_hash(self) -> str:
        return self.semantic_hash

    @property
    def content_hash(self) -> str:
        return self.semantic_hash


class QuestionDedupeSidecar(BaseModel):
    """Dedupe sidecar with one record per question_id."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    records: list[QuestionDedupeRecord] = Field(
        default_factory=list,
        validation_alias=AliasChoices("records", "dedupe_records"),
    )

    @model_validator(mode="after")
    def validate_unique_dedupe_ids(self) -> "QuestionDedupeSidecar":
        ids = [record.question_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("dedupe question_id values must be unique")
        return self


class QuestionRightsRecord(BaseModel):
    """Rights and safety decision for one question/source relation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    public_access: bool | Literal["allowed", "denied", "unknown", "public"] = False
    paraphrase_only: bool = False
    original_text_present: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "original_text_present", "contains_original_text"
        ),
    )
    answer_present: bool = Field(
        default=False,
        validation_alias=AliasChoices("answer_present", "contains_answer"),
    )
    no_pii: bool = Field(
        default=False,
        validation_alias=AliasChoices("no_pii", "pii_scan_passed"),
    )
    no_paid_content: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "no_paid_content", "paid_content_scan_passed"
        ),
    )
    contains_pii: bool | None = None
    contains_paid_content: bool | None = None
    originality_confirmed: bool = False
    decision: QuestionRightsDecision = Field(
        default="pending_human",
        validation_alias=AliasChoices("decision", "rights_status"),
    )
    reviewed_at: date
    notes: str = ""

    @field_validator("question_id", "source_id")
    @classmethod
    def validate_rights_ids(cls, value: str, info: Any) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("notes")
    @classmethod
    def validate_rights_notes(cls, value: str) -> str:
        return _require_non_blank(value, "notes") if value else value

    @model_validator(mode="after")
    def validate_approved_rights(self) -> "QuestionRightsRecord":
        allow_state = self.public_access is True or self.public_access in {
            "allowed",
            "public",
        }
        if self.decision != "approved" and not allow_state:
            return self
        public_access_allowed = allow_state
        if not public_access_allowed:
            raise ValueError("approved rights require public access")
        if not self.paraphrase_only:
            raise ValueError("approved rights require paraphrase_only")
        if self.original_text_present:
            raise ValueError("approved rights must not contain original text")
        if self.answer_present:
            raise ValueError("approved rights must not contain an answer")
        if not self.no_pii or self.contains_pii is True:
            raise ValueError("approved rights require no PII")
        if not self.no_paid_content or self.contains_paid_content is True:
            raise ValueError("approved rights require no paid content")
        if not self.originality_confirmed:
            raise ValueError("approved rights require originality confirmation")
        return self


class QuestionRightsSidecar(BaseModel):
    """Rights sidecar with a unique (question_id, source_id) key."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    records: list[QuestionRightsRecord] = Field(
        default_factory=list,
        validation_alias=AliasChoices("records", "rights_records"),
    )

    @model_validator(mode="after")
    def validate_unique_rights_keys(self) -> "QuestionRightsSidecar":
        keys = [(record.question_id, record.source_id) for record in self.records]
        if len(keys) != len(set(keys)):
            raise ValueError("rights question/source keys must be unique")
        return self


class QuestionLocatorRecord(BaseModel):
    """Minimal, non-quoting locator for a question/source relation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    section: str = ""
    heading: str = ""
    published_date: date | None = Field(
        default=None,
        validation_alias=AliasChoices("published_date", "date", "published_at"),
    )
    page: int | None = Field(default=None, ge=1)
    time_range: str = ""
    viewed_at: date = Field(
        validation_alias=AliasChoices("viewed_at", "accessed_at", "retrieved_at")
    )
    locator_hash: str = Field(
        min_length=1,
        validation_alias=AliasChoices("locator_hash", "locator_digest"),
    )

    @field_validator("question_id", "source_id")
    @classmethod
    def validate_locator_ids(cls, value: str, info: Any) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("canonical_url")
    @classmethod
    def validate_locator_url(cls, value: str) -> str:
        return _validate_http_url(value)

    @field_validator("section", "heading", "time_range", "locator_hash")
    @classmethod
    def validate_locator_text(cls, value: str, info: Any) -> str:
        if value:
            return _require_non_blank(value, info.field_name)
        return value

    @model_validator(mode="after")
    def validate_locator_reference(self) -> "QuestionLocatorRecord":
        if not any((self.section, self.heading, self.page, self.time_range, self.published_date)):
            raise ValueError("locator must contain at least one page location field")
        return self


class QuestionLocatorSidecar(BaseModel):
    """Locator sidecar with a unique (question_id, source_id) key."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    records: list[QuestionLocatorRecord] = Field(
        default_factory=list,
        validation_alias=AliasChoices("records", "locator_records"),
    )

    @model_validator(mode="after")
    def validate_unique_locator_keys(self) -> "QuestionLocatorSidecar":
        keys = [(record.question_id, record.source_id) for record in self.records]
        if len(keys) != len(set(keys)):
            raise ValueError("locator question/source keys must be unique")
        return self


class LabeledQuestionIntent(BaseModel):
    """One deterministic evaluation intent and its three label buckets."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    intent_id: str = Field(min_length=1)
    role: Literal["ai_agent_engineer"] = CORPUS_ROLE
    role_version: Literal["2026-H2"] = CORPUS_ROLE_VERSION
    dimension_id: QuestionDimensionId
    requested_mode: QuestionMode
    query_text: str = Field(min_length=1)
    gold_question_id: str = Field(min_length=1)
    acceptable_question_ids: list[str] = Field(default_factory=list)
    hard_negative_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("hard_negative_ids", "hard_negative_question_ids"),
    )
    label_notes: str = Field(min_length=1)

    @field_validator(
        "intent_id",
        "dimension_id",
        "query_text",
        "gold_question_id",
        "label_notes",
    )
    @classmethod
    def validate_intent_text(cls, value: str, info: Any) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("acceptable_question_ids", "hard_negative_ids")
    @classmethod
    def validate_intent_ids(cls, values: list[str], info: Any) -> list[str]:
        return _validate_unique_strings(values, info.field_name)


class QuestionCorpusSnapshot(BaseModel):
    """All v2 records and governance sidecars for one offline snapshot."""

    model_config = ConfigDict(extra="forbid")

    records: list[InterviewQuestionRecord]
    manifest: QuestionBankManifest
    source_registry: QuestionSourceRegistry
    review: QuestionReviewSidecar
    dedupe: QuestionDedupeSidecar
    rights: QuestionRightsSidecar
    locator: QuestionLocatorSidecar

    @model_validator(mode="after")
    def validate_snapshot_records(self) -> "QuestionCorpusSnapshot":
        if len(self.records) != 30:
            raise ValueError("snapshot must contain exactly 30 records")
        record_ids = [record.question_id for record in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("snapshot record question_id values must be unique")
        if set(record_ids) != set(self.manifest.question_ids):
            raise ValueError(
                "snapshot record question_ids must equal manifest.question_ids"
            )
        return self
