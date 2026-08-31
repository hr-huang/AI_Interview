from pathlib import Path

from profile_agent.schemas.report_schema import RoleCompetencyProfile


_ROLE_PACKS = {
    ("ai_application_engineering", "2026-H2"):
        "ai_application_engineer_2026_h2.json",
}


def load_role_profile(role_family: str, version: str) -> RoleCompetencyProfile:
    filename = _ROLE_PACKS.get((role_family, version))
    if filename is None:
        raise ValueError(f"不存在的 Role Pack: {role_family}/{version}")

    path = (
        Path(__file__).resolve().parents[1]
        / "knowledge"
        / "role_packs"
        / filename
    )
    return RoleCompetencyProfile.model_validate_json(path.read_text("utf-8"))
