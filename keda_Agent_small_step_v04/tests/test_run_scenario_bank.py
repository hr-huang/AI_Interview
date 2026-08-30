from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

import run_scenario_bank


class RunScenarioBankTests(TestCase):
    def test_validate_is_offline_and_reports_frozen_counts(self) -> None:
        with patch(
            "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
            side_effect=AssertionError("validate must not construct provider"),
        ):
            result = run_scenario_bank.main(["validate"])
        self.assertEqual(result, 0)

    def test_rebuild_requires_scenario_qdrant_configuration(self) -> None:
        with (
            patch.dict(run_scenario_bank.os.environ, {}, clear=True),
            patch.object(run_scenario_bank, "load_dotenv") as load_dotenv,
        ):
            result = run_scenario_bank.main(["rebuild-index", "--apply"])
        self.assertEqual(result, 2)
        load_dotenv.assert_called_once_with()

    def test_rebuild_preview_does_not_construct_provider(self) -> None:
        with patch(
            "profile_agent.services.siliconflow_embedding_service.SiliconFlowEmbeddingClient.from_env",
            side_effect=AssertionError("preview must not construct provider"),
        ):
            result = run_scenario_bank.main(["rebuild-index"])
        self.assertEqual(result, 0)


if __name__ == "__main__":
    import unittest

    unittest.main()
