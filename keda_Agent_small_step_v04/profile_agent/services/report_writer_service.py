"""Write an evidence-grounded narrative for an immutable score snapshot.

The writer is deliberately a narrow boundary around the LLM.  Scores,
levels, coverage and confidence already belong to ``ScoreSnapshot``; the LLM
may only fill the prose-shaped ``ReportNarrativeDraft``.  Every citation is
checked against the evidence provenance recorded by the snapshot before the
draft is returned.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ValidationError

from profile_agent.llm import llm
from profile_agent.schemas.report_schema import (
    DevelopmentAction,
    NarrativeItem,
    RadarDimensionResult,
    ReportNarrativeDraft,
    RoleCompetencyProfile,
    ScoreReason,
    ScoreSnapshot,
)
from profile_agent.schemas.runtime_schema import Evidence


class GroundingValidationError(ValueError):
    """Raised when a narrative is not grounded in the supplied snapshot."""


_SYSTEM_PROMPT = """
你是 Evidence-driven Assessment Report 的 Report Writer。

你的唯一任务是根据已经锁定的 ScoreSnapshot、其证据引用和版本化 Role
Profile 生成 ReportNarrativeDraft。LLM 只负责自然语言表达，不能重新评分、
改变等级、覆盖率、置信度或岗位匹配结果。

严格约束：
- 只输出 ReportNarrativeDraft 的字段；不要新增 score、level、fit_level、
  coverage 或 confidence 字段，也不要在文本中生成新的分数或等级判断。
- 优势只能引用 supporting 或 transfer Evidence；风险只能引用 limiting 且
  polarity 为 contradicting 的 Evidence。
- 只能使用 ScoreSnapshot.requirement_assessments 中已经出现的 Evidence ID、
  Role Profile 中已经出现的 dimension ID；不要编造事实或 ID。
- 未验证项只能描述为当前观察不足、尚未核验或需要补充证据，不得写成能力差、
  风险或劣势。
- 不得输出任何录用、淘汰或招聘决策结论。

请严格按照 ReportNarrativeDraft 的 JSON 结构输出。
""".strip()

_PROHIBITED_HIRING_PHRASES = (
    "建议录用",
    "建议淘汰",
    "必须录用",
    "不予录用",
    "录用",
    "淘汰",
)

_UNVERIFIED_NEGATIVE_PHRASES = (
    "能力不足",
    "表现不足",
    "明显不足",
    "能力薄弱",
    "表现薄弱",
    "缺乏能力",
    "能力欠缺",
    "能力缺失",
    "能力较低",
    "能力差",
    "表现差",
    "不具备",
    "无法胜任",
    "不合格",
    "劣势",
    "短板",
    "风险",
    "失败",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def _serialized(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )


def _index_by_id(
    items: Iterable[Any],
    item_kind: str,
    *,
    id_field: str = "id",
) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for item in items:
        item_id = getattr(item, id_field, None)
        if not isinstance(item_id, str) or not item_id.strip():
            raise GroundingValidationError(f"{item_kind} 必须有非空 ID")
        if item_id in indexed:
            raise GroundingValidationError(f"{item_kind} ID 重复: {item_id}")
        indexed[item_id] = item
    return indexed


def _raise_unknown_ids(
    ids: Iterable[str],
    known_ids: set[str],
    label: str,
) -> None:
    unknown_ids = sorted(set(ids) - known_ids)
    if unknown_ids:
        raise GroundingValidationError(
            f"{label} 不存在: " + ", ".join(unknown_ids)
        )


def _snapshot_evidence_sources(
    score_snapshot: ScoreSnapshot,
    evidences: list[Evidence],
    role_profile: RoleCompetencyProfile,
) -> tuple[
    dict[str, Evidence],
    set[str],
    set[str],
    set[str],
    dict[str, set[str]],
    dict[str, RadarDimensionResult],
]:
    if score_snapshot.role_family != role_profile.role_family:
        raise GroundingValidationError(
            "ScoreSnapshot 与 Role Profile 的 role_family 不一致"
        )
    if score_snapshot.role_profile_version != role_profile.version:
        raise GroundingValidationError(
            "ScoreSnapshot 与 Role Profile 的版本不一致"
        )

    evidence_by_id = _index_by_id(evidences, "Evidence")
    role_dimension_ids = {dimension.id for dimension in role_profile.dimensions}

    radar_by_dimension = _index_by_id(
        score_snapshot.radar_dimensions,
        "Radar Dimension",
        id_field="dimension_id",
    )
    radar_dimension_ids = set(radar_by_dimension)
    unknown_radar_dimensions = sorted(radar_dimension_ids - role_dimension_ids)
    if unknown_radar_dimensions:
        raise GroundingValidationError(
            "ScoreSnapshot 引用了不存在的 Role Dimension: "
            + ", ".join(unknown_radar_dimensions)
        )

    for assessment in score_snapshot.requirement_assessments:
        if assessment.dimension_id not in role_dimension_ids:
            raise GroundingValidationError(
                "RequirementEvidenceAssessment 引用了不存在的 Role Dimension: "
                + assessment.dimension_id
            )
    for requirement_score in score_snapshot.requirement_scores:
        if requirement_score.dimension_id not in role_dimension_ids:
            raise GroundingValidationError(
                "RequirementScore 引用了不存在的 Role Dimension: "
                + requirement_score.dimension_id
            )

    supporting_ids: set[str] = set()
    limiting_ids: set[str] = set()
    transfer_ids: set[str] = set()
    source_kinds: dict[str, set[str]] = {}

    def validate_ids(ids: Iterable[str], kind: str) -> None:
        ids = list(ids)
        _raise_unknown_ids(ids, set(evidence_by_id), f"ScoreSnapshot {kind} Evidence ID")

    def register(ids: Iterable[str], kind: str) -> None:
        validate_ids(ids, kind)
        for evidence_id in ids:
            source_kinds.setdefault(evidence_id, set()).add(kind)

    for assessment in score_snapshot.requirement_assessments:
        register(assessment.supporting_evidence_ids, "supporting")
        register(assessment.limiting_evidence_ids, "limiting")
        register(assessment.transfer_evidence_ids, "transfer")
        supporting_ids.update(assessment.supporting_evidence_ids)
        limiting_ids.update(assessment.limiting_evidence_ids)
        transfer_ids.update(assessment.transfer_evidence_ids)

        for reason in assessment.assessment_reasons:
            validate_ids(reason.evidence_ids, "Assessment ScoreReason")

    for radar in score_snapshot.radar_dimensions:
        for reason in radar.score_reasons:
            validate_ids(reason.evidence_ids, "Radar ScoreReason")

    for reason in score_snapshot.job_match.limiting_reasons:
        validate_ids(reason.evidence_ids, "JobMatch ScoreReason")

    return (
        evidence_by_id,
        supporting_ids,
        limiting_ids,
        transfer_ids,
        source_kinds,
        radar_by_dimension,
    )


def _messages(
    score_snapshot: ScoreSnapshot,
    evidences: list[Evidence],
    role_profile: RoleCompetencyProfile,
    assessment_evidence_ids: set[str],
) -> list[tuple[str, str]]:
    grounded_evidence = [
        evidence
        for evidence in evidences
        if evidence.id in assessment_evidence_ids
    ]
    return [
        ("system", _SYSTEM_PROMPT),
        (
            "human",
            "Role Profile:\n"
            + _serialized(role_profile)
            + "\n\nScoreSnapshot（锁定事实，只可解释不可改写）：\n"
            + _serialized(score_snapshot)
            + "\n\n允许引用的 Evidence（其 ID 必须来自上述 assessment provenance）：\n"
            + _serialized(grounded_evidence)
            + "\n\n请生成 ReportNarrativeDraft。不得评分，不得改变任何已锁定事实。",
        ),
    ]


def _check_narrative_text(text: str, label: str) -> None:
    lowered = text.casefold()
    for phrase in _PROHIBITED_HIRING_PHRASES:
        if phrase.casefold() in lowered:
            raise GroundingValidationError(
                f"{label} 包含禁止的录用/淘汰措辞: {phrase}"
            )


def _check_unverified_text(text: str, label: str) -> None:
    lowered = text.casefold()
    for phrase in _UNVERIFIED_NEGATIVE_PHRASES:
        if phrase.casefold() in lowered:
            raise GroundingValidationError(
                f"{label} 将未验证项写成负向结论: {phrase}"
            )


def _all_narrative_items(draft: ReportNarrativeDraft):
    yield from (("strength", item) for item in draft.strengths)
    yield from (("risk", item) for item in draft.risks)
    yield from (("unverified", item) for item in draft.unverified_areas)
    yield from (("fit_context", item) for item in draft.fit_contexts)


def _validate_item_references(
    item: NarrativeItem,
    label: str,
    dimension_ids: set[str],
    evidence_by_id: dict[str, Evidence],
    assessment_evidence_ids: set[str],
) -> None:
    _raise_unknown_ids(item.dimension_ids, dimension_ids, f"{label} dimension ID")
    _raise_unknown_ids(
        item.evidence_ids,
        set(evidence_by_id),
        f"{label} Evidence ID",
    )
    not_grounded = sorted(
        set(item.evidence_ids) - assessment_evidence_ids
    )
    if not_grounded:
        raise GroundingValidationError(
            f"{label} 的 Evidence 未出现在 ScoreSnapshot assessments: "
            + ", ".join(not_grounded)
        )


def _has_unverified_reason(radar: RadarDimensionResult) -> bool:
    return any(
        reason.reason_type == "unverified"
        for reason in radar.score_reasons
    )


def _validate_snapshot_reason_provenance(
    score_snapshot: ScoreSnapshot,
    evidence_by_id: dict[str, Evidence],
    assessment_evidence_ids: set[str],
) -> None:
    known_ids = set(evidence_by_id)
    for radar in score_snapshot.radar_dimensions:
        for reason in radar.score_reasons:
            _raise_unknown_ids(reason.evidence_ids, known_ids, "Radar ScoreReason Evidence ID")
            ungrounded = sorted(
                set(reason.evidence_ids) - assessment_evidence_ids
            )
            if ungrounded:
                raise GroundingValidationError(
                    "Radar ScoreReason 的 Evidence 未出现在 ScoreSnapshot assessments: "
                    + ", ".join(ungrounded)
                )
    for assessment in score_snapshot.requirement_assessments:
        for reason in assessment.assessment_reasons:
            _raise_unknown_ids(
                reason.evidence_ids,
                known_ids,
                "Assessment ScoreReason Evidence ID",
            )
            ungrounded = sorted(
                set(reason.evidence_ids) - assessment_evidence_ids
            )
            if ungrounded:
                raise GroundingValidationError(
                    "Assessment ScoreReason 的 Evidence 未出现在 ScoreSnapshot assessments: "
                    + ", ".join(ungrounded)
                )
    for reason in score_snapshot.job_match.limiting_reasons:
        _raise_unknown_ids(
            reason.evidence_ids,
            known_ids,
            "JobMatch ScoreReason Evidence ID",
        )
        ungrounded = sorted(set(reason.evidence_ids) - assessment_evidence_ids)
        if ungrounded:
            raise GroundingValidationError(
                "JobMatch ScoreReason 的 Evidence 未出现在 ScoreSnapshot assessments: "
                + ", ".join(ungrounded)
            )


def validate_report_narrative(
    draft: ReportNarrativeDraft,
    score_snapshot: ScoreSnapshot,
    evidences: list[Evidence],
    role_profile: RoleCompetencyProfile,
) -> None:
    """Validate narrative provenance and wording without changing the draft."""

    try:
        normalized_draft = ReportNarrativeDraft.model_validate(draft)
        normalized_snapshot = ScoreSnapshot.model_validate(score_snapshot)
        normalized_profile = RoleCompetencyProfile.model_validate(role_profile)
        normalized_evidences = [Evidence.model_validate(item) for item in evidences]
    except (ValidationError, TypeError, ValueError) as exc:
        raise GroundingValidationError(
            "报告叙事或其输入不符合结构化契约"
        ) from exc

    (
        evidence_by_id,
        supporting_ids,
        limiting_ids,
        transfer_ids,
        source_kinds,
        radar_by_dimension,
    ) = _snapshot_evidence_sources(
        normalized_snapshot,
        normalized_evidences,
        normalized_profile,
    )
    assessment_evidence_ids = set(source_kinds)
    _validate_snapshot_reason_provenance(
        normalized_snapshot,
        evidence_by_id,
        assessment_evidence_ids,
    )

    for label, item in _all_narrative_items(normalized_draft):
        _check_narrative_text(item.text, f"{label} 文案")
        _validate_item_references(
            item,
            label,
            set(radar_by_dimension) | {
                dimension.id for dimension in normalized_profile.dimensions
            },
            evidence_by_id,
            assessment_evidence_ids,
        )

    _check_narrative_text(
        normalized_draft.executive_summary,
        "executive_summary",
    )

    for index, item in enumerate(normalized_draft.strengths):
        label = f"strengths[{index}]"
        if not item.evidence_ids:
            raise GroundingValidationError(
                f"{label} 必须引用 supporting Evidence"
            )
        for evidence_id in item.evidence_ids:
            evidence = evidence_by_id[evidence_id]
            if evidence.polarity != "supporting":
                raise GroundingValidationError(
                    f"{label} 只能引用 supporting Evidence: {evidence_id}"
                )
            if evidence_id not in supporting_ids | transfer_ids:
                raise GroundingValidationError(
                    f"{label} 的 Evidence 不在 supporting/transfer assessment 引用中: "
                    + evidence_id
                )

    for index, item in enumerate(normalized_draft.risks):
        label = f"risks[{index}]"
        if not item.evidence_ids:
            raise GroundingValidationError(
                f"{label} 必须引用 limiting/contradicting Evidence"
            )
        for evidence_id in item.evidence_ids:
            evidence = evidence_by_id[evidence_id]
            if evidence.polarity != "contradicting":
                raise GroundingValidationError(
                    f"{label} 只能引用 contradicting Evidence: {evidence_id}"
                )
            if evidence_id not in limiting_ids:
                raise GroundingValidationError(
                    f"{label} 的 Evidence 不在 limiting assessment 引用中: "
                    + evidence_id
                )

    for index, item in enumerate(normalized_draft.unverified_areas):
        label = f"unverified_areas[{index}]"
        _check_unverified_text(item.text, label)
        if item.evidence_ids:
            raise GroundingValidationError(
                f"{label} 不应使用正向或负向 Evidence 作为未验证结论"
            )
        if not item.dimension_ids:
            raise GroundingValidationError(
                f"{label} 必须关联至少一个未验证维度"
            )
        for dimension_id in item.dimension_ids:
            radar = radar_by_dimension.get(dimension_id)
            if radar is None or not _has_unverified_reason(radar):
                raise GroundingValidationError(
                    f"{label} 未映射到带 unverified ScoreReason 的维度: "
                    + dimension_id
                )

    for index, action in enumerate(normalized_draft.development_actions):
        label = f"development_actions[{index}]"
        if action.dimension_id not in {
            dimension.id for dimension in normalized_profile.dimensions
        }:
            raise GroundingValidationError(
                f"{label} 引用了不存在的 dimension ID: {action.dimension_id}"
            )
        _check_narrative_text(action.current_gap, f"{label}.current_gap")
        for action_index, text in enumerate(action.actions):
            _check_narrative_text(
                text,
                f"{label}.actions[{action_index}]",
            )
        for criterion_index, text in enumerate(action.acceptance_criteria):
            _check_narrative_text(
                text,
                f"{label}.acceptance_criteria[{criterion_index}]",
            )


def write_report_narrative(
    score_snapshot: ScoreSnapshot,
    evidence: list[Evidence],
    role_profile: RoleCompetencyProfile,
    llm_client=llm,
) -> ReportNarrativeDraft:
    """Generate one structured narrative and validate it fail-closed."""

    normalized_snapshot = ScoreSnapshot.model_validate(score_snapshot)
    normalized_profile = RoleCompetencyProfile.model_validate(role_profile)
    normalized_evidence = [Evidence.model_validate(item) for item in evidence]
    (
        evidence_by_id,
        _supporting_ids,
        _limiting_ids,
        _transfer_ids,
        source_kinds,
        _radar_by_dimension,
    ) = _snapshot_evidence_sources(
        normalized_snapshot,
        normalized_evidence,
        normalized_profile,
    )
    _validate_snapshot_reason_provenance(
        normalized_snapshot,
        evidence_by_id,
        set(source_kinds),
    )

    response = llm_client.structured(
        _messages(
            normalized_snapshot,
            normalized_evidence,
            normalized_profile,
            set(source_kinds),
        ),
        ReportNarrativeDraft,
    )
    try:
        draft = ReportNarrativeDraft.model_validate(response)
    except (ValidationError, TypeError, ValueError) as exc:
        raise GroundingValidationError(
            "LLM 返回的 ReportNarrativeDraft 无效，可能包含非法字段或评分字段"
        ) from exc

    validate_report_narrative(
        draft,
        normalized_snapshot,
        normalized_evidence,
        normalized_profile,
    )
    return draft


def _valid_reason_evidence_ids(
    reason: ScoreReason,
    evidence_by_id: dict[str, Evidence],
    source_ids: set[str],
    *,
    required_polarity: str | None = None,
) -> list[str]:
    valid_ids: list[str] = []
    for evidence_id in reason.evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None or evidence_id not in source_ids:
            continue
        if required_polarity is not None and evidence.polarity != required_polarity:
            continue
        if evidence_id not in valid_ids:
            valid_ids.append(evidence_id)
    return valid_ids


def _append_unique_item(
    items: list[NarrativeItem],
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]],
    item: NarrativeItem,
) -> None:
    key = (
        item.text,
        tuple(item.dimension_ids),
        tuple(item.evidence_ids),
    )
    if key not in seen:
        seen.add(key)
        items.append(item)


def fallback_report_narrative(
    score_snapshot: ScoreSnapshot,
    evidence: list[Evidence],
    role_profile: RoleCompetencyProfile,
) -> ReportNarrativeDraft:
    """Build a stable narrative from ScoreReason objects without an LLM call."""

    normalized_snapshot = ScoreSnapshot.model_validate(score_snapshot)
    normalized_profile = RoleCompetencyProfile.model_validate(role_profile)
    normalized_evidence = [Evidence.model_validate(item) for item in evidence]
    (
        evidence_by_id,
        supporting_ids,
        limiting_ids,
        transfer_ids,
        source_kinds,
        radar_by_dimension,
    ) = _snapshot_evidence_sources(
        normalized_snapshot,
        normalized_evidence,
        normalized_profile,
    )
    assessment_evidence_ids = set(source_kinds)
    _validate_snapshot_reason_provenance(
        normalized_snapshot,
        evidence_by_id,
        assessment_evidence_ids,
    )

    strengths: list[NarrativeItem] = []
    risks: list[NarrativeItem] = []
    unverified_areas: list[NarrativeItem] = []
    fit_contexts: list[NarrativeItem] = []
    seen_strengths: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    seen_risks: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    seen_unverified: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    seen_fit_contexts: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()

    for radar in normalized_snapshot.radar_dimensions:
        dimension_id = radar.dimension_id
        for reason in radar.score_reasons:
            if reason.reason_type == "strength":
                ids = _valid_reason_evidence_ids(
                    reason,
                    evidence_by_id,
                    supporting_ids | transfer_ids,
                    required_polarity="supporting",
                )
                if ids:
                    _append_unique_item(
                        strengths,
                        seen_strengths,
                        NarrativeItem(
                            text=reason.text,
                            dimension_ids=[dimension_id],
                            evidence_ids=ids,
                        ),
                    )
            elif reason.reason_type in {"risk", "critical_error"}:
                ids = _valid_reason_evidence_ids(
                    reason,
                    evidence_by_id,
                    limiting_ids,
                    required_polarity="contradicting",
                )
                if ids:
                    _append_unique_item(
                        risks,
                        seen_risks,
                        NarrativeItem(
                            text=reason.text,
                            dimension_ids=[dimension_id],
                            evidence_ids=ids,
                        ),
                    )
            elif reason.reason_type == "unverified":
                _append_unique_item(
                    unverified_areas,
                    seen_unverified,
                    NarrativeItem(
                        text=reason.text,
                        dimension_ids=[dimension_id],
                    ),
                )

    for reason in normalized_snapshot.job_match.limiting_reasons:
        ids = _valid_reason_evidence_ids(
            reason,
            evidence_by_id,
            limiting_ids,
            required_polarity="contradicting",
        )
        if ids:
            _append_unique_item(
                fit_contexts,
                seen_fit_contexts,
                NarrativeItem(text=reason.text, evidence_ids=ids),
            )

    development_actions: list[DevelopmentAction] = []
    action_dimensions: set[str] = set()
    for item in (*risks, *unverified_areas):
        for dimension_id in item.dimension_ids:
            if dimension_id in action_dimensions:
                continue
            action_dimensions.add(dimension_id)
            development_actions.append(
                DevelopmentAction(
                    dimension_id=dimension_id,
                    current_gap=item.text,
                    actions=["补充一个独立场景并记录可观察的验证过程。"],
                    acceptance_criteria=["能够说明输入、边界、验证方式与结果。"],
                )
            )

    if normalized_snapshot.job_match.published:
        executive_summary = "当前报告基于已验证证据生成岗位适配摘要，并保留覆盖范围与限制条件。"
    else:
        executive_summary = "当前证据覆盖仍有限，岗位适配结果暂不计算；已验证表现和待核验项已分别列出。"

    draft = ReportNarrativeDraft(
        executive_summary=executive_summary,
        strengths=strengths,
        risks=risks,
        unverified_areas=unverified_areas,
        fit_contexts=fit_contexts,
        development_actions=development_actions,
    )
    validate_report_narrative(
        draft,
        normalized_snapshot,
        normalized_evidence,
        normalized_profile,
    )
    return draft
