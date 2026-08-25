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
    DecisionSignal,
    EnterpriseAssessment,
    EvidenceExcerpt,
    HiringDecisionCode,
    RoleCompetencyProfile,
    ScoreReason,
    ScoreSnapshot,
)
from profile_agent.schemas.runtime_schema import Evidence, InterviewTurn
from profile_agent.services.role_profile_service import load_role_profile


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
    """Collect published gating critical-error text in stable order.

    ``ScoreSnapshot`` does not carry the Role Pack's ``is_gating`` flag.  The
    score engine therefore publishes gating critical errors through
    ``job_match.limiting_reasons``; critical-error reasons attached only to a
    radar or requirement remain ordinary risk evidence.
    """

    texts: list[str] = []
    texts.extend(
        reason.text
        for reason in snapshot.job_match.limiting_reasons
        if reason.reason_type == "critical_error"
    )
    return texts


def _gating_risk_dimension_ids(snapshot: ScoreSnapshot) -> list[str]:
    """Resolve only ScoreEngine-published gating limiting dimensions.

    Radar risk, L0 and critical-error reasons are not enough to establish a
    gating condition: the Role Pack's gating decision is represented in this
    contract by ``job_match.limiting_reasons``.
    """

    limiting_dimensions = _limiting_dimension_ids(snapshot)
    ordered_ids: list[str] = []
    for radar in snapshot.radar_dimensions:
        if radar.dimension_id in limiting_dimensions:
            ordered_ids.append(radar.dimension_id)
    for assessment in snapshot.requirement_assessments:
        if (
            assessment.dimension_id in limiting_dimensions
            and assessment.dimension_id not in ordered_ids
        ):
            ordered_ids.append(assessment.dimension_id)
    return ordered_ids


def _limiting_dimension_ids(snapshot: ScoreSnapshot) -> set[str]:
    """Resolve dimensions only from ``job_match.limiting_reasons`` provenance."""

    if not snapshot.job_match.limiting_reasons:
        return set()

    evidence_to_dimensions: dict[str, set[str]] = {}
    rubric_to_dimensions: dict[str, set[str]] = {}

    for radar in snapshot.radar_dimensions:
        for reason in radar.score_reasons:
            for evidence_id in reason.evidence_ids:
                evidence_to_dimensions.setdefault(evidence_id, set()).add(
                    radar.dimension_id
                )
            for rubric_id in reason.rubric_signal_ids:
                rubric_to_dimensions.setdefault(rubric_id, set()).add(
                    radar.dimension_id
                )

    for assessment in snapshot.requirement_assessments:
        for evidence_id in assessment.limiting_evidence_ids:
            evidence_to_dimensions.setdefault(evidence_id, set()).add(
                assessment.dimension_id
            )
        for reason in assessment.assessment_reasons:
            for evidence_id in reason.evidence_ids:
                evidence_to_dimensions.setdefault(evidence_id, set()).add(
                    assessment.dimension_id
                )
            for rubric_id in reason.rubric_signal_ids:
                rubric_to_dimensions.setdefault(rubric_id, set()).add(
                    assessment.dimension_id
                )

    dimension_ids: set[str] = set()
    for reason in snapshot.job_match.limiting_reasons:
        for radar in snapshot.radar_dimensions:
            if radar.dimension_id in reason.text:
                dimension_ids.add(radar.dimension_id)
        for assessment in snapshot.requirement_assessments:
            if assessment.dimension_id in reason.text:
                dimension_ids.add(assessment.dimension_id)
        for evidence_id in reason.evidence_ids:
            dimension_ids.update(evidence_to_dimensions.get(evidence_id, set()))
        for rubric_id in reason.rubric_signal_ids:
            dimension_ids.update(rubric_to_dimensions.get(rubric_id, set()))
    return dimension_ids


def _has_contradictory_claim(snapshot: ScoreSnapshot) -> bool:
    return any(
        claim.status == "contradictory"
        for claim in snapshot.claim_verifications
    )


def _risk_dimension_ids(snapshot: ScoreSnapshot) -> set[str]:
    """Return dimensions that are valid for report risk signal references."""

    dimension_ids: set[str] = set()
    evidence_to_dimensions: dict[str, set[str]] = {}
    rubric_to_dimensions: dict[str, set[str]] = {}

    def register(
        dimension_id: str,
        evidence_ids: Iterable[str],
        rubric_ids: Iterable[str],
    ) -> None:
        for evidence_id in evidence_ids:
            evidence_to_dimensions.setdefault(evidence_id, set()).add(
                dimension_id
            )
        for rubric_id in rubric_ids:
            rubric_to_dimensions.setdefault(rubric_id, set()).add(dimension_id)

    for radar in snapshot.radar_dimensions:
        for reason in radar.score_reasons:
            if reason.reason_type in {"risk", "critical_error"}:
                dimension_ids.add(radar.dimension_id)
                register(
                    radar.dimension_id,
                    reason.evidence_ids,
                    reason.rubric_signal_ids,
                )
        if radar.level == "L0":
            dimension_ids.add(radar.dimension_id)

    for assessment in snapshot.requirement_assessments:
        risk_reasons = [
            reason
            for reason in assessment.assessment_reasons
            if reason.reason_type in {"risk", "critical_error"}
        ]
        if (
            assessment.limiting_evidence_ids
            or assessment.unresolved_critical_error_ids
            or risk_reasons
        ):
            dimension_ids.add(assessment.dimension_id)
        register(
            assessment.dimension_id,
            assessment.limiting_evidence_ids
            + [
                evidence_id
                for reason in risk_reasons
                for evidence_id in reason.evidence_ids
            ],
            [
                rubric_id
                for reason in risk_reasons
                for rubric_id in reason.rubric_signal_ids
            ],
        )

    # A JobMatch limiting reason has no dimension field.  Resolve its
    # provenance through dimension-scoped evidence only when the score engine
    # actually published that limiting reason.
    dimension_ids.update(_limiting_dimension_ids(snapshot))

    # An UNVERIFIED dimension is an unknown, never a report risk.  Keep this
    # boundary here as well as in signal construction so the guard cannot
    # accept a risk signal merely because a raw reason happened to be attached
    # to an unverified radar dimension.
    return dimension_ids - set(_unverified_dimension_ids(snapshot))


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
    negative_prefixes = (
        "并非",
        "不是",
        "并不",
        "并未",
        "未",
        "尚未",
        "没有",
        "不代表",
        "not",
        "no",
    )
    for pattern in _ALL_VERIFIED_PATTERNS:
        for match in pattern.finditer(text):
            prefix = text[: match.start()].casefold().rstrip(" ，,、:：；;。.!！")
            if any(prefix.endswith(item) for item in negative_prefixes):
                continue
            return True
    return False


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
    return bool(_risk_dimension_ids(snapshot)) or _has_contradictory_claim(snapshot)


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
    allowed_unknown_dimensions = set(unverified_dimension_ids)
    if unverified_dimension_ids and not enterprise.unknowns:
        errors.append(
            "存在未验证维度时 enterprise_assessment.unknowns 不能为空"
        )
    for index, signal in enumerate(enterprise.unknowns):
        if not signal.dimension_ids:
            errors.append(f"unknowns[{index}] 必须关联至少一个未验证维度")
            continue
        invalid_unknowns = sorted(
            set(signal.dimension_ids) - allowed_unknown_dimensions
        )
        if invalid_unknowns:
            errors.append(
                f"unknowns[{index}] 引用了非未验证维度: "
                + ", ".join(invalid_unknowns)
            )

    if unverified_dimension_ids:
        if any(
            _contains_all_verified_claim(text)
            for text in _enterprise_texts(enterprise)
        ):
            errors.append("未验证维度不能被描述为全部能力均已验证")

    related_risk_dimensions = _risk_dimension_ids(snapshot)
    for index, signal in enumerate(enterprise.risks):
        invalid_risks = sorted(
            set(signal.dimension_ids) - related_risk_dimensions
        )
        if invalid_risks:
            errors.append(
                f"risks[{index}] 引用了无风险/限制证据的维度: "
                + ", ".join(invalid_risks)
            )

    if _has_report_risk(snapshot):
        if not enterprise.risks:
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
        answer = turn.answer or ""
        quote = excerpt.quote
        if not quote or not answer or quote not in answer:
            errors.append(
                "证据摘录 quote 不在其关联 InterviewTurn 的原回答中: "
                + excerpt.evidence_id
            )

    if errors:
        raise ReportConsistencyError("；".join(errors))


_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
_RISK_REASON_RANK = {"critical_error": 0, "risk": 1}
_DEFAULT_EVIDENCE_LIMITATION = "该证据只支持当前场景，迁移能力仍需独立验证。"
_INTERNAL_TEXT_ID = re.compile(
    r"\b(?:role_dim_\d+|req_[A-Za-z0-9_]+|"
    r"(?:d\d+_(?:min|exc|err|alt)_[A-Za-z0-9_]+)|"
    r"(?:ev(?:idence)?_[A-Za-z0-9_]+)|E\d+)\b"
)


def _normalise_evidences(
    evidences: Iterable[Evidence],
) -> list[Evidence]:
    try:
        return [Evidence.model_validate(evidence) for evidence in evidences]
    except (ValidationError, TypeError, ValueError) as exc:
        raise ReportConsistencyError("Evidence 不符合报告契约") from exc


def _normalise_profile(
    profile: RoleCompetencyProfile,
) -> RoleCompetencyProfile:
    try:
        return RoleCompetencyProfile.model_validate(profile)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ReportConsistencyError("RoleCompetencyProfile 不符合报告契约") from exc


def _profile_for_snapshot(snapshot: ScoreSnapshot) -> RoleCompetencyProfile:
    try:
        profile = load_role_profile(
            snapshot.role_family,
            snapshot.role_profile_version,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ReportConsistencyError(
            "无法加载 ScoreSnapshot 对应的 Role Pack"
        ) from exc
    return _normalise_profile(profile)


def _unique_index(
    items: Iterable[object],
    item_kind: str,
    *,
    id_field: str = "id",
) -> dict[str, object]:
    indexed: dict[str, object] = {}
    for item in items:
        item_id = getattr(item, id_field, None)
        if not isinstance(item_id, str) or not item_id.strip():
            raise ReportConsistencyError(f"{item_kind} 必须有非空 ID")
        if item_id in indexed:
            raise ReportConsistencyError(f"{item_kind} ID 重复: {item_id}")
        indexed[item_id] = item
    return indexed


def _profile_dimension_index(
    profile: RoleCompetencyProfile,
) -> dict[str, object]:
    return _unique_index(profile.dimensions, "Role Dimension")


def _reason_entries(
    snapshot: ScoreSnapshot,
) -> list[tuple[str | None, object | None, ScoreReason]]:
    """Return score reasons in stable, provenance-oriented order."""

    entries: list[tuple[str | None, object | None, ScoreReason]] = []
    for assessment in snapshot.requirement_assessments:
        for reason in assessment.assessment_reasons:
            entries.append((assessment.dimension_id, assessment, reason))
    for radar in snapshot.radar_dimensions:
        for reason in radar.score_reasons:
            entries.append((radar.dimension_id, None, reason))
    for reason in snapshot.job_match.limiting_reasons:
        entries.append((None, None, reason))
    return entries


def _evidence_contexts(
    snapshot: ScoreSnapshot,
) -> dict[str, list[tuple[str | None, object | None, ScoreReason]]]:
    contexts: dict[
        str,
        list[tuple[str | None, object | None, ScoreReason]],
    ] = {}
    for dimension_id, assessment, reason in _reason_entries(snapshot):
        for evidence_id in reason.evidence_ids:
            contexts.setdefault(evidence_id, []).append(
                (dimension_id, assessment, reason)
            )
    return contexts


def _evidence_assessment_matches(
    evidence: Evidence,
    snapshot: ScoreSnapshot,
    contexts: Iterable[tuple[str | None, object | None, ScoreReason]],
) -> list[object]:
    requirement_ids = set(evidence.requirement_ids)
    matches = [
        assessment
        for assessment in snapshot.requirement_assessments
        if assessment.requirement_id in requirement_ids
    ]
    if matches:
        return matches
    return [
        assessment
        for _, assessment, _ in contexts
        if assessment is not None and assessment not in matches
    ]


def _criterion_texts(
    profile: RoleCompetencyProfile,
    dimension_ids: Iterable[str | None],
    reasons: Iterable[ScoreReason],
) -> list[str]:
    dimensions = _profile_dimension_index(profile)
    ordered_dimensions = [
        dimensions[dimension_id]
        for dimension_id in dimension_ids
        if dimension_id in dimensions
    ]
    if not ordered_dimensions:
        ordered_dimensions = list(profile.dimensions)

    all_criteria_by_id: dict[str, str] = {}
    for dimension in ordered_dimensions:
        for criterion in (
            dimension.minimum_criteria
            + dimension.excellence_signals
            + dimension.critical_errors
            + dimension.accepted_alternatives
        ):
            all_criteria_by_id.setdefault(criterion.id, criterion.text)

    texts: list[str] = []
    for reason in reasons:
        for rubric_id in reason.rubric_signal_ids:
            criterion_text = all_criteria_by_id.get(rubric_id)
            if criterion_text and criterion_text not in texts:
                texts.append(criterion_text)

    if texts:
        return texts

    # A score reason may be evidence-backed without carrying a rubric ID
    # (for example, a hand-authored snapshot).  Keep the interpretation
    # enterprise-readable by using the dimension's minimum criterion text.
    for dimension in ordered_dimensions:
        for criterion in dimension.minimum_criteria:
            if criterion.text not in texts:
                texts.append(criterion.text)
    return texts


def _clean_enterprise_text(text: str) -> str:
    cleaned = _INTERNAL_TEXT_ID.sub("", text).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"[：:，,、；;]\s*[。.!！]", "。", cleaned)
    return cleaned.strip(" ，,、:：；;")


def _render_reason(
    reason: ScoreReason,
    *,
    criterion_texts: Iterable[str],
) -> str:
    readable_reason = _clean_enterprise_text(reason.text)
    criteria = [text for text in criterion_texts if text]
    if criteria:
        if readable_reason:
            return readable_reason + "（对应岗位要求：" + "；".join(criteria) + "）"
        return "对应岗位要求：" + "；".join(criteria)
    return readable_reason or "该结论来自已锁定的结构化证据。"


def _reason_conclusion(
    reason: ScoreReason,
    dimension_name: str | None,
) -> str:
    name = f"“{dimension_name}”" if dimension_name else "当前岗位判断"
    if reason.reason_type == "strength":
        return f"该证据支持{name}已有可验证的岗位相关表现。"
    if reason.reason_type == "critical_error":
        return f"该证据提示{name}存在需要重点关注的关键限制。"
    if reason.reason_type == "risk":
        return f"该证据限制{name}的当前判断。"
    return f"该证据与{name}相关，但尚不足以形成完整结论。"


def _reason_interpretation(
    profile: RoleCompetencyProfile,
    contexts: list[tuple[str | None, object | None, ScoreReason]],
) -> str:
    dimension_ids = [dimension_id for dimension_id, _, _ in contexts]
    reasons = [reason for _, _, reason in contexts]
    criterion_texts = _criterion_texts(profile, dimension_ids, reasons)
    if criterion_texts:
        return "该片段与岗位要求的对应点包括：" + "；".join(criterion_texts) + "。"
    dimensions = _profile_dimension_index(profile)
    names = [
        dimensions[dimension_id].name
        for dimension_id in dimension_ids
        if dimension_id in dimensions
    ]
    if names:
        return "该片段与“" + "、".join(dict.fromkeys(names)) + "”相关。"
    return "该片段是当前岗位判断的可追溯证据。"


def _reason_limitation(
    profile: RoleCompetencyProfile,
    assessments: Iterable[object],
) -> str:
    dimensions = _profile_dimension_index(profile)
    unmet_texts: list[str] = []
    for assessment in assessments:
        dimension_id = getattr(assessment, "dimension_id", None)
        dimension = dimensions.get(dimension_id)
        if dimension is None:
            continue
        if getattr(assessment, "accepted_alternative_ids", []):
            continue
        satisfied_ids = set(
            getattr(assessment, "satisfied_minimum_criterion_ids", [])
        )
        for criterion in dimension.minimum_criteria:
            if criterion.id not in satisfied_ids and criterion.text not in unmet_texts:
                unmet_texts.append(criterion.text)
    if unmet_texts:
        return "尚未验证：" + "；".join(unmet_texts) + "。"
    return _DEFAULT_EVIDENCE_LIMITATION


def build_evidence_excerpts(
    snapshot: ScoreSnapshot,
    evidences: Iterable[Evidence],
    turns: Iterable[InterviewTurn],
) -> list[EvidenceExcerpt]:
    """Build short, auditable excerpts from score-reason evidence only.

    The answer itself is never used as a fallback quote.  An excerpt is
    published only when the original ``Evidence.source_excerpt`` is non-empty
    and is an exact substring of its linked interview answer.
    """

    snapshot = _normalise_snapshot(snapshot)
    normalized_evidences = _normalise_evidences(evidences)
    normalized_turns = _normalise_turns(turns)
    evidences_by_id = _unique_index(normalized_evidences, "Evidence")
    turns_by_id = _unique_index(normalized_turns, "InterviewTurn")
    profile = _profile_for_snapshot(snapshot)
    contexts_by_evidence = _evidence_contexts(snapshot)

    ordered_evidence_ids: list[str] = []
    for _, _, reason in _reason_entries(snapshot):
        for evidence_id in reason.evidence_ids:
            if evidence_id not in ordered_evidence_ids:
                ordered_evidence_ids.append(evidence_id)

    excerpts: list[EvidenceExcerpt] = []
    dimensions = _profile_dimension_index(profile)
    for evidence_id in ordered_evidence_ids:
        evidence = evidences_by_id.get(evidence_id)
        contexts = contexts_by_evidence.get(evidence_id, [])
        if evidence is None or not contexts:
            continue
        turn = turns_by_id.get(evidence.turn_id)
        if turn is None:
            continue
        quote = evidence.source_excerpt
        answer = turn.answer or ""
        if (
            not quote
            or not quote.strip()
            or not answer
            or quote == answer
            or quote not in answer
        ):
            continue

        dimension_name = next(
            (
                dimensions[dimension_id].name
                for dimension_id, _, _ in contexts
                if dimension_id in dimensions
            ),
            None,
        )
        reasons = [reason for _, _, reason in contexts]
        first_reason = reasons[0]
        assessments = _evidence_assessment_matches(evidence, snapshot, contexts)
        excerpts.append(
            EvidenceExcerpt(
                evidence_id=evidence.id,
                turn_id=turn.id,
                conclusion=_reason_conclusion(first_reason, dimension_name),
                quote=quote,
                interpretation=_reason_interpretation(profile, contexts),
                limitation=_reason_limitation(profile, assessments),
            )
        )
    return excerpts


def _dimension_reasons(
    snapshot: ScoreSnapshot,
) -> dict[str, list[ScoreReason]]:
    reasons_by_dimension: dict[str, list[ScoreReason]] = {}
    for radar in snapshot.radar_dimensions:
        reasons_by_dimension.setdefault(radar.dimension_id, []).extend(
            radar.score_reasons
        )
    for assessment in snapshot.requirement_assessments:
        reasons_by_dimension.setdefault(assessment.dimension_id, []).extend(
            assessment.assessment_reasons
        )
    return reasons_by_dimension


def _dimension_evidence_ids(
    snapshot: ScoreSnapshot,
) -> dict[str, list[str]]:
    evidence_by_dimension: dict[str, list[str]] = {}

    def add(dimension_id: str, evidence_ids: Iterable[str]) -> None:
        values = evidence_by_dimension.setdefault(dimension_id, [])
        for evidence_id in evidence_ids:
            if evidence_id not in values:
                values.append(evidence_id)

    for radar in snapshot.radar_dimensions:
        for reason in radar.score_reasons:
            add(radar.dimension_id, reason.evidence_ids)
    for assessment in snapshot.requirement_assessments:
        add(
            assessment.dimension_id,
            assessment.supporting_evidence_ids
            + assessment.limiting_evidence_ids
            + assessment.transfer_evidence_ids,
        )
        for reason in assessment.assessment_reasons:
            add(assessment.dimension_id, reason.evidence_ids)
    return evidence_by_dimension


def _signal_text(
    profile: RoleCompetencyProfile,
    dimension_id: str | None,
    reasons: list[ScoreReason],
    *,
    fallback: str | None = None,
) -> str:
    dimensions = _profile_dimension_index(profile)
    dimension = dimensions.get(dimension_id) if dimension_id else None
    rendered: list[str] = []
    for reason in reasons:
        criteria = _criterion_texts(profile, [dimension_id], [reason])
        text = _render_reason(reason, criterion_texts=criteria)
        if text not in rendered:
            rendered.append(text)
    body = "；".join(rendered)
    if body:
        if reason_type := reasons[0].reason_type:
            if reason_type == "critical_error":
                prefix = "关键风险："
            elif reason_type == "risk":
                prefix = "限制信号："
            elif reason_type == "strength":
                prefix = "已验证表现："
            else:
                prefix = "观察结论："
            body = prefix + body
    if body:
        return body
    if fallback:
        return fallback
    if dimension is not None:
        return f"“{dimension.name}”当前有待补充可验证证据。"
    return "当前岗位判断存在待核验限制。"


def build_decision_signals(
    snapshot: ScoreSnapshot,
    profile: RoleCompetencyProfile,
) -> tuple[list[DecisionSignal], list[DecisionSignal], list[DecisionSignal]]:
    """Select bounded enterprise signals from the frozen score snapshot."""

    snapshot = _normalise_snapshot(snapshot)
    profile = _normalise_profile(profile)
    if (
        snapshot.role_family != profile.role_family
        or snapshot.role_profile_version != profile.version
    ):
        raise ReportConsistencyError(
            "ScoreSnapshot 与 RoleCompetencyProfile 的版本或岗位不一致"
        )

    dimensions = _profile_dimension_index(profile)
    radar_by_id = _unique_index(
        snapshot.radar_dimensions,
        "Radar Dimension",
        id_field="dimension_id",
    )
    reasons_by_dimension = _dimension_reasons(snapshot)
    evidence_by_dimension = _dimension_evidence_ids(snapshot)
    dimension_order = {
        dimension.id: index for index, dimension in enumerate(profile.dimensions)
    }

    strength_candidates: list[tuple[tuple[int, float, int], str, list[ScoreReason]]] = []
    for dimension_id, radar_object in radar_by_id.items():
        radar = radar_object
        if radar.level == "UNVERIFIED" or radar.score is None:
            continue
        strength_reasons = [
            reason
            for reason in reasons_by_dimension.get(dimension_id, [])
            if reason.reason_type == "strength" and reason.evidence_ids
        ]
        if not strength_reasons:
            continue
        strength_candidates.append(
            (
                (
                    -_CONFIDENCE_RANK[radar.confidence],
                    -radar.score,
                    dimension_order.get(dimension_id, len(dimension_order)),
                ),
                dimension_id,
                strength_reasons,
            )
        )
    strength_candidates.sort(key=lambda item: item[0])
    strengths: list[DecisionSignal] = []
    for _, dimension_id, reasons in strength_candidates[:3]:
        radar = radar_by_id[dimension_id]
        evidence_ids = list(evidence_by_dimension.get(dimension_id, []))
        evidence_ids = [
            evidence_id
            for reason in reasons
            for evidence_id in reason.evidence_ids
            if evidence_id not in evidence_ids
        ] + evidence_ids
        strengths.append(
            DecisionSignal(
                title=dimensions.get(dimension_id, radar).name,
                text=_signal_text(profile, dimension_id, reasons),
                dimension_ids=[dimension_id],
                evidence_ids=list(dict.fromkeys(evidence_ids)),
                confidence=radar.confidence,
            )
        )

    risk_candidates: dict[
        str | None,
        tuple[int, list[ScoreReason], list[str]],
    ] = {}

    def add_risk(
        dimension_id: str | None,
        reason_rank: int,
        reason: ScoreReason | None,
        evidence_ids: Iterable[str] = (),
    ) -> None:
        current = risk_candidates.get(dimension_id)
        if current is None:
            risk_candidates[dimension_id] = (
                reason_rank,
                [reason] if reason is not None else [],
                list(dict.fromkeys(evidence_ids)),
            )
            return
        current_rank, current_reasons, current_evidence_ids = current
        if reason is not None:
            current_reasons.append(reason)
        for evidence_id in evidence_ids:
            if evidence_id not in current_evidence_ids:
                current_evidence_ids.append(evidence_id)
        risk_candidates[dimension_id] = (
            min(current_rank, reason_rank),
            current_reasons,
            current_evidence_ids,
        )

    for dimension_id, reasons in reasons_by_dimension.items():
        radar = radar_by_id.get(dimension_id)
        if radar is not None and radar.level == "UNVERIFIED":
            continue
        for reason in reasons:
            reason_rank = _RISK_REASON_RANK.get(reason.reason_type)
            if reason_rank is not None:
                add_risk(dimension_id, reason_rank, reason, reason.evidence_ids)
    for assessment in snapshot.requirement_assessments:
        radar = radar_by_id.get(assessment.dimension_id)
        if (
            radar is not None
            and radar.level == "UNVERIFIED"
        ):
            continue
        if (
            assessment.limiting_evidence_ids
            and assessment.dimension_id not in risk_candidates
        ):
            add_risk(
                assessment.dimension_id,
                _RISK_REASON_RANK["risk"],
                ScoreReason(
                    reason_type="risk",
                    text="存在带有明确限制的岗位证据。",
                    evidence_ids=list(assessment.limiting_evidence_ids),
                ),
                assessment.limiting_evidence_ids,
            )

    evidence_dimensions: dict[str, list[str]] = {}
    for dimension_id, evidence_ids in evidence_by_dimension.items():
        for evidence_id in evidence_ids:
            evidence_dimensions.setdefault(evidence_id, []).append(dimension_id)
    for reason in snapshot.job_match.limiting_reasons:
        reason_rank = _RISK_REASON_RANK.get(reason.reason_type)
        if reason_rank is None:
            continue
        linked_dimensions: list[str] = []
        linked_unverified = False
        for evidence_id in reason.evidence_ids:
            for dimension_id in evidence_dimensions.get(evidence_id, []):
                radar = radar_by_id.get(dimension_id)
                if radar is not None and radar.level == "UNVERIFIED":
                    linked_unverified = True
                elif dimension_id not in linked_dimensions:
                    linked_dimensions.append(dimension_id)
        if not linked_dimensions:
            for dimension in profile.dimensions:
                rubric_matches_dimension = any(
                    rubric_id
                    in {
                        criterion.id
                        for criterion in (
                            dimension.minimum_criteria
                            + dimension.excellence_signals
                            + dimension.critical_errors
                            + dimension.accepted_alternatives
                        )
                    }
                    for rubric_id in reason.rubric_signal_ids
                )
                if not rubric_matches_dimension:
                    continue
                radar = radar_by_id.get(dimension.id)
                if radar is not None and radar.level == "UNVERIFIED":
                    linked_unverified = True
                else:
                    linked_dimensions.append(dimension.id)
        if linked_dimensions:
            for dimension_id in linked_dimensions:
                add_risk(dimension_id, reason_rank, reason, reason.evidence_ids)
        elif not linked_unverified:
            add_risk(None, reason_rank, reason, reason.evidence_ids)

    for dimension in profile.dimensions:
        radar = radar_by_id.get(dimension.id)
        if radar is None or radar.level == "UNVERIFIED" or radar.score is None:
            continue
        low_score = radar.level in {"L0", "L1"} or radar.score < 60
        if dimension.is_gating and low_score and dimension.id not in risk_candidates:
            add_risk(
                dimension.id,
                2,
                None,
                (),
            )

    ordered_risks = sorted(
        risk_candidates.items(),
        key=lambda item: (
            item[1][0],
            dimension_order.get(item[0], len(dimension_order)),
            -(
                radar_by_id[item[0]].score
                if item[0] is not None and radar_by_id[item[0]].score is not None
                else -1
            ),
        ),
    )
    risks: list[DecisionSignal] = []
    for dimension_id, (reason_rank, reasons, candidate_evidence_ids) in ordered_risks[:3]:
        radar = radar_by_id.get(dimension_id) if dimension_id else None
        dimension = dimensions.get(dimension_id) if dimension_id else None
        evidence_ids = list(candidate_evidence_ids)
        for reason in reasons:
            for evidence_id in reason.evidence_ids:
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)
        risks.append(
            DecisionSignal(
                title=dimension.name if dimension is not None else "岗位匹配限制",
                text=_signal_text(
                    profile,
                    dimension_id,
                    reasons,
                    fallback=(
                        f"门槛维度“{dimension.name}”当前评分较低，需要重点核验。"
                        if dimension is not None and reason_rank == 2
                        else None
                    ),
                ),
                dimension_ids=[dimension_id] if dimension_id else [],
                evidence_ids=evidence_ids,
                confidence=(
                    radar.confidence
                    if radar is not None
                    else snapshot.job_match.confidence
                ),
            )
        )

    unknown_candidates: list[tuple[tuple[int, float, int], str]] = []
    for dimension_id, radar_object in radar_by_id.items():
        radar = radar_object
        if radar.level != "UNVERIFIED":
            continue
        dimension = dimensions.get(dimension_id)
        if dimension is None:
            continue
        unknown_candidates.append(
            (
                (
                    0 if dimension.is_gating else 1,
                    -dimension.weight,
                    dimension_order.get(dimension_id, len(dimension_order)),
                ),
                dimension_id,
            )
        )
    unknown_candidates.sort(key=lambda item: item[0])
    unknowns: list[DecisionSignal] = []
    for _, dimension_id in unknown_candidates[:2]:
        dimension = dimensions[dimension_id]
        radar = radar_by_id[dimension_id]
        unknowns.append(
            DecisionSignal(
                title=dimension.name,
                text="该能力维度尚未形成足够的面试证据，当前状态为未验证。",
                dimension_ids=[dimension_id],
                evidence_ids=[],
                confidence=radar.confidence,
            )
        )

    return strengths, risks, unknowns


def select_reinterview_dimensions(
    snapshot: ScoreSnapshot,
    profile: RoleCompetencyProfile,
) -> list[str]:
    """Select at most three dimensions that need a focused re-interview.

    The score snapshot is already immutable, so this ranking is deliberately
    deterministic.  A dimension with a critical/limiting signal wins over an
    otherwise similar gap; unknowns and lower scores then break ties before
    the Role Pack weight and its stable display order.
    """

    snapshot = _normalise_snapshot(snapshot)
    profile = _normalise_profile(profile)
    if (
        snapshot.role_family != profile.role_family
        or snapshot.role_profile_version != profile.version
    ):
        raise ReportConsistencyError(
            "ScoreSnapshot 与 RoleCompetencyProfile 的版本或岗位不一致"
        )

    dimensions = _profile_dimension_index(profile)
    radar_by_id = _unique_index(
        snapshot.radar_dimensions,
        "Radar Dimension",
        id_field="dimension_id",
    )
    reasons_by_dimension = _dimension_reasons(snapshot)
    assessments_by_dimension: dict[str, list[object]] = {}
    for assessment in snapshot.requirement_assessments:
        assessments_by_dimension.setdefault(assessment.dimension_id, []).append(
            assessment
        )
    limiting_dimension_ids = _limiting_dimension_ids(snapshot)

    # A JobMatch limiting reason has no dimension field.  Only count it as a
    # gating limiter when its provenance identifies one dimension directly,
    # through one rubric criterion, or through evidence unique to one
    # dimension.  Shared evidence across several dimensions is not proof.
    evidence_to_dimensions: dict[str, set[str]] = {}
    for radar in snapshot.radar_dimensions:
        for reason in radar.score_reasons:
            for evidence_id in reason.evidence_ids:
                evidence_to_dimensions.setdefault(evidence_id, set()).add(
                    radar.dimension_id
                )
    for assessment in snapshot.requirement_assessments:
        for evidence_id in (
            assessment.supporting_evidence_ids
            + assessment.limiting_evidence_ids
            + assessment.transfer_evidence_ids
        ):
            evidence_to_dimensions.setdefault(evidence_id, set()).add(
                assessment.dimension_id
            )
        for reason in assessment.assessment_reasons:
            for evidence_id in reason.evidence_ids:
                evidence_to_dimensions.setdefault(evidence_id, set()).add(
                    assessment.dimension_id
                )
    rubric_to_dimensions: dict[str, set[str]] = {}
    for dimension in profile.dimensions:
        for criterion in (
            dimension.minimum_criteria
            + dimension.excellence_signals
            + dimension.critical_errors
            + dimension.accepted_alternatives
        ):
            rubric_to_dimensions.setdefault(criterion.id, set()).add(
                dimension.id
            )
    proven_gating_limiting_ids: set[str] = set()
    for reason in snapshot.job_match.limiting_reasons:
        for dimension in profile.dimensions:
            if dimension.id in reason.text:
                proven_gating_limiting_ids.add(dimension.id)
        for rubric_id in reason.rubric_signal_ids:
            dimensions_for_rubric = rubric_to_dimensions.get(rubric_id, set())
            if len(dimensions_for_rubric) == 1:
                proven_gating_limiting_ids.update(dimensions_for_rubric)
        for evidence_id in reason.evidence_ids:
            dimensions_for_evidence = evidence_to_dimensions.get(evidence_id, set())
            if len(dimensions_for_evidence) == 1:
                proven_gating_limiting_ids.update(dimensions_for_evidence)

    candidates: list[tuple[tuple[int, int, int, float, float], str, int]] = []
    for display_order, dimension in enumerate(profile.dimensions):
        dimension_id = dimension.id
        radar = radar_by_id.get(dimension_id)
        reasons = reasons_by_dimension.get(dimension_id, [])
        assessments = assessments_by_dimension.get(dimension_id, [])
        has_limiting_reason = (
            dimension_id in limiting_dimension_ids
            or any(
                reason.reason_type in {"critical_error", "risk"}
                for reason in reasons
            )
            or any(
                bool(
                    getattr(assessment, "limiting_evidence_ids", [])
                    or getattr(assessment, "unresolved_critical_error_ids", [])
                )
                for assessment in assessments
            )
        )
        has_gating_critical_or_limiting = dimension.is_gating and (
            dimension_id in proven_gating_limiting_ids
            or any(
                reason.reason_type in {"critical_error", "risk"}
                for reason in reasons
            )
            or any(
                bool(
                    getattr(assessment, "unresolved_critical_error_ids", [])
                    or getattr(assessment, "limiting_evidence_ids", [])
                )
                for assessment in assessments
            )
        )
        score = getattr(radar, "score", None) if radar is not None else None
        confidence = getattr(radar, "confidence", "low")
        is_unverified = (
            radar is None
            or getattr(radar, "level", "UNVERIFIED") == "UNVERIFIED"
            or score is None
        )

        # A strong, high-confidence dimension with no limiting signal is not a
        # re-interview gap, even when another dimension has a lower score.
        if (
            radar is not None
            and confidence == "high"
            and score is not None
            and score >= 80
            and not has_limiting_reason
        ):
            continue

        priority = (
            int(has_gating_critical_or_limiting),
            int(dimension.is_gating and confidence == "low"),
            int(is_unverified),
            100 - float(score or 0),
            dimension.weight,
        )
        candidates.append((priority, dimension_id, display_order))

    candidates.sort(key=lambda item: (item[0], -item[2]), reverse=True)
    return [dimension_id for _, dimension_id, _ in candidates[:3]]


__all__ = [
    "HiringDecisionDraft",
    "ReportConsistencyError",
    "build_decision_signals",
    "build_evidence_excerpts",
    "derive_hiring_decision",
    "select_reinterview_dimensions",
    "validate_enterprise_assessment",
]
