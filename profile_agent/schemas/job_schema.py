"""Job Understanding 的结构化数据模型。

这一层尽量保持“事实提取”：JD 写了什么就结构化什么。
真正的 competency importance / target_expectation 等“能力建模判断”放到下一层
Competency Modeling，避免 Job Understanding 和 Competency Modeling 各做一遍相同工作。
"""

from pydantic import BaseModel, Field


class JobRequirement(BaseModel):
    """JD 中一条结构化任职要求。"""

    name: str = Field(
        description="简短标准化名称，例如 Python、Agent Workflow、Tool Calling。"
    )
    description: str = Field(
        description="忠实表达 JD 对这项要求的实际内容，不擅自补充 JD 没有写出的硬条件。"
    )


class JobProfile(BaseModel):
    """Job Understanding 的输出，只描述岗位本身。"""

    role: str = Field(description="岗位名称。")
    responsibilities: list[str] = Field(
        default_factory=list,
        description="JD 明确写出的主要工作职责。",
    )
    requirements: list[JobRequirement] = Field(
        default_factory=list,
        description="JD 明确写出的结构化任职要求。",
    )
    uncertainties: list[str] = Field(
        default_factory=list,
        description="JD 内部无法确定、冲突或明显缺失的信息。",
    )
