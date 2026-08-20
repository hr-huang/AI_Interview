from profile_agent.state.main_state import (
    MainState,
)

from profile_agent.services.interview_planner_service import (
    build_interview_plan,
)


def interview_planner(
    state: MainState,
) -> dict:
    """
    LangGraph 中的 Interview Planner Node。

    这个 Node 自己不负责复杂规划。

    它只完成三件事：

    1. 从 State 读取已经完成的面试前建模结果；
    2. 调用 interview_planner_service；
    3. 把生成的 InterviewPlan 返回给 LangGraph。

    LangGraph Runtime 会把：

        {"interview_plan": plan}

    合并回当前 MainState。
    """

    # --------------------------------------------------------
    # 1. 读取 CompetencyModel
    # --------------------------------------------------------

    competency_model = state[
        "competency_model"
    ]

    # --------------------------------------------------------
    # 2. 读取 ClaimRegistry
    # --------------------------------------------------------

    claim_registry = state[
        "claim_registry"
    ]

    # --------------------------------------------------------
    # 3. 读取本次面试总时长
    #
    # 以后由前端让面试官选择：
    # 20 / 30 / 45 / 60 min
    #
    # 当前没有提供时，默认 30 分钟。
    # --------------------------------------------------------

    duration_minutes = state.get(
        "interview_duration_minutes",
        30,
    )

    # --------------------------------------------------------
    # 4. 调用真正的 Planner Service
    # --------------------------------------------------------

    plan = build_interview_plan(
        competency_model=competency_model,
        claim_registry=claim_registry,
        duration_minutes=duration_minutes,
    )

    # --------------------------------------------------------
    # 5. Node 只返回局部 State Update
    # --------------------------------------------------------

    return {
        "interview_plan": plan
    }