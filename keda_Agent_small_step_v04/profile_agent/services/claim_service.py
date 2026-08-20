"""Claim Registry 的确定性 Service.

Resume Understanding 已经完成哪些声明值得验证的语义判断.
这里不再调用 LLM, 只做确定性登记:
    ResumeClaim -> claim_01 / claim_02 -> ClaimRegistry

这样 Claim ID 不会因为模型表达变化而随机生成.
"""

from profile_agent.schemas.claim_schema import ClaimItem, ClaimRegistry
from profile_agent.schemas.resume_schema import ResumeProfile


def build_claim_registry(resume_profile: ResumeProfile) -> ClaimRegistry:
    """把 ResumeProfile.claims_to_verify 转成可跨多题追踪的 ClaimRegistry."""

    claims: list[ClaimItem] = []

    # 先过滤空文本, 再编号, 避免出现 claim_01 缺失、直接从 claim_02 开始的情况.
    valid_claims = [
        resume_claim
        for resume_claim in resume_profile.claims_to_verify
        if resume_claim.text.strip()
    ]

    for index, resume_claim in enumerate(valid_claims, start=1):
        text = resume_claim.text.strip()

        claims.append(
            ClaimItem(
                id=f"claim_{index:02d}",
                text=text,
                source_section=resume_claim.source_section,
                claim_type=resume_claim.claim_type,
                # status / evidence ids 使用 Schema 默认值初始化.
            )
        )

    return ClaimRegistry(claims=claims)
