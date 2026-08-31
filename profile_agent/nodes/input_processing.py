"""Input Processing Node：做当前阶段最小必要清洗。"""

from profile_agent.state.main_state import MainState


def input_processing(state: MainState) -> dict:
    """清洗 Resume/JD，并返回局部 State 更新。"""

    resume = state.get("resume_text", "")
    clean_resume = resume.strip()
    if not clean_resume:
        raise ValueError("简历不能为空")

    jd = state.get("jd_text")
    clean_jd = jd.strip() if jd else None
    target_role = state.get("target_role", "").strip()

    return {
        "cleaned_resume_text": clean_resume,
        "cleaned_jd_text": clean_jd,
        "target_role": target_role,
    }
