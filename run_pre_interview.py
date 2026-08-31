"""真实运行当前 Pre-Interview Graph.

使用前:
    pip install -e .
    在 .env 填写 DEEPSEEK_API_KEY

运行:
    python run_pre_interview.py
"""

from pprint import pprint

from profile_agent.graphs.pre_interview import pre_interview_graph


if __name__ == "__main__":
    initial_state = {
        "resume_text": """
张三, 本科, 数据科学专业.
个人简介: 具备 AI 应用开发经验, 熟悉 LangGraph.

实习经历:
A科技有限公司, AI应用开发实习生, 2026.05-2026.08.
参与招聘智能体开发, 使用 LangGraph 搭建 Resume Understanding 与 Job Understanding 并行流程,
使用 FastAPI 开发后端接口; 参与优化后处理效率提升 40%.

项目经历:
招聘智能体: 本人负责整体 Workflow 设计, 使用 Python、LangGraph、FastAPI.
""",
        "jd_text": """
AI Agent 开发工程师:
负责 Agent 应用开发和 Workflow 设计;
要求熟练使用 Python, 能够独立设计 Agent Workflow, 熟悉 LangGraph, 理解 Tool Calling,
并具备实际故障定位能力.
""",
        "target_role": "AI Agent 开发工程师",
    }

    result = pre_interview_graph.invoke(initial_state)

    print("\n=== ResumeProfile: 整份简历结构化结果 ===")
    pprint(result["resume_profile"].model_dump())

    print("\n=== JobProfile: JD 事实结构 ===")
    pprint(result["job_profile"].model_dump())

    print("\n=== CompetencyModel: 能力验证地图 ===")
    pprint(result["competency_model"].model_dump())

    print("\n=== ClaimRegistry: 待核验具体声明 ===")
    pprint(result["claim_registry"].model_dump())

    print("\n=== InterviewPlan: 面试计划 ===")
    pprint(result["interview_plan"].model_dump())
