import json
from pathlib import Path
import unittest
from urllib.parse import urlparse

from profile_agent.schemas.report_schema import RoleCompetencyProfile
from profile_agent.services.role_profile_service import load_role_profile


class RoleProfileServiceTest(unittest.TestCase):
    def test_loads_ai_application_2026_h2_profile(self) -> None:
        profile = load_role_profile(
            "ai_application_engineering",
            "2026-H2",
        )

        self.assertIsInstance(profile, RoleCompetencyProfile)
        self.assertEqual(profile.role_family, "ai_application_engineering")
        self.assertEqual(profile.version, "2026-H2")

    def test_profile_has_six_frozen_dimensions(self) -> None:
        profile = load_role_profile(
            "ai_application_engineering",
            "2026-H2",
        )

        self.assertEqual(
            [dimension.id for dimension in profile.dimensions],
            [f"role_dim_{index:02d}" for index in range(1, 7)],
        )
        self.assertEqual(
            [dimension.name for dimension in profile.dimensions],
            [
                "Agent架构与任务编排",
                "业务理解与任务建模",
                "Context、RAG、Memory与工具工程",
                "AI协作开发与生产交付",
                "评测、可观测性与安全治理",
                "成本、性能与持续优化",
            ],
        )

    def test_ai_application_role_pack_uses_2026_h21_student_scope(self) -> None:
        profile = load_role_profile(
            "ai_application_engineering",
            "2026-H2",
        )

        self.assertEqual(profile.display_name, "AI Agent应用工程师（校招/初级）")
        self.assertEqual(
            [item.weight for item in profile.dimensions],
            [0.20, 0.15, 0.20, 0.15, 0.20, 0.10],
        )
        self.assertTrue(
            all(item.accepted_alternatives for item in profile.dimensions)
        )

    def test_weights_equal_20_15_20_15_20_10_percent(self) -> None:
        profile = load_role_profile(
            "ai_application_engineering",
            "2026-H2",
        )

        self.assertEqual(
            [dimension.weight for dimension in profile.dimensions],
            [0.20, 0.15, 0.20, 0.15, 0.20, 0.10],
        )

    def test_source_refs_resolve_to_auditable_registry(self) -> None:
        profile = load_role_profile(
            "ai_application_engineering",
            "2026-H2",
        )
        registry_path = (
            Path(__file__).resolve().parents[1]
            / "profile_agent"
            / "knowledge"
            / "role_packs"
            / "ai_application_engineer_2026_h2_sources.json"
        )
        sources = json.loads(registry_path.read_text("utf-8"))
        source_by_id = {source["id"]: source for source in sources}
        dimension_ids = {dimension.id for dimension in profile.dimensions}

        self.assertEqual(set(profile.source_refs), set(source_by_id))
        for source in sources:
            with self.subTest(source=source.get("id")):
                for field in (
                    "id",
                    "publisher",
                    "title",
                    "url",
                    "published_at",
                    "retrieved_at",
                    "role_level",
                    "supports_dimension_ids",
                ):
                    self.assertTrue(source.get(field), field)
                parsed_url = urlparse(source["url"])
                self.assertIn(parsed_url.scheme, {"http", "https"})
                self.assertTrue(parsed_url.netloc)
                self.assertTrue(
                    set(source["supports_dimension_ids"]).issubset(dimension_ids)
                )

        self.assertTrue(
            any("京东" in source["publisher"] for source in sources)
        )
        self.assertTrue(
            any("上海人工智能实验室" in source["publisher"] for source in sources)
        )
        self.assertTrue(
            any(
                "Anthropic" in source["publisher"]
                and "tool" in source["url"]
                for source in sources
            )
        )
        self.assertTrue(
            any(
                "Anthropic" in source["publisher"]
                and "eval" in source["url"]
                for source in sources
            )
        )

    def test_each_dimension_has_two_minimum_two_excellence_one_error(
        self,
    ) -> None:
        profile = load_role_profile(
            "ai_application_engineering",
            "2026-H2",
        )

        for dimension in profile.dimensions:
            with self.subTest(dimension=dimension.id):
                self.assertEqual(len(dimension.minimum_criteria), 2)
                self.assertEqual(len(dimension.excellence_signals), 2)
                self.assertEqual(len(dimension.critical_errors), 1)

    def test_gating_dimensions_are_01_02_05(self) -> None:
        profile = load_role_profile(
            "ai_application_engineering",
            "2026-H2",
        )

        self.assertEqual(
            {
                dimension.id
                for dimension in profile.dimensions
                if dimension.is_gating
            },
            {"role_dim_01", "role_dim_02", "role_dim_05"},
        )

    def test_unknown_role_or_version_is_rejected(self) -> None:
        cases = (
            ("unknown_role", "2026-H2"),
            ("ai_application_engineering", "2099-H1"),
        )

        for role_family, version in cases:
            with self.subTest(role_family=role_family, version=version):
                with self.assertRaisesRegex(
                    ValueError,
                    f"{role_family}/{version}",
                ):
                    load_role_profile(role_family, version)


if __name__ == "__main__":
    unittest.main()
