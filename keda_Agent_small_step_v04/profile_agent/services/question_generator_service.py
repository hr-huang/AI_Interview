from __future__ import annotations

from profile_agent.llm import llm
from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AskAction,
    GeneratedQuestion,
    InterviewPlan,
)
from profile_agent.schemas.question_rag_schema import QuestionRetrievalResult
from profile_agent.schemas.runtime_schema import InterviewTurn


_SYSTEM_PROMPT = """
你是技术招聘面试系统的 Question Generator。

请根据给定的 Target objective、Evidence Requirement、question_mode、相关 Claim 文本和最近已回答的面试轮次，生成一个候选人可以直接回答的问题。

必须遵守以下约束：
- 只问一个清晰的问题；
- 不要泄露答案、标准答案、推导过程或预期结论；
- 不要评分，不评价候选人表现；
- 不要列出多个问题，不生成问题清单；
- JSON 根对象必须严格是 {"text": "问题文本"}；
- 根对象只能包含 text；
- 不要返回 {"GeneratedQuestion": ...}，也不要增加任何外层包装。
""".strip()


def _find_target(plan: InterviewPlan, target_id: str):
    for target in plan.targets:
        if target.id == target_id:
            return target

    raise ValueError(f"不存在的 target_id: {target_id}")


def _find_requirement(plan: InterviewPlan, target_id: str, requirement_id: str):
    target = _find_target(plan, target_id)
    for requirement in target.evidence_requirements:
        if requirement.id == requirement_id:
            return target, requirement

    belongs_to_other_target = any(
        requirement.id == requirement_id
        for other_target in plan.targets
        if other_target.id != target_id
        for requirement in other_target.evidence_requirements
    )
    if belongs_to_other_target:
        raise ValueError(
            f"requirement {requirement_id} 不属于 target {target_id}"
        )

    raise ValueError(f"不存在的 requirement_id: {requirement_id}")


def _claim_text(target, claim_registry: ClaimRegistry | None) -> str:
    if claim_registry is None:
        return "无关联 Claim 文本"

    claims_by_id = {
        claim.id: claim.text.strip()
        for claim in claim_registry.claims
        if claim.text.strip()
    }
    texts = [
        claims_by_id[claim_id]
        for claim_id in target.related_claim_ids
        if claim_id in claims_by_id
    ]
    if not texts:
        return "无关联 Claim 文本"

    return "\n".join(f"- {text}" for text in texts)


def _recent_answered_turns(recent_turns: list[InterviewTurn] | None) -> list[InterviewTurn]:
    answered_turns = [
        turn
        for turn in recent_turns or []
        if turn.answer is not None
    ]
    answered_turns.sort(key=lambda turn: turn.sequence_number)
    return answered_turns[-2:]


def _history_text(recent_turns: list[InterviewTurn] | None) -> str:
    turns = _recent_answered_turns(recent_turns)
    if not turns:
        return "无可用的已回答历史"

    return "\n".join(
        f"- turn {turn.sequence_number}:\n"
        f"  question: {turn.question}\n"
        f"  answer: {turn.answer}"
        for turn in turns
    )


def _retrieval_grounding_text(
    retrieval_result: QuestionRetrievalResult | None,
    *,
    business_constraint: str,
) -> str:
    """Project only candidate-safe fields from a selected question record.

    Retrieval scores, identifiers, index metadata, and rubric material are
    intentionally not copied into the generator prompt.  A non-hit result is
    a transparent no-op so legacy generation remains byte-for-byte stable.
    """

    if retrieval_result is None or retrieval_result.status != "hit":
        return ""
    selected = retrieval_result.selected_question
    if selected is None:
        return ""
    record = selected.record
    skills = ", ".join(skill.strip() for skill in record.skills)
    return f"""
检索题目安全上下文（只可用于改写问题，不得泄露答案提示）：
Original question:
{record.question_text.strip()}

Business constraint:
{business_constraint.strip()}

Skill names:
{skills}

Source type:
{record.source_type.strip()}

Source date:
{record.published_at.isoformat()}
""".strip()


def generate_question(
    action: AskAction,
    plan: InterviewPlan,
    claim_registry: ClaimRegistry | None = None,
    recent_turns: list[InterviewTurn] | None = None,
    llm_client=llm,
    retrieval_result: QuestionRetrievalResult | None = None,
) -> GeneratedQuestion:
    target, requirement = _find_requirement(
        plan=plan,
        target_id=action.target_id,
        requirement_id=action.primary_requirement_id,
    )

    grounding_text = _retrieval_grounding_text(
        retrieval_result,
        business_constraint=requirement.description,
    )
    grounding_block = f"\n\n{grounding_text}" if grounding_text else ""

    messages = [
        ("system", _SYSTEM_PROMPT),
        (
            "human",
            f"""
Target objective:
{target.objective}

Evidence Requirement:
{requirement.description}

question_mode:
{action.question_mode}

Related Claim text:
{_claim_text(target, claim_registry)}

最近2个已回答 turn:
{_history_text(recent_turns)}{grounding_block}

请生成一个符合全部约束的面试问题。
""".strip(),
        ),
    ]

    generated = llm_client.structured(messages, GeneratedQuestion)
    text = generated.text.strip()
    if not text:
        raise ValueError("生成的问题文本不能为空")

    return GeneratedQuestion(text=text)
