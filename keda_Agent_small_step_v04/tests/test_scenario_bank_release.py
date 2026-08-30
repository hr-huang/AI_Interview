from collections import Counter
from datetime import date
from pathlib import Path
import unittest

from profile_agent.services.scenario_bank_service import ScenarioCatalog


ROOT = Path(__file__).resolve().parents[1] / "profile_agent" / "knowledge" / "scenario_banks" / "ai_application_engineering_2026_h2"
EXPECTED_SCENARIOS = {
    "ecommerce_service", "travel_planner", "enterprise_cost_monitor",
    "enterprise_knowledge_assistant", "marketing_operations", "recruitment_interview",
    "coding_review_agent", "enterprise_data_analysis", "it_operations", "sales_followup",
}
EXPECTED_MODULES = {
    "ecommerce_agent_architecture", "ecommerce_context_tools", "ecommerce_safety_evaluation", "ecommerce_performance_cost",
    "travel_agent_architecture", "travel_business_modeling", "travel_context_tools", "travel_cost_optimization",
    "cost_monitor_business_modeling", "cost_monitor_observability", "cost_monitor_performance",
    "knowledge_rag_memory", "knowledge_production_delivery", "knowledge_security_evaluation", "knowledge_cost_performance",
    "marketing_business_modeling", "marketing_ai_delivery", "marketing_safety_evaluation",
    "recruitment_agent_architecture", "recruitment_business_modeling", "recruitment_context_memory", "recruitment_safety_evaluation",
    "coding_agent_architecture", "coding_ai_delivery", "coding_security_evaluation", "coding_cost_performance",
    "data_analysis_business_modeling", "data_analysis_context_tools", "data_analysis_ai_delivery",
    "itops_agent_architecture", "itops_observability_safety", "itops_performance_cost",
    "sales_agent_architecture", "sales_business_modeling", "sales_context_memory_tools",
}


class ScenarioBankReleaseTests(unittest.TestCase):
    def test_release_matches_frozen_inventory_and_coverage(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=date(2026, 8, 29))
        self.assertEqual(set(catalog.scenarios), EXPECTED_SCENARIOS)
        self.assertEqual({module.module_id for module in catalog.active_modules}, EXPECTED_MODULES)
        counts = Counter(module.primary_dimension_id for module in catalog.active_modules)
        self.assertEqual(dict(counts), {
            "role_dim_01": 6,
            "role_dim_02": 6,
            "role_dim_03": 6,
            "role_dim_04": 4,
            "role_dim_05": 7,
            "role_dim_06": 6,
        })
        self.assertTrue(all(module.constraint_ids for module in catalog.active_modules))


if __name__ == "__main__":
    unittest.main()
