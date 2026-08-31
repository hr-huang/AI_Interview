"""Pre-Interview 建模 Node。

该节点在 Resume Understanding 与 Job Understanding 都完成后执行：
1. ResumeProfile + JobProfile -> CompetencyModel
2. ResumeProfile.claims_to_verify -> ClaimRegistry

Competency 与 Claim 分开存，但后续 InterviewPlanner 应合流规划，避免各自出一套重复题。
"""

from profile_agent.services.claim_service import build_claim_registry
from profile_agent.services.competency_service import build_competency_model
from profile_agent.state.main_state import MainState


def competency_modeling(state: MainState) -> dict:
    resume_profile = state["resume_profile"]
    job_profile = state["job_profile"]

    competency_model = build_competency_model(
        resume_profile=resume_profile,
        job_profile=job_profile,
    )
    claim_registry = build_claim_registry(resume_profile)

    return {
        "competency_model": competency_model,
        "claim_registry": claim_registry,
    }
