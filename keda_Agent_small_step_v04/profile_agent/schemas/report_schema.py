"""Evidence-driven assessment report contracts.

These models deliberately keep final scoring separate from the runtime
``RequirementAssessment`` used by the interview loop.  The report pipeline
uses ``RequirementEvidenceAssessment`` as its evidence-backed intermediate
snapshot and stores numeric results in ``RequirementScore`` only.
"""

from __future__ import annotations

from datetime import date
import math
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from profile_agent.schemas.interview_schema import QuestionMode


ScoreLevel = Literal["UNVERIFIED", "L0", "L1", "L2", "L3", "L4"]
ConfidenceLevel = Literal["low", "medium", "high"]
QualityLevel = Literal["unverified", "weak", "medium", "strong"]
FitLevel = Literal[
    "高度匹配",
    "较高匹配",
    "有条件匹配",
    "当前匹配度较低",
    "存在明显岗位风险",
]


class _ReportModel(BaseModel):
    """Shared strict configuration for every public report model."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RubricCriterion(_ReportModel):
    id: str
    text: str
    score_adjustment: int = Field(default=0, ge=-5, le=5)


class CompetencyDimensionRubric(_ReportModel):
    id: str
    name: str
    weight: float = Field(gt=0, le=1)
    is_gating: bool
    minimum_criteria: list[RubricCriterion] = Field(min_length=1)
    excellence_signals: list[RubricCriterion] = Field(default_factory=list)
    critical_errors: list[RubricCriterion] = Field(default_factory=list)
    accepted_alternatives: list[RubricCriterion] = Field(default_factory=list)


class RoleCompetencyProfile(_ReportModel):
    role_family: str
    display_name: str
    version: str
    valid_from: date
    knowledge_as_of: date
    dimensions: list[CompetencyDimensionRubric] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dimensions(self) -> "RoleCompetencyProfile":
        dimension_ids = [dimension.id for dimension in self.dimensions]
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValueError("Role competency dimension IDs must be unique")

        total_weight = sum(dimension.weight for dimension in self.dimensions)
        if not math.isclose(total_weight, 1.0, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError(
                "Role competency dimension weights must sum to 1.0"
            )
        return self


class RequirementBindingDraft(_ReportModel):
    requirement_id: str
    primary_dimension_id: str
    rubric_id: str


class ScoringBlueprintDraft(_ReportModel):
    bindings: list[RequirementBindingDraft] = Field(default_factory=list)


class RequirementScoringBinding(_ReportModel):
    requirement_id: str
    primary_dimension_id: str
    weight_within_dimension: float = Field(gt=0, le=1)
    rubric_id: str


class ScoringBlueprint(_ReportModel):
    role_family: str
    role_profile_version: str
    bindings: list[RequirementScoringBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_binding_ids(self) -> "ScoringBlueprint":
        requirement_ids = [binding.requirement_id for binding in self.bindings]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("Scoring blueprint requirement IDs must be unique")
        return self


class RubricQuality(_ReportModel):
    correctness: QualityLevel = "unverified"
    specificity: QualityLevel = "unverified"
    reasoning: QualityLevel = "unverified"
    tradeoff_awareness: QualityLevel = "unverified"
    transferability: QualityLevel = "unverified"


class RubricMatch(_ReportModel):
    evidence_id: str
    requirement_id: str
    matched_minimum_criteria: list[str] = Field(default_factory=list)
    matched_excellence_signals: list[str] = Field(default_factory=list)
    matched_critical_errors: list[str] = Field(default_factory=list)
    accepted_alternative_ids: list[str] = Field(default_factory=list)
    quality: RubricQuality


class RubricMatchBatch(_ReportModel):
    matches: list[RubricMatch] = Field(default_factory=list)


class ScoreReason(_ReportModel):
    reason_type: Literal["strength", "risk", "unverified", "critical_error"] = (
        Field(validation_alias=AliasChoices("reason_type", "kind"))
    )
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    rubric_signal_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("rubric_signal_ids", "rubric_ids"),
    )

    @model_validator(mode="after")
    def validate_evidence_requirement(self) -> "ScoreReason":
        if self.reason_type in {"strength", "risk", "critical_error"}:
            if not self.evidence_ids:
                raise ValueError(
                    f"{self.reason_type} score reasons require evidence IDs"
                )
        return self

    @property
    def kind(self) -> str:
        """Compatibility accessor for the design document's short label."""

        return self.reason_type

    @property
    def rubric_ids(self) -> list[str]:
        """Compatibility accessor for the design document's short label."""

        return self.rubric_signal_ids


class RequirementEvidenceAssessment(_ReportModel):
    requirement_id: str
    dimension_id: str
    level: ScoreLevel
    coverage: float = Field(ge=0, le=1)
    confidence: ConfidenceLevel
    satisfied_minimum_criterion_ids: list[str] = Field(default_factory=list)
    matched_excellence_signal_ids: list[str] = Field(default_factory=list)
    unresolved_critical_error_ids: list[str] = Field(default_factory=list)
    accepted_alternative_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    limiting_evidence_ids: list[str] = Field(default_factory=list)
    transfer_evidence_ids: list[str] = Field(default_factory=list)
    quality: RubricQuality
    assessment_reasons: list[ScoreReason] = Field(default_factory=list)


class RequirementScore(_ReportModel):
    requirement_id: str
    dimension_id: str
    base_score: int = Field(ge=0, le=100)
    adjustment: int = Field(ge=-5, le=5)
    display_score: int = Field(ge=0, le=100)


class RadarDimensionResult(_ReportModel):
    dimension_id: str
    name: str
    score: float | None = Field(default=None, ge=0, le=100)
    level: ScoreLevel
    coverage: float = Field(ge=0, le=1)
    confidence: ConfidenceLevel
    score_reasons: list[ScoreReason] = Field(default_factory=list)
    requirement_breakdown: list[RequirementScore] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scored_dimension_reasons(self) -> "RadarDimensionResult":
        if self.level == "UNVERIFIED":
            if self.score is not None:
                raise ValueError("UNVERIFIED radar dimensions cannot have a score")
        else:
            if self.score is None:
                raise ValueError("Scored radar dimensions require a score")
            if len(self.score_reasons) < 2:
                raise ValueError(
                    "Scored radar dimensions require at least two score reasons"
                )
        return self


class JobMatchResult(_ReportModel):
    raw_score: float | None = Field(default=None, ge=0, le=100)
    published: bool
    fit_level: FitLevel | None = None
    coverage: float = Field(ge=0, le=1)
    confidence: ConfidenceLevel
    limiting_reasons: list[ScoreReason] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_publication_state(self) -> "JobMatchResult":
        if self.published:
            if self.raw_score is None or self.fit_level is None:
                raise ValueError(
                    "Published job matches require raw_score and fit_level"
                )
        elif self.raw_score is not None or self.fit_level is not None:
            raise ValueError(
                "Unpublished job matches cannot expose raw_score or fit_level"
            )
        return self


class ClaimVerification(_ReportModel):
    claim_id: str
    status: Literal[
        "supported",
        "partially_supported",
        "insufficient",
        "contradictory",
        "unverified",
    ]
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


class ScoreSnapshot(_ReportModel):
    role_family: str
    role_profile_version: str
    scoring_engine_version: str
    requirement_assessments: list[RequirementEvidenceAssessment] = Field(
        default_factory=list
    )
    requirement_scores: list[RequirementScore] = Field(default_factory=list)
    radar_dimensions: list[RadarDimensionResult] = Field(default_factory=list)
    job_match: JobMatchResult
    claim_verifications: list[ClaimVerification] = Field(default_factory=list)


class NarrativeItem(_ReportModel):
    text: str
    dimension_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class DevelopmentAction(_ReportModel):
    dimension_id: str
    current_gap: str
    actions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class ReportNarrativeDraft(_ReportModel):
    executive_summary: str
    strengths: list[NarrativeItem] = Field(default_factory=list)
    risks: list[NarrativeItem] = Field(default_factory=list)
    unverified_areas: list[NarrativeItem] = Field(default_factory=list)
    fit_contexts: list[NarrativeItem] = Field(default_factory=list)
    development_actions: list[DevelopmentAction] = Field(default_factory=list)


class InterviewPathStep(_ReportModel):
    turn_id: str
    question_mode: QuestionMode
    requirement_id: str
    outcome: str
    evidence_ids: list[str] = Field(default_factory=list)


class AssessmentReport(_ReportModel):
    target_role: str
    score_snapshot: ScoreSnapshot
    narrative: ReportNarrativeDraft
    interview_path: list[InterviewPathStep] = Field(default_factory=list)
    assessment_limitations: list[str] = Field(default_factory=list)
