"""Job Understanding 的业务 Service.

这一层只整理 JD 明确写了什么.
不要在这里提前给 competency 打 importance、候选人评分或生成面试题; 这些属于后续.
"""

from profile_agent.llm import llm
from profile_agent.schemas.job_schema import JobProfile


def parse_job(clean_jd: str, target_role: str = "") -> JobProfile:
    """将清洗后的 JD 解析成 JobProfile."""

    messages = [
        (
            "system",
            """
你是互联网技术岗位招聘系统中的 Job Understanding 模块.

你的任务是把 JD 中的岗位事实结构化, 而不是评价候选人, 也不是提前建立完整 CompetencyModel.

请提取, JSON 字段名必须严格使用以下定义:
- role: str (岗位名称)
- responsibilities: list[str] (JD 明确写出的主要职责)
- requirements: list[object] (JD 明确写出的任职要求, 每个对象包含 name: str + description: str)
- uncertainties: list[str] (JD 中冲突、模糊或明显缺失的信息)

规则:
1. 只依据 JD 和 target_role 的语境, 不凭空补充 JD 没有写出的硬性要求.
2. 当前不在 JobRequirement 中生成 importance; importance 在下一层 Competency Modeling
   综合职责、要求和 JD 上下文判断, 避免两层重复建模.
3. target_role 与 JD 冲突时, 以 JD 为主, 并把冲突写入 uncertainties.
4. 不生成候选人分数、Level、gap、题目.
""".strip(),
        ),
        (
            "human",
            f"""
目标岗位 (可能为空):
{target_role}

岗位 JD:
{clean_jd}

请以 JSON 格式输出.
""".strip(),
        ),
    ]

    return llm.structured(messages, JobProfile)
