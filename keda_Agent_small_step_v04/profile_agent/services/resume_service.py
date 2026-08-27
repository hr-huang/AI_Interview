"""Resume Understanding 的业务 Service.

职责只有一个:
    清洗后的自由文本简历 -> ResumeProfile

Service 不知道 LangGraph State, 也不决定后续题目.
特别注意: claims_to_verify 要扫描整份简历, 不是只看 projects.
"""

from profile_agent.llm import llm
from profile_agent.schemas.resume_schema import ResumeProfile


def parse_resume(clean_resume: str) -> ResumeProfile:
    """将清洗后的简历解析为结构化 ResumeProfile."""

    messages = [
        (
            "system",
            """
你是互联网技术岗位招聘系统中的简历理解模块.
当前版本优先服务 AI 应用 / Agent / 后端等互联网技术岗位, 但你的本阶段任务仍然只是:
准确理解简历写了什么, 不评价候选人最终能力.

请提取, JSON 字段名必须严格使用以下定义:
1. summary: 个人简介/自我评价; 没有则为 null.
2. education: 教育经历, list[str], 每项是一个字符串描述.
3. skills: 简历明确写出的技能, list[str].
4. work_experiences: 逐段结构化实习/工作经历, list[object], 每个对象包含:
   - company: str
   - role: str (岗位名称)
   - period: str | null (时间区间)
   - responsibilities: list[str]
   - achievements: list[str] (没有时用空列表 [], 不能用 null)
   - technologies: list[str]
   - company 和 role 未知时必须使用空字符串 ""，不能使用 null；period 未知时才允许 null.
5. projects: 逐个结构化项目, list[object], 每个对象包含:
   - name: str
   - description: str
   - responsibilities: list[str]
   - achievements: list[str] (没有时用空列表 [], 不能用 null)
   - technologies: list[str]
6. other_experiences: 其他暂不适合归入工作/项目的岗位相关经历, list[str].
7. claims_to_verify: 从整份简历筛选的重要可核验声明, list[object], 每个对象包含:
   - text: str (声明内容, 不是 claim)
   - source_section: str (来源区域, 只能是: summary/skills/work_experience/project/education/achievement/award/certification/other)
   - claim_type: str (声明类型, 只能是: skill/experience/responsibility/achievement/scale/leadership/credential/other)
8. uncertainties: 简历中的歧义、缺失、矛盾或无法确认之处, list[str].

[claims_to_verify 的来源]
必须扫描整份简历, 而不是只从项目经历抽取. Claim 可以来自:
- summary / 自我评价, 例如具备复杂 Agent 系统设计能力;
- skills, 例如熟练掌握 LangGraph;
- work_experience, 例如独立负责整体架构设计;
- project, 例如实现多 Agent 并行;
- achievement, 例如效率提升 40%;
- award / certification, 例如某奖项或证书;
- 其他与招聘判断明显相关的具体声明.

[哪些话适合成为 Claim]
如果一条话真或假会明显影响招聘判断, 且仅凭简历本身无法充分确认, 就值得登记.
不要把每一句普通描述都机械登记成 Claim.

[重要边界]
- ResumeProfile 中的内容都仍然只是简历事实/候选人自述, 不是 Interview Evidence.
- 不得编造简历没有的信息.
- 不给分, 不生成 Level, 不判断最终是否胜任.
""".strip(),
        ),
        (
            "human",
            f"""
请解析下面的候选人简历, 以 JSON 格式输出:

{clean_resume}
""".strip(),
        ),
    ]

    return llm.structured(messages, ResumeProfile)


# 兼容早期练习中的函数名; 新代码统一推荐 parse_resume.
def jiexi(clean_resume: str) -> ResumeProfile:
    return parse_resume(clean_resume)
