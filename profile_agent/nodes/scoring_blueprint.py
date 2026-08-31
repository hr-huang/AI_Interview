from profile_agent.services.role_profile_service import load_role_profile
from profile_agent.services.scoring_blueprint_service import (
    build_scoring_blueprint,
)
from profile_agent.state.main_state import MainState


def scoring_blueprint(state: MainState) -> dict:
    profile = load_role_profile("ai_application_engineering", "2026-H2")
    blueprint = build_scoring_blueprint(state["interview_plan"], profile)
    return {"scoring_blueprint": blueprint}
