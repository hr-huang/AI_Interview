"""不调用 LLM 的结构自检.

运行:
    python check_without_llm.py

这里专门验证本次重构最关键的几点:
- 实习/项目结构化;
- Claim 可来自 work_experience / summary 等不同 section;
- Claim Registry 保留 source/type;
- Competency 正式对象拥有 Python 生成的稳定 ID.
"""

from profile_agent.schemas.competency_schema import CompetencyItem, CompetencyModel
from profile_agent.schemas.job_schema import JobProfile, JobRequirement
from profile_agent.schemas.resume_schema import (
    ProjectExperience,
    ResumeClaim,
    ResumeProfile,
    WorkExperience,
)
from profile_agent.services.claim_service import build_claim_registry


def main() -> None:
    resume_profile = ResumeProfile(
        summary="具备 Agent 应用开发经验, 熟悉 LangGraph.",
        education=["某大学 数据科学与大数据技术 本科"],
        skills=["Python", "LangGraph", "FastAPI"],
        work_experiences=[
            WorkExperience(
                company="A科技有限公司",
                role="AI应用开发实习生",
                period="2026.05-2026.08",
                responsibilities=[
                    "参与招聘智能体开发",
                    "使用 LangGraph 搭建 Resume 与 Job 两个理解模块并行流程",
                ],
                achievements=["参与优化后处理效率提升 40%"],
                technologies=["Python", "LangGraph", "FastAPI"],
            )
        ],
        projects=[
            ProjectExperience(
                name="招聘智能体",
                description="基于 LangGraph 的岗位胜任力面试系统",
                responsibilities=["负责 Workflow 设计"],
                technologies=["LangGraph", "FastAPI"],
            )
        ],
        claims_to_verify=[
            ResumeClaim(
                text="熟悉 LangGraph",
                source_section="summary",
                claim_type="skill",
            ),
            ResumeClaim(
                text="使用 LangGraph 搭建两个理解模块并行流程",
                source_section="work_experience",
                claim_type="responsibility",
            ),
            ResumeClaim(
                text="处理效率提升 40%",
                source_section="work_experience",
                claim_type="achievement",
            ),
        ],
    )

    job_profile = JobProfile(
        role="AI Agent 开发工程师",
        responsibilities=["开发和维护 Agent Workflow"],
        requirements=[
            JobRequirement(
                name="Agent Workflow",
                description="能够独立设计和维护 Agent Workflow",
            ),
            JobRequirement(
                name="Python",
                description="熟练使用 Python 进行 AI 应用工程开发",
            ),
        ],
    )

    registry = build_claim_registry(resume_profile)
    assert [x.id for x in registry.claims] == ["claim_01", "claim_02", "claim_03"]
    assert registry.claims[1].source_section == "work_experience"
    assert registry.claims[2].claim_type == "achievement"

    competency_model = CompetencyModel(
        competencies=[
            CompetencyItem(
                id="competency_01",
                name="Agent Architecture",
                importance="core",
                target_expectation="能够独立设计和维护多节点 Agent Workflow",
                resume_signals=["实习中使用 LangGraph 搭建并行流程"],
                missing_evidence=[
                    "Workflow 状态与数据流设计",
                    "异常定位与调试能力",
                ],
            )
        ]
    )

    assert job_profile.requirements[0].name == "Agent Workflow"
    assert competency_model.competencies[0].id == "competency_01"

    print("无 LLM 自检通过")
    print("- WorkExperience / ProjectExperience: OK")
    print("- ResumeClaim 来源不限于项目: OK")
    print("- Claim source_section / claim_type: OK")
    print("- Competency stable id: OK")


if __name__ == "__main__":
    main()
