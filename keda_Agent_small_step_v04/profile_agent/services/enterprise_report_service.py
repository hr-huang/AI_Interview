"""Deterministic enterprise hiring decisions and report consistency checks.

This module is intentionally independent from the language-model boundary.  A
``ScoreSnapshot`` is already a frozen scoring result, so this layer only
projects its publication state into a hiring decision and rejects prose or
report fields that contradict that result.
"""

from __future__ import annotations

from collections.abc import Iterable
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from profile_agent.schemas.report_schema import (
    ConfidenceLevel,
    EnterpriseAssessment,
    HiringDecisionCode,
    ScoreReason,
    ScoreSnapshot,
)
from profile_agent.schemas.runtime_schema import InterviewTurn


_DECISION_LABELS: dict[HiringDecisionCode, str] = {
    "PROCEED": "建议进入结构化复试",
    "CONDITIONAL_PROCEED": "有条件进入结构化复试",
    "INSUFFICIENT_EVIDENCE": "证据不足，暂缓岗位判断",
    "NOT_RECOMMENDED": "当前证据不支持推进",
}


class HiringDecisionDraft(BaseModel):
    """The deterministic decision projection used by the report assembler."""

    model_config = ConfigDict(extra="forbid")

    code: HiringDecisionCode
    decision_label: str
    provisional_score: float | None = Field(default=None, ge=0, le=100)
    confidence: ConfidenceLevel
    conditions: list[str] = Field(default_factory=list, max_length=3)
    decision_reasons: list[str] = Field(min_length=1, max_length=3)
    unknown_dimension_ids: list[str] = Field(default_factory=list)

    @property
    def decision(self) -> HiringDecisionCode:
        """Compatibility alias for callers that use the report field name."""

        return self.code


class ReportConsistencyError(ValueError):
    """Raised when an enterprise report contradicts frozen assessment facts."""


def _normalise_snapshot(snapshot: ScoreSnapshot) -> ScoreSnapshot:
    try:
        return ScoreSnapshot.model_validate(snapshot)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ReportConsistencyError("ScoreSnapshot 不符合报告契约") from exc


def _normalise_enterprise(enterprise: EnterpriseAssessment) -> EnterpriseAssessment:
    try:
        return EnterpriseAssessment.model_validate(enterprise)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ReportConsistencyError("EnterpriseAssessment 不符合报告契约") from exc


def _normalise_turns(turns: Iterable[InterviewTurn]) -> list[InterviewTurn]:
    try:
        normalised = [InterviewTurn.model_validate(turn) for turn in turns]
    except (ValidationError, TypeError, ValueError) as exc:
        raise ReportConsistencyError("InterviewTurn 不符合报告契约") from exc
    return normalised


def _unverified_dimension_ids(snapshot: ScoreSnapshot) -> list[str]:
    """Return unverified radar IDs in the snapshot's stable display order."""

    result: list[str] = []
    seen: set[str] = set()
    for radar in snapshot.radar_dimensions:
        if radar.level == "UNVERIFIED" and radar.dimension_id not in seen:
            result.append(radar.dimension_id)
            seen.add(radar.dimension_id)
    return result


def _reason_text(reason: ScoreReason) -> str:
    return reason.text.strip()


def _critical_error_texts(snapshot: ScoreSnapshot) -> list[str]:
    """Collect critical-error text in stable order without fabricating IDs."""

    texts: list[str] = []
    for radar in snapshot.radar_dimensions:
        texts.extend(
            reason.text
            for reason in radar.score_reasons
            if reason.reason_type == "critical_error"
        )
    for assessment in snapshot.requirement_assessments:
        texts.extend(
            reason.text
            for reason in assessment.assessment_reasons
            if reason.reason_type == "critical_error"
        )
        if assessment.unresolved_critical_error_ids and not any(
            reason.reason_type == "critical_error"
            for reason in assessment.assessment_reasons
        ):
            texts.append(
                f"维度“{assessment.dimension_id}”存在未解除的关键错误。"
            )
    texts.extend(
        reason.text
        for reason in snapshot.job_match.limiting_reasons
        if reason.reason_type == "critical_error"
    )
    return texts


def _gating_risk_dimension_ids(snapshot: ScoreSnapshot) -> list[str]:
    """Find dimensions carrying a low-level or explicit risk signal."""

    dimension_ids: list[str] = []
    seen: set[str] = set()

    def add(dimension_id: str) -> None:
        if dimension_id not in seen:
            dimension_ids.append(dimension_id)
            seen.add(dimension_id)

    for radar in snapshot.radar_dimensions:
        has_risk_reason = any(
            reason.reason_type in {"risk", "critical_error"}
            for reason in radar.score_reasons
        )
        if radar.level == "L0" or has_risk_reason:
            add(radar.dimension_id)

    for assessment in snapshot.requirement_assessments:
        has_risk_reason = any(
            reason.reason_type in {"risk", "critical_error"}
            for reason in assessment.assessment_reasons
        )
        if assessment.unresolved_critical_error_ids or has_risk_reason:
            add(assessment.dimension_id)

    return dimension_ids


def _has_contradictory_claim(snapshot: ScoreSnapshot) -> bool:
    return any(
        claim.status == "contradictory"
        for claim in snapshot.claim_verifications
    )


def _append_unique(values: list[str], value: str, *, limit: int = 3) -> None:
    value = value.strip()
    if value and value not in values and len(values) < limit:
        values.append(value)


def derive_hiring_decision(snapshot: ScoreSnapshot) -> HiringDecisionDraft:
    """Derive a hiring decision using only the frozen score snapshot.

    Publication state is checked first.  A published critical error or an
    explicit score-engine risk is the sole negative path; an unverified
    dimension by itself remains conditional rather than negative.
    """

    snapshot = _normalise_snapshot(snapshot)
    job_match = snapshot.job_match
    unknown_dimension_ids = _unverified_dimension_ids(snapshot)
    critical_error_texts = _critical_error_texts(snapshot)
    gating_risk_dimension_ids = _gating_risk_dimension_ids(snapshot)

    if not job_match.published:
        code: HiringDecisionCode = "INSUFFICIENT_EVIDENCE"
    elif job_match.fit_level == "存在明显岗位风险" or critical_error_texts:
        code = "NOT_RECOMMENDED"
    elif job_match.confidence == "low" or unknown_dimension_ids:
        code = "CONDITIONAL_PROCEED"
    elif gating_risk_dimension_ids or job_match.limiting_reasons:
        code = "CONDITIONAL_PROCEED"
    else:
        code = "PROCEED"

    reasons: list[str] = []
    conditions: list[str] = []
    if code == "INSUFFICIENT_EVIDENCE":
        _append_unique(
            reasons,
            "岗位匹配证据尚未达到发布条件，暂不输出数值岗位结论。",
        )
        _append_unique(
            conditions,
            "补充岗位门槛证据后再作岗位判断。",
        )
        for limiting_reason in job_match.limiting_reasons:
            _append_unique(reasons, _reason_text(limiting_reason))
    elif code == "NOT_RECOMMENDED":
        if job_match.fit_level == "存在明显岗位风险":
            _append_unique(
                reasons,
                "评分引擎适配等级为“存在明显岗位风险”。",
            )
        for reason_text in critical_error_texts:
            _append_unique(reasons, reason_text)
        _append_unique(conditions, "当前证据不支持推进。")
    else:
        if unknown_dimension_ids:
            _append_unique(
                reasons,
                "尚有未验证能力维度：" + "、".join(unknown_dimension_ids) + "。",
            )
            _append_unique(
                conditions,
                "补充验证未验证能力维度："
                + "、".join(unknown_dimension_ids)
                + "。",
            )
        if job_match.confidence == "low":
            _append_unique(reasons, "整体岗位匹配置信度为 low。")
            _append_unique(conditions, "低置信度结论需经结构化复试复核。")
        if gating_risk_dimension_ids:
            _append_unique(
                reasons,
                "存在低等级或风险能力维度："
                + "、".join(gating_risk_dimension_ids)
                + "。",
            )
            _append_unique(conditions, "复试中核验门槛维度的限制证据。")
        for limiting_reason in job_match.limiting_reasons:
            _append_unique(reasons, _reason_text(limiting_reason))
            _append_unique(conditions, "复试中核验岗位限制证据。")
        if code == "PROCEED":
            _append_unique(
                reasons,
                "岗位匹配分已发布且当前没有门槛限制证据。",
            )

    # The schema requires at least one reason.  Every branch above appends one,
    # but the fallback keeps this invariant explicit if rules evolve.
    if not reasons:
        reasons.append("已依据冻结评分快照形成确定性岗位判断。")

    return HiringDecisionDraft(
        code=code,
        decision_label=_DECISION_LABELS[code],
        provisional_score=job_match.raw_score if job_match.published else None,
        confidence=job_match.confidence,
        conditions=conditions[:3],
        decision_reasons=reasons[:3],
        unknown_dimension_ids=unknown_dimension_ids,
    )


_ALL_VERIFIED_PATTERNS = (
    re.compile(
        r"(?:全部|六项|六个)(?:的)?(?:能力|维度)?"
        r"(?:均|都|全部)?(?:已|已经)?"
        r"(?:形成证据|得到验证|已验证|验证|覆盖)"
    ),
    re.compile(
        r"所有(?:的)?(?:能力|维度)(?:均|都|全部)?(?:已|已经)?"
        r"(?:形成证据|得到验证|已验证|验证|覆盖)"
    ),
    re.compile(
        r"全维度(?:均|都|全部)?(?:已|已经)?"
        r"(?:形成证据|得到验证|已验证|验证|覆盖)"
    ),
    re.compile(
        r"\ball\s+(?:six\s+)?(?:dimensions|competencies|abilities)"
        r"(?:\s+are)?\s+(?:fully\s+)?"
        r"(?:verified|validated|assessed|covered)\b",
        re.IGNORECASE,
    ),
)


def _contains_all_verified_claim(text: str) -> bool:
    return any(pattern.search(text) for pattern in _ALL_VERIFIED_PATTERNS)


def _enterprise_texts(enterprise: EnterpriseAssessment) -> Iterable[str]:
    yield enterprise.overall_assessment
    yield from enterprise.conditions
    yield from enterprise.decision_reasons
    for signal in (*enterprise.strengths, *enterprise.risks, *enterprise.unknowns):
        yield signal.title
        yield signal.text
    for excerpt in enterprise.evidence_excerpts:
        yield excerpt.conclusion
        yield excerpt.interpretation
        yield excerpt.limitation


def _has_report_risk(snapshot: ScoreSnapshot) -> bool:
    if snapshot.job_match.limiting_reasons:
        return True
    if _gating_risk_dimension_ids(snapshot):
        return True
    return _has_contradictory_claim(snapshot)


def validate_enterprise_assessment(
    enterprise: EnterpriseAssessment,
    snapshot: ScoreSnapshot,
    turns: Iterable[InterviewTurn],
) -> None:
    """Reject enterprise report fields that contradict frozen report facts."""

    enterprise = _normalise_enterprise(enterprise)
    snapshot = _normalise_snapshot(snapshot)
    turns = _normalise_turns(turns)
    errors: list[str] = []

    unverified_dimension_ids = _unverified_dimension_ids(snapshot)
    if unverified_dimension_ids:
        if not enterprise.unknowns:
            errors.append(
                "存在未验证维度时 enterprise_assessment.unknowns 不能为空"
            )
        if any(
            _contains_all_verified_claim(text)
            for text in _enterprise_texts(enterprise)
        ):
            errors.append("未验证维度不能被描述为全部能力均已验证")

    if _has_report_risk(snapshot) and not enterprise.risks:
        errors.append("存在限制或矛盾证据时 enterprise_assessment.risks 不能为空")

    if enterprise.decision == "PROCEED" and (
        enterprise.confidence == "low"
        or snapshot.job_match.confidence == "low"
    ):
        errors.append("低置信度时不能输出 PROCEED")

    if not snapshot.job_match.published and enterprise.provisional_score is not None:
        errors.append("岗位匹配分未发布时不能暴露 provisional_score")

    priorities = [focus.priority for focus in enterprise.reinterview_plan]
    dimension_ids = [focus.dimension_id for focus in enterprise.reinterview_plan]
    if len(enterprise.reinterview_plan) > 3:
        errors.append("reinterview_plan 不能超过三个重点")
    if len(priorities) != len(set(priorities)):
        errors.append("reinterview_plan 的 priority 必须唯一")
    if len(dimension_ids) != len(set(dimension_ids)):
        errors.append("reinterview_plan 的 dimension_id 不能重复")

    turns_by_id: dict[str, InterviewTurn] = {}
    for turn in turns:
        if turn.id in turns_by_id:
            errors.append(f"InterviewTurn ID 重复: {turn.id}")
        turns_by_id[turn.id] = turn

    for excerpt in enterprise.evidence_excerpts:
        turn = turns_by_id.get(excerpt.turn_id)
        if turn is None:
            errors.append(f"证据摘录引用了不存在的 turn_id: {excerpt.turn_id}")
            continue
        answer = (turn.answer or "").strip()
        quote = excerpt.quote.strip()
        if not quote or not answer or quote not in answer:
            errors.append(
                "证据摘录 quote 不在其关联 InterviewTurn 的原回答中: "
                + excerpt.evidence_id
            )

    if errors:
        raise ReportConsistencyError("；".join(errors))


__all__ = [
    "HiringDecisionDraft",
    "ReportConsistencyError",
    "derive_hiring_decision",
    "validate_enterprise_assessment",
]
