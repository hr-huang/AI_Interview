"""Competency Modeling 的数据结构。

CompetencyModel 已经从最早的“预评分表”升级为“能力验证地图”：

旧思路（已废弃）：
    target_level=4, estimated_level=2, gap=2
问题：面试前证据不足，却制造了过早、虚假的精确评分。

当前思路：
    岗位希望什么 + 简历有哪些 signal + 后面还缺什么 evidence

此外，最终 CompetencyItem 的 id 由 Python 确定性生成，不让 LLM 自由生成 ID。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Importance = Literal["core", "important", "supplementary"]


class CompetencyDraftItem(BaseModel):
    """LLM 负责生成的能力内容草稿，不含机器主键 ID。"""

    name: str = Field(description="有真实招聘意义的能力维度，例如 Agent Architecture。")
    importance: Importance = Field(
        description="该能力对目标岗位的重要程度，主要依据 JobProfile 判断。"
    )
    target_expectation: str = Field(
        description="岗位希望候选人在该能力上真正做到什么，用自然语言表达。"
    )
    resume_signals: list[str] = Field(
        default_factory=list,
        description="简历为该能力提供的明确线索/自述，不是已经验证的 Evidence。",
    )
    missing_evidence: list[str] = Field(
        default_factory=list,
        description=(
            "仅凭简历还无法确认、后续面试需要获得的中等粒度证据；"
            "不要机械拆成几十个 API/知识点。"
        ),
    )


class CompetencyDraftModel(BaseModel):
    """LLM 的结构化输出。"""

    competencies: list[CompetencyDraftItem] = Field(default_factory=list)


class CompetencyItem(BaseModel):
    """进入系统 State 后的正式能力对象。"""

    id: str = Field(
        description="稳定引用 ID，例如 competency_01；由 Python 生成而不是 LLM 生成。"
    )
    name: str
    importance: Importance
    target_expectation: str
    resume_signals: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class CompetencyModel(BaseModel):
    competencies: list[CompetencyItem] = Field(default_factory=list)
