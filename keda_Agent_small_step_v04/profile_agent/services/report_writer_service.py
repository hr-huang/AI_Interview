"""Write an evidence-grounded narrative for an immutable score snapshot.

The writer is deliberately a narrow boundary around the LLM.  Scores,
levels, coverage and confidence already belong to ``ScoreSnapshot``; the LLM
may only fill the prose-shaped ``ReportNarrativeDraft``.  Every citation is
checked against the evidence provenance recorded by the snapshot before the
draft is returned.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from profile_agent.llm import llm
from profile_agent.schemas.report_schema import (
    DevelopmentAction,
    NarrativeItem,
    RadarDimensionResult,
    ReinterviewFocus,
    ReportNarrativeDraft,
    RoleCompetencyProfile,
    ScoreReason,
    ScoreSnapshot,
    _ReportModel,
)
from profile_agent.schemas.runtime_schema import Evidence


class GroundingValidationError(ValueError):
    """Raised when a narrative is not grounded in the supplied snapshot."""


class HiringDecisionTextError(GroundingValidationError):
    """Raised when public enterprise copy contains a hiring decision."""


class EnterpriseCopyDraft(_ReportModel):
    """The prose-only output boundary for the enterprise report.

    Hiring decisions, ranking and selected dimensions are deterministic
    inputs.  The writer can only explain the locked assessment and fill the
    question plan for the already selected dimensions.
    """

    overall_assessment: str
    reinterview_plan: list[ReinterviewFocus] = Field(
        default_factory=list,
        max_length=3,
    )


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

_HIRING_DECISION_PATTERNS = (
    re.compile(
        r"(?:建议|推荐|应当|可以|可|决定|应)"
        r"[^。！？\n]{0,20}"
        r"(?:进入(?:下一轮|复试|终面)|录用|淘汰)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:进入(?:下一轮|复试|终面))", re.IGNORECASE),
    re.compile(r"(?:通过)(?:面试|筛选|招聘)", re.IGNORECASE),
    re.compile(
        r"(?:建议|推荐|应当|可以|可|决定|应)"
        r"[^。！？\n]{0,20}继续推进",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:候选人|应聘者|面试者)[^。！？\n]{0,20}继续推进",
        re.IGNORECASE,
    ),
    re.compile(
        r"继续推进[^。！？\n]{0,20}"
        r"(?:候选人|应聘者|面试者|下一轮|复试|终面|面试|筛选|招聘|录用|淘汰)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:进入(?:下一轮|复试|终面))[^。！？\n]{0,20}继续推进",
        re.IGNORECASE,
    ),
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

_INTERNAL_PUBLIC_TEXT_ID = re.compile(
    r"(?:role_dim_\d+|req_[A-Za-z0-9_]*|ev_[A-Za-z0-9_]*|"
    r"(?<![A-Za-z0-9_])d\d+_[A-Za-z0-9_]+|\bRequirement\b|\bRubricMatch\b)",
    re.IGNORECASE,
)

_GROWTH_ADVICE_PATTERNS = (
    re.compile(
        r"(?:建议|推荐|要求|需要|应当|应|后续|未来|之后|下一步)"
        r"[^。！？\n]{0,20}(?:候选人|其)?"
        r"[^。！？\n]{0,12}(?:补足|补齐|弥补|提升|改进|加强|学习|培训|练习)"
        r"[^。！？\n]{0,16}(?:能力|短板|技能|知识|基础|表现)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"[^。！？\n]{0,8}(?:成长|发展)(?:建议|路径|计划|空间)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:补足|补齐|弥补)[^。！？\n]{0,12}(?:能力|短板|技能|知识)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:提升|改进|加强)[^。！？\n]{0,12}(?:能力|短板|技能|知识)",
        re.IGNORECASE,
    ),
)

_ENTERPRISE_COPY_SYSTEM_PROMPT = """
你是企业评估报告的 Copy Writer。只能根据已经锁定的评分快照、岗位 Role
Pack、决策投影、已选复试维度、决策信号和证据摘录生成 EnterpriseCopyDraft。

严格边界：
- 只输出 overall_assessment 和 reinterview_plan；不能增加字段。
- 岗位招聘结论、复试维度的选择顺序和 priority 已由确定性规则锁定，不能
  重新决定招聘结论、不能改变 selected dimension IDs、不能自行排序优先级。
- 必须为每一个 selected dimension ID 输出且只输出一个复试重点；结构字段可
  使用锁定的维度 ID，但任何公开文案不得出现 req_、ev_、role_dim_、d003_、
  Requirement 或 RubricMatch 等内部标识。
- 公开文案不得提供候选人成长、学习、培训、提升或改进建议；只写本轮复试
  要观察的信号、追问和通过标准。
- 不得输出录用、淘汰或其他新的招聘判断，不得写入分数、等级或置信度。

请严格按照 EnterpriseCopyDraft 的 JSON 结构返回。
""".strip()


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


def _normalise_selected_dimension_ids(
    selected_dimension_ids: Iterable[str],
    role_profile: RoleCompetencyProfile,
) -> list[str]:
    try:
        selected = list(selected_dimension_ids)
    except (TypeError, ValueError) as exc:
        raise GroundingValidationError("selected_dimension_ids 无效") from exc
    if len(selected) > 3:
        raise GroundingValidationError("复试重点不能超过三个维度")
    if any(not isinstance(item, str) or not item.strip() for item in selected):
        raise GroundingValidationError("selected_dimension_ids 必须是非空字符串")
    if len(selected) != len(set(selected)):
        raise GroundingValidationError("selected_dimension_ids 不能重复")
    known_ids = {dimension.id for dimension in role_profile.dimensions}
    unknown_ids = sorted(set(selected) - known_ids)
    if unknown_ids:
        raise GroundingValidationError(
            "selected_dimension_ids 不存在: " + ", ".join(unknown_ids)
        )
    return selected


def _normalise_enterprise_inputs(
    snapshot: ScoreSnapshot,
    profile: RoleCompetencyProfile,
    evidences: Iterable[Evidence] | None,
) -> tuple[
    ScoreSnapshot,
    RoleCompetencyProfile,
    list[Evidence],
    dict[str, Evidence],
    set[str],
]:
    """Validate the shared enterprise writer inputs and provenance."""

    try:
        normalized_snapshot = ScoreSnapshot.model_validate(snapshot)
        normalized_profile = RoleCompetencyProfile.model_validate(profile)
        normalized_evidences = (
            []
            if evidences is None
            else [Evidence.model_validate(item) for item in evidences]
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise GroundingValidationError(
            "企业文案输入不符合结构化契约"
        ) from exc

    if (
        normalized_snapshot.role_family != normalized_profile.role_family
        or normalized_snapshot.role_profile_version != normalized_profile.version
    ):
        raise GroundingValidationError(
            "ScoreSnapshot 与 RoleCompetencyProfile 的版本或岗位不一致"
        )

    # A fallback call without an evidence collection is supported for the
    # frozen dimension copy contract.  When evidence is supplied, both online
    # and offline paths execute exactly the same provenance checks.
    if evidences is None:
        return (
            normalized_snapshot,
            normalized_profile,
            normalized_evidences,
            {},
            set(),
        )

    (
        evidence_by_id,
        _supporting_ids,
        _limiting_ids,
        _transfer_ids,
        source_kinds,
        _radar_by_dimension,
    ) = _snapshot_evidence_sources(
        normalized_snapshot,
        normalized_evidences,
        normalized_profile,
    )
    _validate_snapshot_reason_provenance(
        normalized_snapshot,
        evidence_by_id,
        set(source_kinds),
    )
    return (
        normalized_snapshot,
        normalized_profile,
        normalized_evidences,
        evidence_by_id,
        set(source_kinds),
    )


_ENTERPRISE_NON_PUBLIC_FIELDS = frozenset(
    {"priority", "dimension_id", "suggested_minutes", "related_evidence_ids"}
)


def _iter_enterprise_public_texts(
    value: Any,
    label: str = "",
    *,
    include_overall: bool = True,
) -> Iterable[tuple[str, str]]:
    """Walk every user-facing string in an ``EnterpriseCopyDraft``.

    The structured identifiers and scheduling metadata are intentionally not
    public copy.  Recursing through the Pydantic fields keeps this boundary in
    one place as the copy contract grows new prose fields.
    """

    if isinstance(value, str):
        yield label, value
        return
    if isinstance(value, BaseModel):
        for field_name in value.__class__.model_fields:
            if field_name in _ENTERPRISE_NON_PUBLIC_FIELDS:
                continue
            if (
                not include_overall
                and isinstance(value, EnterpriseCopyDraft)
                and field_name == "overall_assessment"
            ):
                continue
            child_label = f"{label}.{field_name}" if label else field_name
            yield from _iter_enterprise_public_texts(
                getattr(value, field_name),
                child_label,
                include_overall=include_overall,
            )
        return
    if isinstance(value, dict):
        for key, child in value.items():
            child_label = f"{label}[{key!r}]" if label else repr(key)
            yield from _iter_enterprise_public_texts(
                child,
                child_label,
                include_overall=include_overall,
            )
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, child in enumerate(value):
            child_label = f"{label}[{index}]" if label else f"[{index}]"
            yield from _iter_enterprise_public_texts(
                child,
                child_label,
                include_overall=include_overall,
            )


def _validate_enterprise_copy_text(
    text: str,
    label: str,
) -> None:
    if not text.strip():
        raise GroundingValidationError(f"{label} 不能为空")
    internal_match = _INTERNAL_PUBLIC_TEXT_ID.search(text)
    if internal_match:
        raise GroundingValidationError(
            f"{label} 不得暴露内部标识: {internal_match.group(0)}"
        )
    for pattern in _GROWTH_ADVICE_PATTERNS:
        growth_match = pattern.search(text)
        if growth_match:
            raise GroundingValidationError(
                f"{label} 不得提供候选人成长建议: {growth_match.group(0)}"
            )
    for pattern in _HIRING_DECISION_PATTERNS:
        hiring_match = pattern.search(text)
        if hiring_match:
            raise HiringDecisionTextError(
                f"{label} 不得新增招聘结论: {hiring_match.group(0)}"
            )
    lowered = text.casefold()
    for phrase in _PROHIBITED_HIRING_PHRASES:
        if phrase.casefold() in lowered:
            raise HiringDecisionTextError(
                f"{label} 不得新增招聘结论: {phrase}"
            )


def _validate_enterprise_copy_public_texts(
    draft: EnterpriseCopyDraft,
    *,
    include_overall: bool = True,
) -> None:
    for label, text in _iter_enterprise_public_texts(
        draft,
        include_overall=include_overall,
    ):
        _validate_enterprise_copy_text(text, label)


def _validate_enterprise_copy(
    draft: EnterpriseCopyDraft,
    selected_dimension_ids: list[str],
    *,
    known_evidence_ids: set[str] | None = None,
) -> None:
    actual_ids = [focus.dimension_id for focus in draft.reinterview_plan]
    if len(actual_ids) != len(selected_dimension_ids):
        raise GroundingValidationError(
            "reinterview_plan 必须为每个已选维度返回一个重点"
        )
    if len(actual_ids) != len(set(actual_ids)):
        raise GroundingValidationError("reinterview_plan 的维度不能重复")
    if set(actual_ids) != set(selected_dimension_ids):
        raise GroundingValidationError(
            "reinterview_plan 只能覆盖已锁定的 selected dimension IDs"
        )
    if known_evidence_ids is None:
        return
    for index, focus in enumerate(draft.reinterview_plan):
        unknown_evidence_ids = sorted(
            set(focus.related_evidence_ids) - known_evidence_ids
        )
        if unknown_evidence_ids:
            raise GroundingValidationError(
                f"reinterview_plan[{index}] 引用了未知 Evidence ID: "
                + ", ".join(unknown_evidence_ids)
            )


def _canonicalize_enterprise_copy(
    draft: EnterpriseCopyDraft,
    selected_dimension_ids: list[str],
    snapshot: ScoreSnapshot,
    role_profile: RoleCompetencyProfile,
) -> EnterpriseCopyDraft:
    dimensions = {dimension.id: dimension for dimension in role_profile.dimensions}
    focus_by_dimension = {
        focus.dimension_id: focus for focus in draft.reinterview_plan
    }
    plan = [
        focus_by_dimension[dimension_id].model_copy(
            update={
                # Priority is deterministic and never selected by the model.
                "priority": index + 1,
                "dimension_name": dimensions[dimension_id].name,
            }
        )
        for index, dimension_id in enumerate(selected_dimension_ids)
    ]
    return EnterpriseCopyDraft(
        overall_assessment=_deterministic_enterprise_overall_assessment(
            snapshot,
            role_profile,
        ),
        reinterview_plan=plan,
    )


def _enterprise_copy_messages(
    snapshot: ScoreSnapshot,
    profile: RoleCompetencyProfile,
    evidences: list[Evidence],
    selected_dimension_ids: list[str],
    assessment_evidence_ids: set[str],
) -> list[tuple[str, str]]:
    # Imports stay local so the legacy narrative writer remains independently
    # usable and the two service modules do not form an import cycle.
    from profile_agent.services.enterprise_report_service import (
        build_decision_signals,
        derive_hiring_decision,
    )

    strengths, risks, unknowns = build_decision_signals(snapshot, profile)
    decision = derive_hiring_decision(snapshot)
    selected_dimensions = [
        dimension
        for dimension in profile.dimensions
        if dimension.id in selected_dimension_ids
    ]
    readable_criteria = {
        dimension.id: {
            "name": dimension.name,
            "minimum_criteria": [
                criterion.text for criterion in dimension.minimum_criteria
            ],
            "excellence_signals": [
                criterion.text for criterion in dimension.excellence_signals
            ],
            "critical_errors": [
                criterion.text for criterion in dimension.critical_errors
            ],
            "accepted_alternatives": [
                criterion.text for criterion in dimension.accepted_alternatives
            ],
        }
        for dimension in selected_dimensions
    }
    grounded_evidence = [
        evidence for evidence in evidences if evidence.id in assessment_evidence_ids
    ]
    return [
        ("system", _ENTERPRISE_COPY_SYSTEM_PROMPT),
        (
            "human",
            "锁定的招聘决策投影（只能解释，不能改变）：\n"
            + _serialized(decision)
            + "\n\n已锁定的 selected dimension IDs（顺序和 priority 由系统负责）：\n"
            + _serialized(selected_dimension_ids)
            + "\n\n可读岗位标准（不要把内部 ID 写入公开文案）：\n"
            + _serialized(readable_criteria)
            + "\n\n决策信号（只能解释已给信号）：\n"
            + _serialized(
                {
                    "strengths": strengths,
                    "risks": risks,
                    "unknowns": unknowns,
                }
            )
            + "\n\n可追溯证据摘录：\n"
            + _serialized(grounded_evidence)
            + "\n\n请生成 EnterpriseCopyDraft；不得评分、不得作招聘判断、不得给候选人成长建议。",
        ),
    ]


def _deterministic_enterprise_overall_assessment(
    snapshot: ScoreSnapshot,
    profile: RoleCompetencyProfile,
) -> str:
    """Project locked report facts into safe, readable overall copy."""

    from profile_agent.services.enterprise_report_service import (
        build_decision_signals,
        derive_hiring_decision,
    )

    decision = derive_hiring_decision(snapshot)
    strengths, risks, unknowns = build_decision_signals(snapshot, profile)
    if decision.code == "INSUFFICIENT_EVIDENCE":
        opening = "当前证据覆盖仍有限，结构化复试需要继续核验关键边界。"
    elif decision.code == "NOT_RECOMMENDED":
        opening = "当前证据包含需要重点核验的限制信号，结构化复试聚焦限制边界。"
    else:
        opening = "当前证据已形成部分岗位相关观察，结构化复试聚焦剩余核验边界。"

    parts = [opening]
    strength_names = [signal.title for signal in strengths if signal.title]
    risk_names = [signal.title for signal in risks if signal.title]
    unknown_names = [signal.title for signal in unknowns if signal.title]
    if strength_names:
        parts.append("已观察到：" + "、".join(dict.fromkeys(strength_names)) + "。")
    if risk_names:
        parts.append("限制信号集中在：" + "、".join(dict.fromkeys(risk_names)) + "。")
    if unknown_names:
        parts.append("尚待核验：" + "、".join(dict.fromkeys(unknown_names)) + "。")
    return "".join(parts)


def write_enterprise_copy(
    snapshot: ScoreSnapshot,
    profile: RoleCompetencyProfile,
    evidences: Iterable[Evidence],
    selected_dimension_ids: Iterable[str],
    llm_client=llm,
) -> EnterpriseCopyDraft:
    """Generate one enterprise copy draft for locked re-interview targets."""

    (
        normalized_snapshot,
        normalized_profile,
        normalized_evidences,
        evidence_by_id,
        source_kinds,
    ) = _normalise_enterprise_inputs(snapshot, profile, evidences)

    selected = _normalise_selected_dimension_ids(
        selected_dimension_ids,
        normalized_profile,
    )
    messages = _enterprise_copy_messages(
        normalized_snapshot,
        normalized_profile,
        normalized_evidences,
        selected,
        set(source_kinds),
    )

    try:
        response = llm_client.structured(messages, EnterpriseCopyDraft)
    except Exception:
        # The deterministic fallback is the only recovery path for a model
        # outage or API error; it never triggers a second structured call.
        return fallback_enterprise_copy(
            normalized_snapshot,
            normalized_profile,
            selected,
            evidence=normalized_evidences,
        )
    try:
        draft = EnterpriseCopyDraft.model_validate(response)
    except (ValidationError, TypeError, ValueError) as exc:
        raise GroundingValidationError(
            "LLM 返回的 EnterpriseCopyDraft 无效"
        ) from exc

    _validate_enterprise_copy(
        draft,
        selected,
        known_evidence_ids=set(evidence_by_id),
    )
    try:
        # Check model-controlled public prose before canonicalization so a
        # malicious dimension_name cannot be hidden by the locked profile
        # name.  overall_assessment is ignored here and projected below.
        _validate_enterprise_copy_public_texts(draft, include_overall=False)
    except HiringDecisionTextError:
        return fallback_enterprise_copy(
            normalized_snapshot,
            normalized_profile,
            selected,
            evidence=normalized_evidences,
        )

    canonical = _canonicalize_enterprise_copy(
        draft,
        selected,
        normalized_snapshot,
        normalized_profile,
    )
    _validate_enterprise_copy_public_texts(canonical)
    return canonical


_FALLBACK_REINTERVIEW_CONTENT: dict[str, dict[str, object]] = {
    "role_dim_01": {
        "reason": "需要核验 Agent 状态、任务拆分与工具边界是否能闭环。",
        "question": "请拆解一次 Agent 编排任务，并说明状态、工具和失败转移。",
        "follow_ups": ["何时交给人工？"],
        "positive_signals": ["边界和转移条件清晰。"],
        "risk_signals": ["只描述链路而没有状态约束。"],
        "pass_criteria": ["能说明输入、边界、失败处理和验证方式。"],
        "suggested_minutes": 10,
    },
    "role_dim_02": {
        "reason": "需要核验业务目标能否转成可验收的任务模型。",
        "question": "请把一个业务目标拆成任务、输入、输出和验收标准。",
        "follow_ups": ["需求变化时如何调整？"],
        "positive_signals": ["目标、约束与验收标准一致。"],
        "risk_signals": ["方案与业务结果脱节。"],
        "pass_criteria": ["能用可观察结果定义任务完成。"],
        "suggested_minutes": 8,
    },
    "role_dim_03": {
        "reason": "需要核验上下文生命周期、记忆冲突和工具验证的完整边界。",
        "question": "请设计一个涉及上下文生命周期、记忆冲突和工具验证的场景。",
        "follow_ups": ["如何发现记忆污染？", "工具结果不可信时怎么办？"],
        "positive_signals": ["能区分上下文与记忆边界。"],
        "risk_signals": ["只依赖历史内容而没有冲突处理。"],
        "pass_criteria": ["能说明生命周期、冲突处置和工具验证闭环。"],
        "suggested_minutes": 10,
    },
    "role_dim_04": {
        "reason": "需要核验 AI 协作开发方案能否落到生产交付和回滚。",
        "question": "请说明一次 AI 协作开发上线前后的交付与回滚安排。",
        "follow_ups": ["如何设置发布闸门？"],
        "positive_signals": ["交付、观测和回滚互相对应。"],
        "risk_signals": ["只展示开发速度而没有上线约束。"],
        "pass_criteria": ["能说明变更验证、发布闸门和回滚触发条件。"],
        "suggested_minutes": 8,
    },
    "role_dim_05": {
        "reason": "需要核验评测、可观测性与安全治理在异常场景中的联动。",
        "question": "请设计一套发现模型风险并追踪到修复的评测与观测流程。",
        "follow_ups": ["如何处理安全事件？"],
        "positive_signals": ["指标、告警和处置责任清楚。"],
        "risk_signals": ["只有离线指标而没有线上闭环。"],
        "pass_criteria": ["能说明评测样本、告警阈值、责任和复盘证据。"],
        "suggested_minutes": 9,
    },
    "role_dim_06": {
        "reason": "需要核验成本、延迟、质量回归与优化权衡的决策依据。",
        "question": "请在一个真实场景中说明成本、延迟、质量回归与优化权衡。",
        "follow_ups": ["如何定位质量回归？", "何时接受更高成本？"],
        "positive_signals": ["能用数据解释优化取舍。"],
        "risk_signals": ["只追求单一指标而忽略质量回归。"],
        "pass_criteria": ["能说明基线、权衡指标、回归验证和优化边界。"],
        "suggested_minutes": 9,
    },
}


def fallback_enterprise_copy(
    snapshot: ScoreSnapshot,
    profile: RoleCompetencyProfile,
    selected_dimension_ids: Iterable[str],
    *,
    evidence: Iterable[Evidence] | None = None,
) -> EnterpriseCopyDraft:
    """Return dimension-specific enterprise copy without an API call."""

    (
        normalized_snapshot,
        normalized_profile,
        _normalized_evidences,
        evidence_by_id,
        _source_kinds,
    ) = _normalise_enterprise_inputs(snapshot, profile, evidence)
    selected = _normalise_selected_dimension_ids(
        selected_dimension_ids,
        normalized_profile,
    )
    dimensions = {dimension.id: dimension for dimension in normalized_profile.dimensions}
    evidence_by_dimension: dict[str, list[str]] = {}
    for radar in normalized_snapshot.radar_dimensions:
        values = evidence_by_dimension.setdefault(radar.dimension_id, [])
        for reason in radar.score_reasons:
            for evidence_id in reason.evidence_ids:
                if evidence_id not in values:
                    values.append(evidence_id)
    for assessment in normalized_snapshot.requirement_assessments:
        values = evidence_by_dimension.setdefault(assessment.dimension_id, [])
        for evidence_id in (
            assessment.supporting_evidence_ids
            + assessment.limiting_evidence_ids
            + assessment.transfer_evidence_ids
        ):
            if evidence_id not in values:
                values.append(evidence_id)
    known_evidence_ids: set[str] | None = (
        set(evidence_by_id) if evidence is not None else None
    )

    plan: list[ReinterviewFocus] = []
    for priority, dimension_id in enumerate(selected, start=1):
        dimension = dimensions[dimension_id]
        content = _FALLBACK_REINTERVIEW_CONTENT.get(dimension_id)
        if content is None:
            content = {
                "reason": f"需要核验“{dimension.name}”的可观察表现。",
                "question": f"请说明一个能体现“{dimension.name}”的真实场景。",
                "follow_ups": ["如何验证结果？"],
                "positive_signals": ["能给出可复核的过程和结果。"],
                "risk_signals": ["描述停留在抽象判断。"],
                "pass_criteria": ["能说明输入、边界、验证和结果。"],
                "suggested_minutes": 8,
            }
        related_evidence_ids = list(evidence_by_dimension.get(dimension_id, []))
        if known_evidence_ids is not None:
            related_evidence_ids = [
                evidence_id
                for evidence_id in related_evidence_ids
                if evidence_id in known_evidence_ids
            ]
        plan.append(
            ReinterviewFocus(
                priority=priority,
                dimension_id=dimension_id,
                dimension_name=dimension.name,
                reason=content["reason"],
                question=content["question"],
                follow_ups=content["follow_ups"],
                positive_signals=content["positive_signals"],
                risk_signals=content["risk_signals"],
                pass_criteria=content["pass_criteria"],
                suggested_minutes=content["suggested_minutes"],
                related_evidence_ids=related_evidence_ids,
            )
        )

    draft = EnterpriseCopyDraft(
        overall_assessment=_deterministic_enterprise_overall_assessment(
            normalized_snapshot,
            normalized_profile,
        ),
        reinterview_plan=plan,
    )
    _validate_enterprise_copy(
        draft,
        selected,
        known_evidence_ids=known_evidence_ids,
    )
    _validate_enterprise_copy_public_texts(draft)
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
