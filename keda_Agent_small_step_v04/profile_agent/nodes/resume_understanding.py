"""Resume Understanding Node。

Node 只负责：State -> Service -> State update。
Prompt 与 LLM 细节放在 services/resume_service.py。
"""

from profile_agent.services.resume_service import parse_resume
from profile_agent.state.main_state import MainState


def resume_understanding(state: MainState) -> dict:
    clean_resume = state["cleaned_resume_text"]
    profile = parse_resume(clean_resume)
    return {"resume_profile": profile}
