"""Job Understanding Node。

当前只实现“有 JD”路径。未来无 JD 时再增加 Role KB / RAG / Search 条件路由。
"""

from profile_agent.services.job_service import parse_job
from profile_agent.state.main_state import MainState


def job_understanding(state: MainState) -> dict:
    clean_jd = state.get("cleaned_jd_text")
    target_role = state.get("target_role", "")

    if not clean_jd:
        raise ValueError(
            "当前版本 Job Understanding 暂时要求提供 JD；"
            "无 JD 的 Role KB / RAG / Search 路径尚未实现。"
        )

    profile = parse_job(clean_jd=clean_jd, target_role=target_role)
    return {"job_profile": profile}
