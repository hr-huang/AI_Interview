from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import InterviewPlan, QuestionMode
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
    required_question_modes: dict[str, list[QuestionMode]] = Field(
        default_factory=dict
    )
    required_limiting_evidence_ids: dict[str, list[str]] = Field(
        default_factory=dict
    )

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
        level_ids = set(self.requirement_level_ranges)
        unverified_ids = set(self.expected_unverified_requirements)
        state_conflicts = sorted(level_ids & unverified_ids)
        if state_conflicts:
            raise ValueError(
                "Requirement 不能同时要求数值等级和 UNVERIFIED: "
                f"{state_conflicts}"
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


class ScriptedAnswerRule(CalibrationModel):
    id: str
    match_any: list[str]
    answer: str
    max_uses: int = Field(default=1, gt=0)

    @field_validator("id", "answer")
    @classmethod
    def validate_nonempty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value

    @field_validator("match_any")
    @classmethod
    def validate_match_terms(cls, value: list[str]) -> list[str]:
        terms = [term.strip() for term in value]
        if not terms or any(not term for term in terms):
            raise ValueError("match_any 必须包含至少一个非空语义词")
        if len(terms) != len(set(terms)):
            raise ValueError("match_any 不能包含重复语义词")
        return terms


class InterviewPathExpectation(CalibrationModel):
    required_topics: dict[str, list[str]] = Field(default_factory=dict)
    forbidden_repeated_topics: list[str] = Field(default_factory=list)
    radar_level_ranges: dict[str, LevelRange] = Field(default_factory=dict)
    expected_unverified_dimensions: list[str] = Field(default_factory=list)
    required_critical_dimensions: list[str] = Field(default_factory=list)
    job_match_published: bool | None = None
    max_questions: int = Field(gt=0)

    @field_validator("required_topics")
    @classmethod
    def validate_required_topics(
        cls,
        value: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        topics: dict[str, list[str]] = {}
        for raw_topic, raw_terms in value.items():
            topic = raw_topic.strip()
            terms = [term.strip() for term in raw_terms]
            if not topic or not terms or any(not term for term in terms):
                raise ValueError("required_topics 必须使用非空主题和语义词")
            if topic in topics:
                raise ValueError(f"required_topics 主题重复: {topic}")
            topics[topic] = terms
        return topics

    @field_validator(
        "forbidden_repeated_topics",
        "expected_unverified_dimensions",
        "required_critical_dimensions",
    )
    @classmethod
    def validate_nonempty_unique_list(cls, value: list[str]) -> list[str]:
        items = [item.strip() for item in value]
        if any(not item for item in items):
            raise ValueError("列表项不能为空")
        if len(items) != len(set(items)):
            raise ValueError("列表项不能重复")
        return items

    @model_validator(mode="after")
    def reject_topic_overlap(self) -> "InterviewPathExpectation":
        overlap = sorted(
            set(self.required_topics) & set(self.forbidden_repeated_topics)
        )
        if overlap:
            raise ValueError(f"required/forbidden path topic 冲突: {overlap}")
        return self


class InterviewCalibrationCase(CalibrationModel):
    id: str
    title: str
    resume_text: str
    jd_text: str
    target_role: str
    answer_rules: list[ScriptedAnswerRule] = Field(min_length=1)
    path_expectation: InterviewPathExpectation

    @field_validator("id", "title", "resume_text", "jd_text", "target_role")
    @classmethod
    def validate_nonempty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value

    @model_validator(mode="after")
    def reject_duplicate_rule_ids(self) -> "InterviewCalibrationCase":
        rule_ids = [rule.id for rule in self.answer_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("ScriptedAnswerRule ID 不能重复")
        return self


class InterviewCalibrationRun(CalibrationModel):
    case_id: str
    run_number: int = Field(gt=0)
    initial_state: dict[str, Any]
    final_state: dict[str, Any]
    selected_rule_ids: list[str]
    assertions: list[CalibrationAssertion]

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("case_id 不能为空")
        return value

    @field_validator("selected_rule_ids")
    @classmethod
    def validate_selected_rule_ids(cls, value: list[str]) -> list[str]:
        rule_ids = [rule_id.strip() for rule_id in value]
        if any(not rule_id for rule_id in rule_ids):
            raise ValueError("selected_rule_ids 不能包含空值")
        return rule_ids

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.assertions)
