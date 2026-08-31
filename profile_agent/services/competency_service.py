"""Competency Modeling 的业务 Service.

这是当前流程第一次把:
    JobProfile (岗位要什么)
    + ResumeProfile (候选人简历给了什么 signal)
放到一起分析.

它不是评分器, 而是能力验证地图生成器.
最终正式 CompetencyItem 的 ID 由 Python 生成; LLM 只负责语义判断.
"""

from profile_agent.llm import llm
from profile_agent.schemas.competency_schema import (
    CompetencyDraftModel,
    CompetencyItem,
    CompetencyModel,
)
from profile_agent.schemas.job_schema import JobProfile
from profile_agent.schemas.resume_schema import ResumeProfile


def build_competency_model(
    resume_profile: ResumeProfile,
    job_profile: JobProfile,
) -> CompetencyModel:
    """联合 JobProfile + ResumeProfile, 生成正式 CompetencyModel."""

    resume_json = resume_profile.model_dump_json(indent=2)
    job_json = job_profile.model_dump_json(indent=2)

    messages = [
        (
            "system",
            """
你是互联网技术岗位招聘系统中的 Competency Modeling 模块.

你的任务不是给候选人评分, 而是建立后续面试要验证哪些岗位能力的能力地图.

输入:
- JobProfile: 岗位职责和明确要求;
- ResumeProfile: 候选人简历中的结构化经历、项目、技能和 Claim 线索.

请以 JSON 格式输出, 结构必须严格如下:

{
  "competencies": [
    {
      "name": "能力维度名称",
      "importance": "core/important/supplementary 之一",
      "target_expectation": "岗位希望候选人做到什么",
      "resume_signals": ["信号1", "信号2"],
      "missing_evidence": ["证据1", "证据2"]
    }
  ]
}

重要: resume_signals 和 missing_evidence 都必须是字符串数组, 不能是单个字符串!

[关键职责边界]
1. name / importance / target_expectation 主要由 JobProfile 决定;
2. resume_signals 只能来自 ResumeProfile, 不能脑补;
3. missing_evidence 要结合岗位要求和已有简历 signals 判断;
4. 简历中的技能、实习、项目和 Claim 都只是 signal, 不代表能力已经被证明;
5. 即使简历完全没写某项核心岗位能力, 只要岗位需要, 仍要保留该 competency,
   此时 resume_signals=[];
6. 不要把每个技术名词机械拆成一个 competency. 应形成对真实招聘判断有意义的中等粒度能力维度;
7. missing_evidence 也保持中等粒度, 例如 Workflow 状态与数据流设计、
   异常定位与调试能力, 不要展开成几十个 API/函数名;
8. 不评分, 不生成 target_level / estimated_level / gap, 不判断最终是否胜任.

注意: 不要生成 competency ID. ID 由 Python 在模型返回后确定性编号.
""".strip(),
        ),
        (
            "human",
            f"""
ResumeProfile:
{resume_json}

JobProfile:
{job_json}
""".strip(),
        ),
    ]

    # 第一步: LLM 只返回内容草稿, 不负责机器 ID.
    draft = llm.structured(messages, CompetencyDraftModel)

    # 第二步: Python 做确定性后处理.
    # 当前按 LLM 返回顺序生成 competency_01、competency_02...
    # 这些 ID 后续可供 InterviewPlan / Evidence 等稳定引用.
    competencies: list[CompetencyItem] = []
    for index, item in enumerate(draft.competencies, start=1):
        competencies.append(
            CompetencyItem(
                id=f"competency_{index:02d}",
                name=item.name.strip(),
                importance=item.importance,
                target_expectation=item.target_expectation.strip(),
                resume_signals=[x.strip() for x in item.resume_signals if x.strip()],
                missing_evidence=[x.strip() for x in item.missing_evidence if x.strip()],
            )
        )

    return CompetencyModel(competencies=competencies)
