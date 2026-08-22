from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.schemas.report_schema import (
    AssessmentReport,
    RubricMatchBatch,
    ScoringBlueprint,
)
from profile_agent.schemas.runtime_schema import Evidence, InterviewRuntimeState, InterviewTurn


Level = Literal["L0", "L1", "L2", "L3", "L4"]
_LEVEL_ORDER = {
    level: index for index, level in enumerate(("L0", "L1", "L2", "L3", "L4"))
}


class CalibrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LevelRange(CalibrationModel):
    min_level: Level
    max_level: Level

    @model_validator(mode="after")
    def validate_order(self) -> "LevelRange":
        if _LEVEL_ORDER[self.min_level] > _LEVEL_ORDER[self.max_level]:
            raise ValueError("min_level 不能高于 max_level")
        return self


class ReportCalibrationExpectation(CalibrationModel):
    required_rubric_hits: dict[str, list[str]] = Field(default_factory=dict)
    forbidden_rubric_hits: dict[str, list[str]] = Field(default_factory=dict)
    requirement_level_ranges: dict[str, LevelRange] = Field(default_factory=dict)
    expected_unverified_requirements: list[str] = Field(default_factory=list)
    expected_unverified_dimensions: list[str] = Field(default_factory=list)
    job_match_published: bool | None = None
    required_claim_statuses: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_conflicting_hits(self) -> "ReportCalibrationExpectation":
        for requirement_id, required in self.required_rubric_hits.items():
            conflict = set(required) & set(
                self.forbidden_rubric_hits.get(requirement_id, [])
            )
            if conflict:
                raise ValueError(
                    f"required/forbidden rubric hit 冲突: {sorted(conflict)}"
                )
        return self


class ReportCalibrationCase(CalibrationModel):
    id: str
    title: str
    description: str
    target_role: str
    plan: InterviewPlan
    runtime_state: InterviewRuntimeState
    turns: list[InterviewTurn]
    evidences: list[Evidence]
    claim_registry: ClaimRegistry
    expectation: ReportCalibrationExpectation


class CalibrationAssertion(CalibrationModel):
    code: str
    passed: bool
    message: str


class ReportCalibrationRun(CalibrationModel):
    case_id: str
    run_number: int
    blueprint: ScoringBlueprint
    rubric_matches: RubricMatchBatch
    report: AssessmentReport
    assertions: list[CalibrationAssertion]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.assertions)
