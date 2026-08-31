"""Claim Registry 的正式数据结构。

Claim 与 Competency 分开存：
- Competency：最终要判断“候选人是否具备某项岗位能力”；
- Claim：最终要判断“候选人简历中的某句具体声明得到多少支持”。

它们后续会在 InterviewPlanner/Question/Evidence 阶段合流，但不会各自独立出一套题。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .resume_schema import ClaimSourceSection, ClaimType


ClaimStatus = Literal[
    "unverified",
    "partially_supported",
    "supported",
    "contradicted",
]


class ClaimItem(BaseModel):
    id: str = Field(description="claim_01 形式的确定性 ID，由 Python 生成。")
    text: str = Field(description="简历中的具体声明。")
    source_section: ClaimSourceSection = Field(description="声明来自简历哪个区域。")
    claim_type: ClaimType = Field(description="skill/responsibility/achievement 等声明类型。")
    status: ClaimStatus = Field(default="unverified")
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)


class ClaimRegistry(BaseModel):
    claims: list[ClaimItem] = Field(default_factory=list)
