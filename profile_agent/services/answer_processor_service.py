"""将候选人回答转换为 Evidence，并更新面试运行状态。"""

from __future__ import annotations

from collections.abc import Sequence
import json
import re

from profile_agent.llm import llm
from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.schemas.runtime_schema import (
    AnswerProcessingResult,
    Evidence,
    InterviewRuntimeState,
    InterviewTurn,
    TurnAssessment,
)
from profile_agent.services.runtime_state_service import (
    record_requirement_evidence,
)


_EVIDENCE_ID_PATTERN = re.compile(r"^evidence_(\d+)$")


def _plan_requirement_ids(plan: InterviewPlan) -> set[str]:
    requirement_ids: set[str] = set()

    for target in plan.targets:
        for requirement in target.evidence_requirements:
            if requirement.id in requirement_ids:
                raise ValueError(
                    f"InterviewPlan 包含重复的 requirement_id: {requirement.id}"
                )
            requirement_ids.add(requirement.id)

    return requirement_ids


def _plan_claim_ids(plan: InterviewPlan) -> set[str]:
    return {
        claim_id
        for target in plan.targets
        for claim_id in target.related_claim_ids
    }


def _target_requirement_ids(plan: InterviewPlan, target_id: str) -> set[str]:
    for target in plan.targets:
        if target.id == target_id:
            return {
                requirement.id for requirement in target.evidence_requirements
            }
    raise ValueError(f"InterviewTurn 引用了不存在的 target_id: {target_id}")


def _known_claim_ids(
    plan: InterviewPlan,
    claim_registry: ClaimRegistry | None,
) -> set[str]:
    if claim_registry is not None:
        return {claim.id for claim in claim_registry.claims}
    return _plan_claim_ids(plan)


def _next_evidence_number(existing_evidences: list[Evidence]) -> int:
    numbers = []
    for evidence in existing_evidences:
        match = _EVIDENCE_ID_PATTERN.fullmatch(evidence.id)
        if match is not None:
            numbers.append(int(match.group(1)))

    return max(numbers, default=0) + 1


def _evidences_json(evidences: list[Evidence]) -> str:
    return json.dumps(
        [evidence.model_dump(mode="json") for evidence in evidences],
        ensure_ascii=False,
    )


def _normalize_allowed_gap_tags(values: Sequence[str]) -> list[str]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError("allowed_gap_tags 必须是字符串序列")

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("allowed_gap_tags 必须全部是字符串")
        tag = value.strip()
        if not tag:
            raise ValueError("allowed_gap_tags 不能包含空 tag")
        if tag in seen:
            raise ValueError(f"allowed_gap_tags 包含重复 tag: {tag}")
        seen.add(tag)
        normalized.append(tag)
    return normalized


def _build_messages(
    plan: InterviewPlan,
    runtime_state: InterviewRuntimeState,
    turn: InterviewTurn,
    existing_evidences: list[Evidence],
    claim_registry: ClaimRegistry | None,
    allowed_gap_tags: Sequence[str],
) -> list[tuple[str, str]]:
    claim_registry_json = (
        claim_registry.model_dump_json()
        if claim_registry is not None
        else "null"
    )

    return [
        (
            "system",
            """
你是技术面试系统的 AnswerProcessor。

请只根据本轮候选人回答提取结构化 Evidence，并评估本轮实际覆盖的
Evidence Requirement。evidence_drafts 中的一个 Evidence 可以同时关联多个
requirement_id。只能引用输入中存在的 requirement_id 和 claim_id；不要编造 ID。
只能关联 Current InterviewTurn.target_id 所属 target 下的 requirement_id，
不能把本轮 Evidence 关联到其他 target。
每个 requirement_assessment 都必须能被本轮至少一个 evidence_draft 的
requirement_ids 直接关联。recommended_status 只能使用 in_progress、sufficient、
contradictory；即使强反向证据存在，也可以根据整体评估返回 sufficient。

输出 JSON 契约：
- 根对象必须严格包含 answer_relevance、evidence_drafts、requirement_assessments；
- answer_relevance 只能是 low、medium、high；
- EvidenceDraft 字段只能是 requirement_ids、related_claim_ids、polarity、strength、observation、source_excerpt；
- source_excerpt 必须逐字复制回答中的一段连续原文；禁止使用省略号、改写或拼接不同片段；
- polarity 只能是 supporting、contradicting；strength 只能是 weak、medium、strong；
- RequirementAssessment 字段只能是 requirement_id、recommended_status、rationale、missing_evidence_tags；
- 注意两个相近枚举不要混淆：EvidenceDraft.polarity 使用 contradicting；recommended_status 必须使用 contradictory，绝不能使用 contradicting；
- 只有 Primary Requirement 且 recommended_status 为 in_progress 或 contradictory 时，missing_evidence_tags 才可以从给定 allowlist 中选择；
- recommended_status 为 sufficient 时，missing_evidence_tags 必须是空数组；
- 非 Primary Requirement 的 missing_evidence_tags 必须是空数组，即使它属于当前 Target；
- allowlist 为空时，所有 missing_evidence_tags 都必须是空数组；
- 无法归属到任何 requirement 时，evidence_drafts 和 requirement_assessments 都返回空数组；这也适用于只重复与当前要求无关经历的回答。绝不能生成 requirement_ids 为空的 EvidenceDraft，也不要用 not_started 作为 recommended_status；
- 不要生成 evidence_id、content、status、coverage_notes、overall_notes，也不要增加外层包装。
""".strip(),
        ),
        (
            "human",
            "\n\n".join(
                [
                    f"InterviewPlan:\n{plan.model_dump_json()}",
                    f"InterviewRuntimeState:\n{runtime_state.model_dump_json()}",
                    f"Current InterviewTurn:\n{turn.model_dump_json()}",
                    f"Existing Evidences:\n{_evidences_json(existing_evidences)}",
                    f"ClaimRegistry:\n{claim_registry_json}",
                    "Allowed missing_evidence_tags JSON:\n"
                    + json.dumps(list(allowed_gap_tags), ensure_ascii=False),
                    f"Primary Requirement ID:\n{turn.primary_requirement_id}",
                    "请返回 TurnAssessment。",
                ]
            ),
        ),
    ]


def _validate_assessment(
    assessment: TurnAssessment,
    requirement_ids: set[str],
    allowed_requirement_ids: set[str],
    known_claim_ids: set[str],
    answer_text: str,
    primary_requirement_id: str,
    allowed_gap_tags: Sequence[str],
) -> dict[str, list[str]]:
    for draft in assessment.evidence_drafts:
        if draft.source_excerpt not in answer_text:
            raise ValueError(
                "Evidence source_excerpt 不是回答中的连续原文: "
                f"{draft.source_excerpt}"
            )
        unknown_requirements = set(draft.requirement_ids) - requirement_ids
        if unknown_requirements:
            unknown = ", ".join(sorted(unknown_requirements))
            raise ValueError(f"Evidence 引用了不存在的 requirement_id: {unknown}")
        cross_target_requirements = (
            set(draft.requirement_ids) - allowed_requirement_ids
        )
        if cross_target_requirements:
            invalid = ", ".join(sorted(cross_target_requirements))
            raise ValueError(
                "Evidence requirement 不属于当前 turn target: " + invalid
            )

        unknown_claims = set(draft.related_claim_ids) - known_claim_ids
        if unknown_claims:
            unknown = ", ".join(sorted(unknown_claims))
            raise ValueError(f"Evidence 引用了不存在的 claim_id: {unknown}")

    assessed_requirements: set[str] = set()
    for requirement_assessment in assessment.requirement_assessments:
        requirement_id = requirement_assessment.requirement_id
        if requirement_id not in requirement_ids:
            raise ValueError(
                "RequirementAssessment 引用了不存在的 requirement_id: "
                f"{requirement_id}"
            )
        if requirement_id in assessed_requirements:
            raise ValueError(
                f"RequirementAssessment 的 requirement_id 重复: {requirement_id}"
            )
        assessed_requirements.add(requirement_id)

    current_turn_requirement_ids = {
        requirement_id
        for draft in assessment.evidence_drafts
        for requirement_id in draft.requirement_ids
    }
    for requirement_id in assessed_requirements:
        if requirement_id not in current_turn_requirement_ids:
            raise ValueError(
                "RequirementAssessment 没有本轮 linked evidence: "
                f"{requirement_id}"
            )

    allowlist = {tag: tag for tag in allowed_gap_tags}
    validated_gap_tags: dict[str, list[str]] = {}
    for requirement_assessment in assessment.requirement_assessments:
        requirement_id = requirement_assessment.requirement_id
        raw_tags = requirement_assessment.missing_evidence_tags
        if requirement_id != primary_requirement_id:
            if raw_tags:
                raise ValueError(
                    "非 Primary Requirement 的 missing_evidence_tags 必须是 []: "
                    f"{requirement_id}"
                )
            validated_gap_tags[requirement_id] = []
            continue
        if requirement_assessment.recommended_status == "sufficient":
            if raw_tags:
                raise ValueError(
                    "recommended_status=sufficient 时 missing_evidence_tags 必须是 []"
                )
            validated_gap_tags[requirement_id] = []
            continue

        normalized_tags: list[str] = []
        seen_tags: set[str] = set()
        for raw_tag in raw_tags:
            normalized_tag = raw_tag.strip()
            if normalized_tag in seen_tags:
                raise ValueError(
                    "missing_evidence_tags 包含重复 tag: "
                    f"{normalized_tag}"
                )
            seen_tags.add(normalized_tag)
            if normalized_tag not in allowlist:
                if not allowlist:
                    raise ValueError(
                        "allowed_gap_tags allowlist 为空时 "
                        "missing_evidence_tags 必须是 []"
                    )
                raise ValueError(
                    "missing_evidence_tags 包含 allowlist 外的 tag: "
                    f"{normalized_tag}"
                )
            normalized_tags.append(allowlist[normalized_tag])
        validated_gap_tags[requirement_id] = normalized_tags

    return validated_gap_tags


def process_answer(
    plan: InterviewPlan,
    runtime_state: InterviewRuntimeState,
    turn: InterviewTurn,
    existing_evidences: list[Evidence],
    claim_registry: ClaimRegistry | None = None,
    *,
    allowed_gap_tags: Sequence[str] = (),
    llm_client=llm,
) -> AnswerProcessingResult:
    """Process one answered turn with one structured LLM call.

    The input runtime state and evidence list are treated as immutable. Runtime
    updates are applied one assessed requirement at a time through the shared
    deterministic update service.
    """

    if turn.answer is None or not turn.answer.strip():
        raise ValueError("InterviewTurn 的回答不能为空或未回答")

    requirement_ids = _plan_requirement_ids(plan)
    allowed_requirement_ids = _target_requirement_ids(plan, turn.target_id)
    if turn.primary_requirement_id not in requirement_ids:
        raise ValueError(
            "InterviewTurn 引用了不存在的 requirement_id: "
            f"{turn.primary_requirement_id}"
        )
    normalized_allowed_gap_tags = _normalize_allowed_gap_tags(
        allowed_gap_tags
    )

    messages = _build_messages(
        plan=plan,
        runtime_state=runtime_state,
        turn=turn,
        existing_evidences=existing_evidences,
        claim_registry=claim_registry,
        allowed_gap_tags=normalized_allowed_gap_tags,
    )
    known_claim_ids = _known_claim_ids(plan, claim_registry)
    correction: ValueError | None = None
    for semantic_attempt in range(2):
        attempt_messages = messages
        if correction is not None:
            attempt_messages = messages + [
                (
                    "human",
                    "上一轮 TurnAssessment 未通过业务校验："
                    f"{correction}。请修正后重新生成；每个 "
                    "requirement_assessment 必须由本轮 evidence_drafts 中至少一个 "
                    "Evidence 的 requirement_ids 直接关联。只返回 JSON。",
                )
            ]
        raw_assessment = llm_client.structured(attempt_messages, TurnAssessment)
        assessment = TurnAssessment.model_validate(raw_assessment)
        try:
            validated_gap_tags = _validate_assessment(
                assessment=assessment,
                requirement_ids=requirement_ids,
                allowed_requirement_ids=allowed_requirement_ids,
                known_claim_ids=known_claim_ids,
                answer_text=turn.answer,
                primary_requirement_id=turn.primary_requirement_id,
                allowed_gap_tags=normalized_allowed_gap_tags,
            )
        except ValueError as error:
            if semantic_attempt == 1:
                raise
            correction = error
        else:
            break

    next_number = _next_evidence_number(existing_evidences)
    new_evidences: list[Evidence] = []
    for draft in assessment.evidence_drafts:
        evidence_id = f"evidence_{next_number:03d}"
        next_number += 1
        new_evidences.append(
            Evidence(
                id=evidence_id,
                turn_id=turn.id,
                **draft.model_dump(),
            )
        )

    known_evidence_ids = {
        evidence.id for evidence in existing_evidences
    } | {evidence.id for evidence in new_evidences}
    updated_runtime = runtime_state.model_copy(deep=True)

    for requirement_assessment in assessment.requirement_assessments:
        requirement_id = requirement_assessment.requirement_id
        supporting_ids = [
            evidence.id
            for evidence in new_evidences
            if requirement_id in evidence.requirement_ids
            and evidence.polarity == "supporting"
        ]
        contradicting_ids = [
            evidence.id
            for evidence in new_evidences
            if requirement_id in evidence.requirement_ids
            and evidence.polarity == "contradicting"
        ]
        updated_runtime = record_requirement_evidence(
            updated_runtime,
            requirement_id=requirement_id,
            status=requirement_assessment.recommended_status,
            supporting_evidence_ids=supporting_ids,
            contradicting_evidence_ids=contradicting_ids,
            known_evidence_ids=known_evidence_ids,
            latest_gap_tags=validated_gap_tags[requirement_id],
        )

    return AnswerProcessingResult(
        new_evidences=new_evidences,
        runtime_state=updated_runtime,
    )
