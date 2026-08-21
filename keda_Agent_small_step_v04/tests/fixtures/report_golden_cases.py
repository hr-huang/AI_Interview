"""Deterministic golden inputs for the Requirement evidence assessment stage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterator

from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
    InterviewPlan,
)
from profile_agent.schemas.report_schema import (
    CompetencyDimensionRubric,
    RequirementScoringBinding,
    RoleCompetencyProfile,
    RubricCriterion,
    RubricMatch,
    RubricMatchBatch,
    RubricQuality,
    ScoringBlueprint,
)
from profile_agent.schemas.runtime_schema import Evidence, InterviewTurn


@dataclass(frozen=True)
class GoldenCase:
    """The complete structured input consumed by the assessment builder."""

    role_profile: RoleCompetencyProfile
    blueprint: ScoringBlueprint
    matches: RubricMatchBatch
    evidences: list[Evidence]
    turns: list[InterviewTurn]

    def __iter__(self) -> Iterator[object]:
        yield self.role_profile
        yield self.blueprint
        yield self.matches
        yield self.evidences
        yield self.turns


def _criterion(criterion_id: str, text: str) -> RubricCriterion:
    return RubricCriterion(id=criterion_id, text=text)


def _quality(
    *,
    correctness: str = "strong",
    specificity: str = "strong",
    reasoning: str = "strong",
    tradeoff_awareness: str = "strong",
    transferability: str = "unverified",
) -> RubricQuality:
    return RubricQuality(
        correctness=correctness,
        specificity=specificity,
        reasoning=reasoning,
        tradeoff_awareness=tradeoff_awareness,
        transferability=transferability,
    )


def _profile() -> RoleCompetencyProfile:
    return RoleCompetencyProfile(
        role_family="ai_application_engineering",
        display_name="AI Agent / AI应用工程师",
        version="2026-H2",
        valid_from=date(2026, 7, 1),
        knowledge_as_of=date(2026, 8, 21),
        dimensions=[
            CompetencyDimensionRubric(
                id="role_dim_01",
                name="AI应用与Agent编排",
                weight=1.0,
                is_gating=True,
                minimum_criteria=[
                    _criterion("min_01", "拆分状态、节点与工具边界"),
                    _criterion("min_02", "解释状态流转与人工介入"),
                ],
                excellence_signals=[
                    _criterion("exc_01", "比较单 Agent、Workflow 与多 Agent"),
                    _criterion("exc_02", "将方法迁移到新场景并优化"),
                ],
                critical_errors=[
                    _criterion("err_01", "无差别拆成多 Agent"),
                ],
                accepted_alternatives=[
                    _criterion("alt_01", "用等价状态机方案满足边界要求"),
                ],
            )
        ],
        source_refs=["golden-fixture"],
    )


def _blueprint(*requirement_ids: str) -> ScoringBlueprint:
    ids = requirement_ids or ("req_01",)
    return ScoringBlueprint(
        role_family="ai_application_engineering",
        role_profile_version="2026-H2",
        bindings=[
            RequirementScoringBinding(
                requirement_id=requirement_id,
                primary_dimension_id="role_dim_01",
                weight_within_dimension=1.0 / len(ids),
                rubric_id="role_dim_01",
            )
            for requirement_id in ids
        ],
    )


def _plan() -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=10,
        closing_buffer_minutes=2,
        targets=[
            AssessmentTarget(
                id="target_01",
                objective="验证候选人设计可靠 AI Workflow 的能力",
                target_type="system_design",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id="req_01",
                        description="能够拆分状态、节点和工具边界",
                    )
                ],
                related_claim_ids=[],
                priority="high",
                must_cover=True,
                time_budget_minutes=20,
                preferred_modes=["system_design", "follow_up", "scenario"],
            )
        ],
    )


def _turn(
    turn_id: str,
    sequence_number: int,
    question_mode: str,
    *,
    requirement_id: str = "req_01",
) -> InterviewTurn:
    timestamp = datetime(2026, 8, 21, 12, sequence_number, tzinfo=timezone.utc)
    return InterviewTurn(
        id=turn_id,
        sequence_number=sequence_number,
        target_id="target_01",
        primary_requirement_id=requirement_id,
        question_mode=question_mode,
        question="请说明你的设计和取舍。",
        answer="候选人的结构化回答。",
        asked_at=timestamp,
        answered_at=timestamp,
    )


def _evidence(
    evidence_id: str,
    turn_id: str,
    *,
    polarity: str = "supporting",
    strength: str = "strong",
    observation: str = "候选人给出了与问题相关的结构化回答。",
    source_excerpt: str = "我会先定义边界，再验证每条路径。",
    requirement_ids: list[str] | None = None,
) -> Evidence:
    return Evidence(
        id=evidence_id,
        turn_id=turn_id,
        requirement_ids=requirement_ids or ["req_01"],
        polarity=polarity,
        strength=strength,
        observation=observation,
        source_excerpt=source_excerpt,
    )


def _match(
    evidence_id: str,
    *,
    minimum: list[str] | None = None,
    excellence: list[str] | None = None,
    critical: list[str] | None = None,
    alternatives: list[str] | None = None,
    quality: RubricQuality | None = None,
    requirement_id: str = "req_01",
) -> RubricMatch:
    return RubricMatch(
        evidence_id=evidence_id,
        requirement_id=requirement_id,
        matched_minimum_criteria=minimum or [],
        matched_excellence_signals=excellence or [],
        matched_critical_errors=critical or [],
        accepted_alternative_ids=alternatives or [],
        quality=quality or _quality(),
    )


def _case(
    *,
    turns: list[InterviewTurn],
    evidences: list[Evidence],
    matches: list[RubricMatch],
) -> GoldenCase:
    return GoldenCase(
        role_profile=_profile(),
        blueprint=_blueprint(),
        matches=RubricMatchBatch(matches=matches),
        evidences=evidences,
        turns=turns,
    )


def make_deep_but_non_exhaustive_case() -> GoldenCase:
    """Both minimum criteria and deep quality are shown, but not every signal."""

    turns = [_turn("turn_01", 1, "system_design"), _turn("turn_02", 2, "follow_up")]
    evidences = [
        _evidence("ev_01", "turn_01", source_excerpt="我会拆分状态、节点和工具边界。"),
        _evidence("ev_02", "turn_02", source_excerpt="失败时我会保留人工介入和恢复路径。"),
    ]
    matches = [
        _match("ev_01", minimum=["min_01"], excellence=["exc_01"]),
        _match("ev_02", minimum=["min_02"]),
    ]
    return _case(turns=turns, evidences=evidences, matches=matches)


def make_keyword_only_case() -> GoldenCase:
    """Relevant terminology is present, but no minimum criterion is established."""

    turns = [_turn("turn_01", 1, "foundation")]
    evidences = [
        _evidence(
            "ev_keyword",
            "turn_01",
            strength="medium",
            observation="回答提到了 Agent、Workflow 和工具等关键词。",
            source_excerpt="Agent、Workflow、工具。",
        )
    ]
    matches = [
        _match(
            "ev_keyword",
            excellence=["exc_01"],
            quality=_quality(
                correctness="medium",
                specificity="weak",
                reasoning="weak",
                tradeoff_awareness="weak",
            ),
        )
    ]
    return _case(turns=turns, evidences=evidences, matches=matches)


def make_unverified_case() -> GoldenCase:
    """The interview has an Evidence object, but no validated rubric hit."""

    turns = [_turn("turn_01", 1, "scenario")]
    evidences = [
        _evidence(
            "ev_unverified",
            "turn_01",
            observation="回答没有形成可核验的 rubric 事实。",
            source_excerpt="我还没有想清楚。",
        )
    ]
    return _case(turns=turns, evidences=evidences, matches=[])


def make_critical_safety_error_case() -> GoldenCase:
    """An explicit medium-strength safety error is a limiting match."""

    turns = [_turn("turn_01", 1, "scenario")]
    evidences = [
        _evidence(
            "ev_error",
            "turn_01",
            polarity="contradicting",
            strength="medium",
            observation="模型未经人工确认就直接触发了高风险工具。",
            source_excerpt="让模型直接执行高风险操作。",
        )
    ]
    matches = [_match("ev_error", critical=["err_01"])]
    return _case(turns=turns, evidences=evidences, matches=matches)


def make_conflicting_transfer_case() -> GoldenCase:
    """Strong project support is retained beside a contradictory migration answer."""

    turns = [
        _turn("turn_project", 1, "project_deep_dive"),
        _turn("turn_migration", 2, "scenario"),
    ]
    evidences = [
        _evidence(
            "ev_project",
            "turn_project",
            observation="候选人在已有项目中完整说明了状态边界和恢复路径。",
            source_excerpt="项目中我把状态、节点和工具边界拆开。",
        ),
        _evidence(
            "ev_migration",
            "turn_migration",
            polarity="contradicting",
            strength="medium",
            observation="迁移到新场景时，候选人又主张不加校验地复用旧流程。",
            source_excerpt="新场景也直接复用原来的流程，不需要再验证。",
        ),
    ]
    matches = [
        _match(
            "ev_project",
            minimum=["min_01", "min_02"],
            excellence=["exc_01"],
            quality=_quality(transferability="unverified"),
        ),
        _match(
            "ev_migration",
            minimum=["min_01"],
            quality=_quality(
                correctness="medium",
                specificity="medium",
                reasoning="medium",
                tradeoff_awareness="medium",
                transferability="strong",
            ),
        ),
    ]
    return _case(turns=turns, evidences=evidences, matches=matches)
