"""LangGraph 主共享 State.

MainState 是整条工作流当前携带哪些数据的类型说明.
它不是某个具体业务对象; ResumeProfile / JobProfile / CompetencyModel / ClaimRegistry
都是放在 State 中的业务数据块.

Node 的标准模式:
    从 state 读取自己需要的字段
        -> 调 Service
        -> return {本节点新产生的字段}

LangGraph Runtime 会把局部 return 合并回共享 State.
"""

from typing import TypedDict

from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.competency_schema import CompetencyModel
from profile_agent.schemas.interview_schema import (
    GeneratedQuestion,
    InterviewAction,
    InterviewPlan,
)
from profile_agent.schemas.job_schema import JobProfile
from profile_agent.schemas.resume_schema import ResumeProfile
from profile_agent.schemas.report_schema import AssessmentReport
from profile_agent.schemas.runtime_schema import (
    Evidence,
    InterviewRuntimeState,
    InterviewTurn,
)


class MainState(TypedDict, total=False):
    # 1) API/前端传入的原始输入
    resume_text: str
    jd_text: str | None
    target_role: str

    # 2) Input Processing 输出
    cleaned_resume_text: str
    cleaned_jd_text: str | None

    # 3) fan-out 两个 Understanding 节点的输出
    resume_profile: ResumeProfile
    job_profile: JobProfile

    # 4) 当前 Pre-Interview 建模输出
    competency_model: CompetencyModel
    claim_registry: ClaimRegistry

    # 5) InterviewPlan
    interview_duration_minutes: int
    # 面试官选择:
    # 20 / 30 / 45 / 60
    # 没有则 Node 默认 30.

    interview_plan: InterviewPlan

    # 6) 动态面试运行状态
    # 在候选人真正开始面试时初始化，不在 Pre-Interview Graph 中启动计时。
    runtime_state: InterviewRuntimeState
    interview_turns: list[InterviewTurn]
    evidences: list[Evidence]
    next_action: InterviewAction
    current_question: GeneratedQuestion | None
    current_turn_id: str | None

    # 7) 面试结束后的最终评估报告
    assessment_report: AssessmentReport
