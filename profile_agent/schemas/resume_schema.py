"""Resume Understanding 的结构化数据模型。

关键原则：ResumeProfile 表达“简历写了什么”，不是“候选人真实能力已经被证明”。

这版相对旧版最重要的升级：
1. 实习/工作经历不再是 list[str]，而是 WorkExperience；
2. 项目不再是 list[str]，而是 ProjectExperience；
3. Claim 不再只是字符串，而是 ResumeClaim，并记录来源 section 与 claim_type；
4. claims_to_verify 必须扫描整份简历，而不是只从 projects 抽取。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ClaimSourceSection = Literal[
    "summary",
    "skills",
    "work_experience",
    "project",
    "education",
    "achievement",
    "award",
    "certification",
    "other",
]

ClaimType = Literal[
    "skill",
    "experience",
    "responsibility",
    "achievement",
    "scale",
    "leadership",
    "credential",
    "other",
]


class WorkExperience(BaseModel):
    """一段实习/工作经历。

    把“公司 → 岗位 → 本人做了什么 → 用了什么技术 → 取得什么结果”保存在同一个
    对象里，避免旧版 list[str] 把这些关系拆散。
    """

    company: str = Field(description="公司/组织名称；简历未明确时可留空字符串。")
    role: str = Field(description="岗位/实习职位名称；简历未明确时可留空字符串。")
    period: str | None = Field(
        default=None,
        description="时间区间，尽量保留简历原写法，例如 2026.05-2026.08。",
    )
    responsibilities: list[str] = Field(
        default_factory=list,
        description="该段经历中候选人明确写出的职责、任务和本人完成的事情。",
    )
    achievements: list[str] = Field(
        default_factory=list,
        description="该段经历中明确写出的结果、指标、产出或业务成果。",
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="明确与这段经历相关联的技术/工具，不要把全局技能表全部复制进来。",
    )


class ProjectExperience(BaseModel):
    """一个项目经历。"""

    name: str = Field(description="项目名称；若简历没有正式名称，生成简短描述性名称即可。")
    description: str = Field(
        default="",
        description="项目做什么、解决什么问题的简短背景。",
    )
    responsibilities: list[str] = Field(
        default_factory=list,
        description="候选人在该项目中明确声称由本人承担的工作。",
    )
    achievements: list[str] = Field(
        default_factory=list,
        description="项目中明确写出的结果、指标、效果、产出。",
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="明确与该项目相关联的技术栈/工具。",
    )


class ResumeClaim(BaseModel):
    """Resume Understanding 识别出的一条“值得后续核验的具体声明”。

    注意：进入这里并不意味着系统怀疑候选人造假；只是说明这句话：
    - 对招聘判断有价值；
    - 仅靠简历不能确认；
    - 后续值得通过正常面试问题顺带收集证据。
    """

    text: str = Field(description="尽量保留简历原意的具体可核验声明。")
    source_section: ClaimSourceSection = Field(
        description="声明来自简历哪个区域，允许来自自我介绍、技能、实习、项目、奖项等。"
    )
    claim_type: ClaimType = Field(
        description="声明性质，用于后续选择更合适的验证证据，而不是直接决定题型。"
    )


class ResumeProfile(BaseModel):
    """整份简历的结构化画像。"""

    summary: str | None = Field(
        default=None,
        description="个人简介/自我评价/summary；没有则为 None。",
    )
    education: list[str] = Field(
        default_factory=list,
        description="教育经历，目前保持中等粒度文本即可。",
    )
    skills: list[str] = Field(
        default_factory=list,
        description="简历明确写出的技能或技术。",
    )
    work_experiences: list[WorkExperience] = Field(
        default_factory=list,
        description="结构化实习/工作经历。",
    )
    projects: list[ProjectExperience] = Field(
        default_factory=list,
        description="结构化项目经历。",
    )
    other_experiences: list[str] = Field(
        default_factory=list,
        description="暂不适合归入工作/项目的竞赛、社团等岗位相关经历。",
    )
    claims_to_verify: list[ResumeClaim] = Field(
        default_factory=list,
        description=(
            "从整份简历中筛选出的重要可核验声明；来源不限于项目，也包括 summary、skills、"
            "work_experience、award 等。不要机械把每句简历文字都变成 Claim。"
        ),
    )
    uncertainties: list[str] = Field(
        default_factory=list,
        description="简历内部缺失、矛盾、指代不清或仅凭文本无法确定的信息。",
    )
