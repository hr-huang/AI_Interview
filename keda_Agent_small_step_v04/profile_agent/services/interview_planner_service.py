from profile_agent.llm import llm

from profile_agent.schemas.competency_schema import (
    CompetencyModel,
)

from profile_agent.schemas.claim_schema import (
    ClaimRegistry,
)

from profile_agent.schemas.interview_schema import (
    InterviewPolicy,
    InterviewPlanDraft,
    InterviewPlan,
    AssessmentTarget,
    EvidenceRequirement,
)


# ============================================================
# 默认面试 Policy
#
# 第一版先固定在系统内部.
# 后面如果产品需要, 可以再开放给面试官配置.
# ============================================================

DEFAULT_INTERVIEW_POLICY = InterviewPolicy()


# ============================================================
# 根据面试时长计算最多允许多少题
#
# 这是安全上限, 不是目标题数.
#
# 为什么 Python 算?
# 因为这属于明确业务规则, 没有必要让 LLM 决定.
# ============================================================

def calculate_max_questions(
    duration_minutes: int,
) -> int:

    if duration_minutes <= 20:
        return 8

    if duration_minutes <= 30:
        return 10

    if duration_minutes <= 45:
        return 14

    return 18


# ============================================================
# 根据面试时长预留结束时间
#
# 比如:
# 30分钟面试
# -> 2分钟用于结束 / 总结
# -> 约28分钟用于核心评估
# ============================================================

def calculate_closing_buffer(
    duration_minutes: int,
) -> int:

    if duration_minutes <= 20:
        return 1

    return 2


# ============================================================
# 对 LLM 返回的 competency_id / claim_id 做引用完整性检查
#
# 为什么需要?
#
# 因为 LLM 有可能输出:
# competency_99
#
# 但实际 State 中根本不存在 competency_99.
#
# 这种错误不能继续流到 Supervisor.
# ============================================================

def validate_references(
    draft: InterviewPlanDraft,
    competency_model: CompetencyModel,
    claim_registry: ClaimRegistry,
) -> None:

    # 当前真实存在的 competency ID
    valid_competency_ids = {
        competency.id
        for competency in competency_model.competencies
    }

    # 当前真实存在的 claim ID
    valid_claim_ids = {
        claim.id
        for claim in claim_registry.claims
    }

    for target in draft.targets:

        # ----------------------------------------------------
        # 检查 competency 引用
        # ----------------------------------------------------
        for competency_id in target.competency_ids:

            if competency_id not in valid_competency_ids:
                raise ValueError(
                    f"Planner 返回了不存在的 competency_id: "
                    f"{competency_id}"
                )

        # ----------------------------------------------------
        # 检查 claim 引用
        # ----------------------------------------------------
        for claim_id in target.related_claim_ids:

            if claim_id not in valid_claim_ids:
                raise ValueError(
                    f"Planner 返回了不存在的 claim_id: "
                    f"{claim_id}"
                )




def validate_target_count(
    draft: InterviewPlanDraft,
    policy: InterviewPolicy,
) -> None:
    """
    防止 LLM 生成过多面试目标。

    Target 太多会导致：
    - 每个能力验证不足
    - Supervisor 后续路径过于复杂
    - 面试时间无法控制
    """

    if len(draft.targets) > policy.max_targets:
        raise ValueError(
            f"Planner生成Target数量过多: {len(draft.targets)}, "
            f"最大允许: {policy.max_targets}"
        )

# ============================================================
# 检查 LLM 给出的 Target 时间预算是否超出总时间
# ============================================================

def validate_time_budget(
    draft: InterviewPlanDraft,
    available_minutes: int,
) -> None:

    total_budget = sum(
        target.time_budget_minutes
        for target in draft.targets
    )

    if total_budget > available_minutes:
        raise ValueError(
            "InterviewPlanner 时间预算超过本次可用时间."
            f"当前规划: {total_budget} 分钟, "
            f"最多可用: {available_minutes} 分钟."
        )


# ============================================================
# 将 LLM 的 Draft 转换成程序真正使用的 Final Plan
#
# 这里最重要的任务就是:
#
# 1. Python 给 target 分配稳定 ID
# 2. Python 给 evidence requirement 分配稳定 ID
# 3. 填入用户选择的 duration
# 4. 填入最大题数
# 5. 填入 closing buffer
# ============================================================

def finalize_interview_plan(
    draft: InterviewPlanDraft,
    duration_minutes: int,
) -> InterviewPlan:

    final_targets: list[AssessmentTarget] = []

    # --------------------------------------------------------
    # target_index:
    #
    # 第一轮 = 1
    # 第二轮 = 2
    #
    # 然后 Python 自动生成:
    #
    # target_01
    # target_02
    # --------------------------------------------------------

    for target_index, target_draft in enumerate(
        draft.targets,
        start=1,
    ):

        target_id = (
            f"target_{target_index:02d}"
        )

        final_requirements: list[
            EvidenceRequirement
        ] = []

        # ----------------------------------------------------
        # 给当前 Target 内部每个 Evidence Requirement 编号
        #
        # 例如:
        #
        # target_01_req_01
        # target_01_req_02
        # target_01_req_03
        # ----------------------------------------------------

        for req_index, req_draft in enumerate(
            target_draft.evidence_requirements,
            start=1,
        ):

            requirement_id = (
                f"{target_id}_req_{req_index:02d}"
            )

            requirement = EvidenceRequirement(
                id=requirement_id,
                description=req_draft.description,
            )

            final_requirements.append(
                requirement
            )

        # ----------------------------------------------------
        # 构造最终 Target
        # ----------------------------------------------------

        final_target = AssessmentTarget(
            id=target_id,

            objective=target_draft.objective,

            target_type=target_draft.target_type,

            competency_ids=target_draft.competency_ids,

            evidence_requirements=final_requirements,

            related_claim_ids=target_draft.related_claim_ids,

            priority=target_draft.priority,

            must_cover=target_draft.must_cover,

            time_budget_minutes=(
                target_draft.time_budget_minutes
            ),

            preferred_modes=(
                target_draft.preferred_modes
            ),
        )

        final_targets.append(
            final_target
        )

    # --------------------------------------------------------
    # 构造最终 InterviewPlan
    # --------------------------------------------------------

    return InterviewPlan(
        duration_minutes=duration_minutes,

        max_questions=calculate_max_questions(
            duration_minutes
        ),

        closing_buffer_minutes=calculate_closing_buffer(
            duration_minutes
        ),

        targets=final_targets,
    )


# ============================================================
# 真正的 Planner Service
#
# Node 后面只需要调用这个函数.
# ============================================================

def build_interview_plan(
    competency_model: CompetencyModel,
    claim_registry: ClaimRegistry,
    duration_minutes: int,
    policy: InterviewPolicy = DEFAULT_INTERVIEW_POLICY,
) -> InterviewPlan:

    # --------------------------------------------------------
    # 1. 基础输入检查
    # --------------------------------------------------------

    if duration_minutes <= 0:
        raise ValueError(
            "面试时长必须大于 0 分钟"
        )

    # --------------------------------------------------------
    # 2. 算出最后预留几分钟
    # --------------------------------------------------------

    closing_buffer = calculate_closing_buffer(
        duration_minutes
    )

    available_minutes = (
        duration_minutes
        - closing_buffer
    )

    # --------------------------------------------------------
    # 3. Pydantic 对象转 JSON 文本
    #
    # LLM 最终看到的是文本, 不是 Python 对象.
    # --------------------------------------------------------

    competency_json = (
        competency_model.model_dump_json()
    )

    claim_json = (
        claim_registry.model_dump_json()
    )

    policy_json = (
        policy.model_dump_json()
    )

    # --------------------------------------------------------
    # 4. 构造 Prompt
    #
    # 这一段是真正的 Planner 业务规则.
    # --------------------------------------------------------

    messages = [
        (
            "system",
            """
你是 AI 技术招聘系统中的 Interview Planner.

你的任务是制定面试初始验证计划.

你不会生成具体面试问题.

你的输入包括:

1. CompetencyModel
2. ClaimRegistry
3. InterviewPolicy
4. 本次可用面试时间


你的输出是 InterviewPlanDraft, JSON 字段必须严格使用以下定义:

{
  "targets": [
    {
      "objective": "字符串",
      "target_type": "knowledge/implementation/debugging/system_design/problem_solving/experience_verification 之一",
      "competency_ids": ["competency_01"],
      "evidence_requirements": [
        {"description": "字符串"}
      ],
      "related_claim_ids": ["claim_01"],
      "priority": "high/medium/low 之一",
      "must_cover": true或false,
      "time_budget_minutes": 数字,
      "preferred_modes": ["foundation/project_deep_dive/scenario/system_design/coding/follow_up"]
    }
  ]
}

重要: evidence_requirements 必须是对象数组, 每个对象包含 description 字段, 不能是字符串数组!

重要: TargetType 与 QuestionMode 是两套完全不同的枚举:

- target_type 只能是 knowledge、implementation、debugging、system_design、problem_solving、experience_verification。
- target_type 严禁使用任何 QuestionMode。
- foundation、project_deep_dive、scenario、coding、follow_up 都只能出现在 preferred_modes。
- project_deep_dive 只能出现在 preferred_modes，不能写入 target_type。
- scenario 只能出现在 preferred_modes，不能写入 target_type。

输出前逐个检查每个 target，若 target_type 不属于上述六个 TargetType，必须自行修正后再输出。


==================================================
一、Competency 是主要规划依据
==================================================

CompetencyModel 描述:

- 岗位需要什么能力
- 哪些能力最重要
- 简历已经提供了哪些线索
- 目前还缺哪些 Evidence

应优先保证 core competency 得到充分验证.


==================================================
二、Claim 不是独立出题来源
==================================================

ClaimRegistry 表示候选人在简历中的具体声明.

不要:

一个 Claim
-> 一个 Target

如果 Claim 与某项 Competency 的验证目标高度相关,
应该把 Claim ID 放入:

related_claim_ids

使后续同一组问题可以同时获得:

Competency Evidence
+
Claim Evidence


例如:

Competency:
需要验证 LangGraph Workflow 实现能力

Claim:
候选人声称实现过 Resume / JD 并行

应该生成一个 implementation Target,
并关联这个 Claim.

不要再额外生成一个重复 Claim Target.


==================================================
三、Target 保持中等粒度
==================================================

不要:

一个 missing_evidence
=
一个 Target

例如:

- State 设计
- fan-out
- fan-in
- 并发状态更新

通常可以合并成:

Agent Workflow 设计与实现能力


但:

Implementation

和:

Debugging / Problem Solving

如果验证性质明显不同,
可以拆成不同 Target.


==================================================
四、Evidence Requirement
==================================================

每个 Target 都必须明确:

候选人需要表现出什么,
我们才能认为这一目标得到验证.

不要写:

进一步了解 LangGraph

应该写:

能够解释 fan-out 与 fan-in 的执行关系.


==================================================
五、题型
==================================================

preferred_modes 表示推荐验证方式，不表示固定题目。

例如：
Agent Architecture 可以使用：
- project_deep_dive 验证真实项目经历
- scenario 验证工程问题处理能力
- system_design 验证抽象设计能力

后续 Supervisor 会根据回答 Evidence 动态选择具体 QuestionMode。

==================================================
五、题型
==================================================

preferred_modes 只能从以下方式选择:

foundation
project_deep_dive
scenario
system_design
coding
follow_up

这些只是推荐验证方式.

不要生成具体问题.

不要提前规定:
第1题是什么、
第2题是什么.


==================================================
六、基础知识的处理
==================================================

require_core_foundation=True 的含义是:

核心能力必须获得基础理解层面的 Evidence.

它不意味着:
必须机械生成固定数量的 foundation 问题.

如果项目深挖等方式已经证明了基础知识,
也可以视为获得相应 Evidence.


==================================================
七、独立问题解决能力
==================================================

如果:

require_independent_problem_solving=True

则不能完全依赖候选人过去项目的自述.

对于核心能力,
应该保留能够验证候选人面对新问题时
分析、调试、设计能力的 Target.


==================================================
八、相关项目 / 实习
==================================================

如果:

deep_dive_relevant_experience=True

并且候选人存在与核心岗位能力高度相关的
项目或实习经历,

应该优先利用这些经历获得高价值 Evidence.


==================================================
九、时间预算
==================================================

time_budget_minutes 是初始软预算.

所有 Target 的预算总和
不能超过本次给出的可用评估时间.

高优先级、must_cover 的 Target
应该获得更多时间.

后续 Supervisor 可以动态调整预算.


==================================================
十、不要做
==================================================

不要:

- 生成具体问题
- 给候选人评分
- 判断是否录用
- 生成 target ID
- 生成 evidence requirement ID
- 为 Claim 单独机械创建重复 Target

请以 JSON 格式输出.
"""
        ),

        (
            "human",
            f"""
本次面试总时长:

{duration_minutes} 分钟


扣除结束预留后,
可用于核心评估的时间:

{available_minutes} 分钟


CompetencyModel:

{competency_json}


ClaimRegistry:

{claim_json}


InterviewPolicy:

{policy_json}


请生成 InterviewPlanDraft.
"""
        ),
    ]

    # --------------------------------------------------------
    # 5. 让 LLM 负责语义规划
    # --------------------------------------------------------

    draft: InterviewPlanDraft = (
        llm.structured(
            messages,
            InterviewPlanDraft,
        )
    )

    # --------------------------------------------------------
    # 6. Python 检查 Target 数量是否超过 Policy 上限
    # --------------------------------------------------------

    validate_target_count(
        draft=draft,
        policy=policy,
    )

    # 7. Python 检查 LLM 有没有乱引用不存在的 ID
    # --------------------------------------------------------

    validate_references(
        draft=draft,
        competency_model=competency_model,
        claim_registry=claim_registry,
    )

    # --------------------------------------------------------
    # 8. Python 检查时间预算
    # --------------------------------------------------------

    validate_time_budget(
        draft=draft,
        available_minutes=available_minutes,
    )

    # --------------------------------------------------------
    # 9. Python 负责最终编号和硬规则
    # --------------------------------------------------------

    final_plan = finalize_interview_plan(
        draft=draft,
        duration_minutes=duration_minutes,
    )

    return final_plan
