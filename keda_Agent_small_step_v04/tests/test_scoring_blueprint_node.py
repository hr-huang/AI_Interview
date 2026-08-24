from unittest.mock import patch
import unittest

from profile_agent.schemas.report_schema import ScoringBlueprint


class ScoringBlueprintNodeTest(unittest.TestCase):
    def test_node_builds_and_returns_frozen_blueprint(self) -> None:
        try:
            from profile_agent.nodes import scoring_blueprint as node_module
        except ImportError as exc:
            self.fail(f"scoring blueprint node is missing: {exc}")

        plan = object()
        profile = object()
        expected = ScoringBlueprint(
            role_family="ai_application_engineering",
            role_profile_version="2026-H2",
            bindings=[],
        )

        with (
            patch.object(node_module, "load_role_profile", return_value=profile) as loader,
            patch.object(
                node_module,
                "build_scoring_blueprint",
                return_value=expected,
            ) as builder,
        ):
            result = node_module.scoring_blueprint({"interview_plan": plan})

        loader.assert_called_once_with("ai_application_engineering", "2026-H2")
        builder.assert_called_once_with(plan, profile)
        self.assertEqual(result, {"scoring_blueprint": expected})


if __name__ == "__main__":
    unittest.main()
