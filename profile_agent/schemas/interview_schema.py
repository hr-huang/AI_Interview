import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator


_CANDIDATE_FOCUS_START = re.compile(
    r"^(请说明|请描述|请|如何|为什么|为何|能否|是否|你会|"
    r"验证|考察|分析|说明|描述|必须|直接|采用|使用|通过|保证|"
    r"确保|应该|应当|先|再)"
)
_CANDIDATE_FOCUS_STATEMENT = re.compile(r"(实际|已经|只是|但|却|正在)")
_CANDIDATE_FOCUS_SHAPE = re.compile(r"^[\u3400-\u9fffA-Za-z0-9][\u3400-\u9fffA-Za-z0-9 -]*$")


def normalize_candidate_focus(value: Any) -> str | None:
    """Return a bounded neutral noun phrase, or fail closed to ``None``."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("candidate_focus must be a string or null")
    if any(character.isspace() and character != " " for character in value):
        return None
    normalized = " ".join(part for part in value.strip().split(" ") if part)
    if not normalized or len(normalized) > 40:
        return None
    if not _CANDIDATE_FOCUS_SHAPE.fullmatch(normalized):
        return None
    if _CANDIDATE_FOCUS_START.match(normalized) or _CANDIDATE_FOCUS_STATEMENT.search(normalized):
        return None
    return normalized


# ============================================================
# 1. 系统允许使用的“题型 / 验证方式”
# ============================================================

QuestionMode = Literal[
    "foundation",          # 基础知识 / 八股
    "project_deep_dive",   # 项目、实习经历深挖
    "scenario",            # 场景题
    "system_design",       # 系统设计
    "coding",              # 代码 / 伪代码 / 实现思路
    "follow_up",           # 根据上一轮回答动态追问
]


class AskAction(BaseModel):
    action: Literal["ask"] = "ask"
    target_id: str
    primary_requirement_id: str
    question_mode: QuestionMode
    reason: str


class FinishAction(BaseModel):
    action: Literal["finish"] = "finish"
    reason: str


InterviewAction = Annotated[
    AskAction | FinishAction,
    Field(discriminator="action"),
]


class GeneratedQuestion(BaseModel):
    text: str


# ============================================================
# 2. AssessmentTarget 本身属于哪类验证目标
#
# 注意：
# TargetType != QuestionMode
#
# debugging 是“我要验证什么”
# scenario 是“我用什么方式验证”
# ============================================================

TargetType = Literal[
    "knowledge",
    "implementation",
    "debugging",
    "system_design",
    "problem_solving",
    "experience_verification",
]


# ============================================================
# 3. 面试的全局规则
#
# Policy 不规定：
# “必须两道八股、两道场景”
#
# Policy 规定的是：
# “哪些类型的 Evidence 必须得到”
# ============================================================

class InterviewPolicy(BaseModel):

    # ==============================
    # 面试规模约束
    # ==============================
    # 防止 Planner 生成过多 Target，导致后续 Supervisor 无法控制。
    max_targets: int = 5

    # 最大问题数量安全上限。
    # 注意：不是必须问这么多题。
    max_questions: int = 10

    # 是否优先保证核心 Competency。
    prioritize_core_competencies: bool = True

    # 是否允许把高度相关能力合并到同一个 Target。
    # 例如 Workflow / State / fan-out 通常属于 Agent Architecture。
    allow_merge_targets: bool = True

    require_core_foundation: bool = True
    # 核心能力需要获得基础理解层面的证据。
    # 不代表必须额外问一题 foundation。
    #
    # 如果项目深挖已经证明基础知识，也可以算覆盖。

    require_independent_problem_solving: bool = True
    # 不能完全依赖候选人的简历自述。
    # 至少需要验证候选人面对新问题时的分析能力。

    deep_dive_relevant_experience: bool = True
    # 如果候选人存在和岗位高度相关的项目 / 实习，
    # 应该进行深挖。

    verify_relevant_claims: bool = True
    # 和招聘判断高度相关的 Claim 应尽量验证。
    #
    # 但 Claim 不应该单独机械生成一道题，
    # 应优先挂载到相关能力 Target 中共同验证。

# ============================================================
# 4. LLM 输出阶段使用的 EvidenceRequirement
#
# Draft 没有 ID。
# LLM 只负责描述“需要什么 Evidence”。
# ============================================================

class EvidenceRequirementDraft(BaseModel):

    description: str

    candidate_focus: str | None = None

    @field_validator("candidate_focus", mode="before")
    @classmethod
    def normalize_candidate_focus(cls, value: Any) -> str | None:
        return normalize_candidate_focus(value)

    planned_role_dimension_id: str | None = None
    # Planner 声明这条证据主要覆盖哪个 Role Pack 维度。
    # 正式 Planner 输出会由 Python 校验；None 仅保留旧数据兼容。

    requires_transfer_validation: bool = False
    # True 表示必须脱离简历原项目，用约束不同的新场景验证迁移能力。


# ============================================================
# 5. LLM 输出阶段使用的 AssessmentTarget
#
# 注意：
# 这里也没有 target_id。
#
# target_01 / target_02 属于确定性编号，
# 后面交给 Python 生成。
# ============================================================

class AssessmentTargetDraft(BaseModel):

    objective: str
    # 这个 Target 到底想证明什么。

    target_type: TargetType
    # knowledge / implementation / debugging ...

    competency_ids: list[str]
    # 关联已经存在的 competency ID。
    #
    # 例如：
    # ["competency_01"]

    evidence_requirements: list[EvidenceRequirementDraft]
    # 完成这个 Target 需要获得哪些证据。

    related_claim_ids: list[str]
    # 可以顺便验证哪些 Claim。
    #
    # 例如：
    # ["claim_01", "claim_02"]

    priority: Literal[
        "high",
        "medium",
        "low",
    ]

    must_cover: bool
    # 时间不足时是否仍然必须覆盖。

    time_budget_minutes: int
    # Planner 初始预计给这个 Target 几分钟。
    #
    # 注意：只是软预算，后面的 Supervisor 可以动态调整。

    preferred_modes: list[QuestionMode]
    # 推荐验证方式。
    #
    # 不是：
    # “必须严格按照这个题型顺序问”。


# ============================================================
# 6. LLM 的完整输出
# ============================================================

class InterviewPlanDraft(BaseModel):

    targets: list[AssessmentTargetDraft]


# ============================================================
# 7. Python 后处理后真正使用的 EvidenceRequirement
# ============================================================

class EvidenceRequirement(BaseModel):

    id: str
    # 例如：
    # target_01_req_01

    description: str

    candidate_focus: str | None = None

    @field_validator("candidate_focus", mode="before")
    @classmethod
    def normalize_candidate_focus(cls, value: Any) -> str | None:
        return normalize_candidate_focus(value)

    planned_role_dimension_id: str | None = None

    requires_transfer_validation: bool = False


# ============================================================
# 8. Python 后处理后真正使用的 AssessmentTarget
# ============================================================

class AssessmentTarget(BaseModel):

    id: str
    # target_01

    objective: str

    target_type: TargetType

    competency_ids: list[str]

    evidence_requirements: list[EvidenceRequirement]

    related_claim_ids: list[str]

    priority: Literal[
        "high",
        "medium",
        "low",
    ]

    must_cover: bool

    time_budget_minutes: int

    preferred_modes: list[QuestionMode]


# ============================================================
# 9. 最终 InterviewPlan
#
# 这是后面 Supervisor 真正读取的对象。
# ============================================================

class InterviewPlan(BaseModel):

    duration_minutes: int
    # 面试官选择的总时间。
    # 例如 30。

    max_questions: int
    # 防止 Agent 无限追问的安全上限。
    #
    # 注意：
    # max_questions=10
    # 不代表必须问 10 道题。

    closing_buffer_minutes: int
    # 为结束、总结等预留时间。

    targets: list[AssessmentTarget]
    # 这场面试真正需要完成的验证任务。
