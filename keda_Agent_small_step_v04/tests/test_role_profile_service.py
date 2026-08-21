import unittest

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
                "AI应用与Agent编排",
                "业务理解与任务建模",
                "Context、RAG与工具集成",
                "AI原生工程交付",
                "可靠性、评测与安全",
                "系统思维与持续进化",
            ],
        )

    def test_weights_equal_25_15_15_15_20_10_percent(self) -> None:
        profile = load_role_profile(
            "ai_application_engineering",
            "2026-H2",
        )

        self.assertEqual(
            [dimension.weight for dimension in profile.dimensions],
            [0.25, 0.15, 0.15, 0.15, 0.20, 0.10],
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
