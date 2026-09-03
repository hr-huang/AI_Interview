from __future__ import annotations

from profile_agent.llm import llm
from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AskAction,
    GeneratedQuestion,
    InterviewPlan,
)
from profile_agent.schemas.question_rag_schema import (
    QuestionRetrievalResult,
    validate_embedding_text_value,
)
from profile_agent.schemas.runtime_schema import InterviewTurn
from profile_agent.schemas.scenario_rag_schema import LockedScenarioContext


_SYSTEM_PROMPT = """
你是技术招聘面试系统的 Question Generator，也是一名职业、克制的资深业务面试官。

你服务的唯一岗位范围是“AI Agent应用工程师（校招/初级）”。请根据给定的 Target objective、Evidence Requirement、question_mode、相关 Claim 文本和最近已回答的面试轮次，生成一个候选人可以直接回答的问题。

高质量问题标准：
- 问题必须围绕真实业务情景或候选人真实做过的工作，优先生成项目深挖、业务场景、故障诊断、架构取舍或执行轨迹分析题；
- 不得考察框架定义背诵，例如“介绍一下 RAG”，不要要求候选人复述术语定义；如果一个问题只靠背定义就能回答，必须改写成具体决策、失败边界或验证任务；
- 问题必须要求候选人提供具体事实、取舍、失败边界、可量化验证中的至少一项；
- 开场题优先让候选人先做一个关键判断或设计决策，再说明理由，不要一上来要求完整列举整个方案；
- follow_up 必须承接最近回答中的一个具体点或当前新增的业务约束，继续验证尚未证明的边界；不要泛泛地重复上一题，也不要只说“请进一步说明”；
- 如果提供了 Selected constraint，把它当作面试官新补充的一条业务事实，自然地放进追问；不要泄露其内部来源、标签或标准答案；
- 学生项目、竞赛、开源或实习都可以作为回答证据，课程项目和可复现实验也同样有效，不要求候选人虚构生产经历；
- 生成的问题必须贴合 question_mode：project_deep_dive 深挖一次具体经历，scenario 放入真实约束，system_design 聚焦一个设计决策，coding 关注可验证的实现思路，follow_up 追问最近回答中的一个缺口，foundation 也必须落到具体应用情景；

职业感与可回答性：
- 语气像真实技术面试官，不像考试卷、审讯或教学提示；
- 可以先用一句简短场景承接，再提出一个主要问题；
- 避免“请分别说明 A、B、C”“从三个方面回答”等清单式问题；
- 不要连续堆叠多个彼此独立的问号，不要一次要求候选人同时设计、编码、压测、复盘多个任务；
- 如果已有回答已经覆盖某个点，不要换一种说法重复追问；优先追问尚未证明的取舍、异常路径或验证方式。

通用约束：
- 只问一个清晰的问题；
- 只保留一个主要回答目标，不要列出多个问题或问题清单，不要一次列出多个独立子问题；
- 不要泄露答案、标准答案、推导过程或预期结论；
- 不要评分，不评价候选人表现；
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

    def safe(value: object) -> str | None:
        try:
            return validate_embedding_text_value(value, "candidate_grounding")
        except (TypeError, ValueError):
            return None

    question = safe(record.question_text)
    constraint = safe(business_constraint)
    skills_values = [safe(skill) for skill in record.skills]
    skills = ", ".join(value for value in skills_values if value is not None)
    if question is None or constraint is None or not skills:
        return ""
    dimension = safe(record.dimension_id)
    mode = safe(record.primary_mode or record.question_mode)
    if dimension is None or mode is None:
        return ""
    return f"""
检索题目安全上下文（只可用于改写问题，不得泄露答案提示）：
Original question:
{question}

Business constraint:
{constraint}

Skill names:
{skills}

Dimension:
{dimension}

Question mode:
{mode}
""".strip()


def _scenario_grounding_text(
    scenario_context: LockedScenarioContext | None,
) -> str:
    """Project only the candidate-visible portion of a locked scenario."""

    if scenario_context is None:
        return ""

    def safe(value: object) -> str | None:
        try:
            return validate_embedding_text_value(value, "scenario_grounding")
        except (TypeError, ValueError):
            return None

    fields = [
        ("Business goal", safe(scenario_context.business_goal)),
    ]
    if scenario_context.selected_constraint is not None:
        fields.append(("Selected constraint", safe(scenario_context.selected_constraint.fact)))
    return "\n\n".join(
        f"{label}:\n{value}" for label, value in fields if value is not None
    )


def _candidate_focus(requirement_description: str) -> str:
    focus = " ".join(
        requirement_description.replace("？", "").replace("?", "").split()
    ).strip()
    for prefix in ("验证候选人能否", "验证候选人", "能够说明", "验证", "考察"):
        if focus.startswith(prefix):
            focus = focus[len(prefix) :].strip("：: ")
            break
    if not focus:
        raise ValueError("场景问题缺少原子考点")
    return focus


def _safe_opening_brief(scenario_context: LockedScenarioContext) -> str:
    """Prefer the independent brief and fail closed to the legacy goal."""

    for value in (scenario_context.candidate_brief, scenario_context.business_goal):
        if not isinstance(value, str):
            continue
        if "\r" in value or "\n" in value:
            continue
        brief = " ".join(value.split()).strip()
        if not brief:
            continue
        brief = brief.replace("?", "").replace("？", "")
        if brief:
            return brief
    return "当前业务场景"


def _scenario_opening_question(
    scenario_context: LockedScenarioContext,
) -> GeneratedQuestion:
    """Render a safe first scenario question without another model call.

    The opening may expose only the reviewed business goal and the single
    requirement selected by Supervisor.  Module signals, hidden constraints,
    and model-invented failure cases must not enter the candidate-facing text.
    """

    business_goal = _safe_opening_brief(scenario_context)
    focus = scenario_context.candidate_focus or "整体方案设计"
    if not business_goal or not focus:
        raise ValueError("场景开场问题缺少业务目标或原子考点")

    prefix = (
        business_goal
        if business_goal.endswith(("。", "！", "!", "；", ";"))
        else business_goal + "。"
    )
    return GeneratedQuestion(
        text=(
            f"{prefix}在这个场景里，围绕“{focus}”，"
            "你会优先做哪个关键设计决策，为什么？"
        )
    )


def _scenario_safe_follow_up(requirement_description: str) -> GeneratedQuestion:
    focus = _candidate_focus(requirement_description)
    return GeneratedQuestion(
        text=(
            f"沿着你刚才的回答，如果要用一个可复现的验证来确认“{focus}”"
            "确实有效，你会如何验证，并用什么结果判断它成立？"
        )
    )


def generate_question(
    action: AskAction,
    plan: InterviewPlan,
    claim_registry: ClaimRegistry | None = None,
    recent_turns: list[InterviewTurn] | None = None,
    llm_client=llm,
    retrieval_result: QuestionRetrievalResult | None = None,
    scenario_context: LockedScenarioContext | None = None,
) -> GeneratedQuestion:
    target, requirement = _find_requirement(
        plan=plan,
        target_id=action.target_id,
        requirement_id=action.primary_requirement_id,
    )

    if scenario_context is not None and scenario_context.selected_constraint is None:
        if action.question_mode == "follow_up":
            return _scenario_safe_follow_up(requirement.description)
        return _scenario_opening_question(scenario_context)

    grounding_text = (
        _scenario_grounding_text(scenario_context)
        if scenario_context is not None
        else _retrieval_grounding_text(
            retrieval_result,
            business_constraint=requirement.description,
        )
    )
    grounding_block = f"\n\n{grounding_text}" if grounding_text else ""

    messages = [
        ("system", _SYSTEM_PROMPT),
        (
            "human",
            f"""
目标岗位：AI Agent应用工程师（校招/初级）

候选人证据范围：学生项目、竞赛、开源或实习都可以作为回答依据；课程项目和可复现实验也有效，不要要求候选人虚构正式生产经历。

Target objective:
{target.objective}

Evidence Requirement:
{requirement.description}

question_mode:
{action.question_mode}

请让问题贴合 question_mode，围绕一个真实业务情景或一段真实经历，只保留一个主要问题，不要一次列出多个独立子问题。优先让候选人做一个关键判断，并用具体事实、取舍、失败边界或可量化验证来支撑回答。

如果 question_mode=follow_up，必须承接最近回答中的一个具体点或上面提供的 Selected constraint，继续验证尚未证明的边界；不要换一种说法重复原 Requirement。

Related Claim text:
{_claim_text(target, claim_registry)}

最近2个已回答 turn:
{_history_text(recent_turns)}{grounding_block}

请生成一个符合全部约束、自然且具有真实技术面试职业感的问题。
""".strip(),
        ),
    ]

    generated = llm_client.structured(messages, GeneratedQuestion)
    text = generated.text.strip()
    if not text:
        raise ValueError("生成的问题文本不能为空")

    return GeneratedQuestion(text=text)
