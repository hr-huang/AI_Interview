from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from profile_agent.services.scenario_bank_service import ScenarioCatalog


class ScenarioCatalogTests(unittest.TestCase):
    AS_OF = date(2026, 8, 29)

    def write_bank(self, root: Path, *, modules=None, constraints=None, manifest=None) -> None:
        root.mkdir(parents=True, exist_ok=True)
        scenario = {
            "scenario_id": "demo",
            "title": "演示业务",
            "business_goal": "支持业务流程",
            "modules": ["demo_module"],
            "source_ids": ["source_demo"],
            "valid_from": "2026-08-29",
            "valid_until": "2027-02-28",
        }
        module = {
            "module_id": "demo_module",
            "scenario_id": "demo",
            "primary_dimension_id": "role_dim_01",
            "supported_requirement_types": ["system_design"],
            "supported_modes": ["system_design", "scenario"],
            "difficulties": ["foundation", "intermediate"],
            "opening_goal": "验证业务链路",
            "semantic_text": "演示业务 Agent 任务编排",
            "evidence_signals": ["任务编排"],
            "constraint_ids": ["demo_constraint"],
            "default_for_dimension": True,
            "valid_from": "2026-08-29",
            "valid_until": "2027-02-28",
        }
        constraint = {
            "constraint_id": "demo_constraint",
            "scenario_id": "demo",
            "module_id": "demo_module",
            "evidence_gap_tags": ["失败恢复"],
            "difficulty": "intermediate",
            "fact": "外部接口响应超时",
        }
        (root / "scenarios.json").write_text(json.dumps([scenario], ensure_ascii=False), encoding="utf-8")
        (root / "modules.json").write_text(json.dumps(modules or [module], ensure_ascii=False), encoding="utf-8")
        (root / "constraints.json").write_text(json.dumps(constraints or [constraint], ensure_ascii=False), encoding="utf-8")
        (root / "ScenarioSourceRegistry.json").write_text(
            json.dumps([{"source_id": "source_demo", "title": "内部审核"}], ensure_ascii=False),
            encoding="utf-8",
        )
        (root / "ScenarioBankManifest.json").write_text(
            json.dumps(manifest or {
                "role_family": "ai_application_engineering",
                "role_profile_version": "2026-H2",
                "scenario_count": 1,
                "retrieval_module_count": 1,
                "scenario_ids": ["demo"],
                "module_ids": ["demo_module"],
                "source_registry_ids": ["source_demo"],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_loads_source_of_truth_and_resolves_retrieval_unit(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_bank(root)
            catalog = ScenarioCatalog.load(root, as_of=self.AS_OF)

        self.assertEqual(catalog.get_scenario("demo").title, "演示业务")
        self.assertEqual(catalog.get_module("demo_module").scenario_id, "demo")
        scenario, module = catalog.resolve("demo::demo_module")
        self.assertEqual(scenario.scenario_id, "demo")
        self.assertEqual(module.module_id, "demo_module")
        self.assertEqual(catalog.get_constraint("demo_constraint").module_id, "demo_module")
        self.assertEqual(catalog.default_module_for_dimension("role_dim_01").module_id, "demo_module")

    def test_rejects_constraint_owned_by_another_module(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            constraint = {
                "constraint_id": "demo_constraint",
                "scenario_id": "demo",
                "module_id": "missing_module",
                "evidence_gap_tags": ["失败恢复"],
                "difficulty": "intermediate",
                "fact": "外部接口响应超时",
            }
            self.write_bank(root, constraints=[constraint])
            with self.assertRaisesRegex(ValueError, "constraint.*missing_module"):
                ScenarioCatalog.load(root, as_of=self.AS_OF)

    def test_rejects_duplicate_module_ids(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            module = {
                "module_id": "demo_module",
                "scenario_id": "demo",
                "primary_dimension_id": "role_dim_01",
                "supported_requirement_types": ["system_design"],
                "supported_modes": ["system_design"],
                "difficulties": ["foundation"],
                "opening_goal": "验证业务链路",
                "semantic_text": "演示业务 Agent",
                "evidence_signals": ["任务编排"],
                "valid_from": "2026-08-29",
            }
            self.write_bank(root, modules=[module, module])
            with self.assertRaisesRegex(ValueError, "duplicate.*module_id"):
                ScenarioCatalog.load(root, as_of=self.AS_OF)

    def test_rejects_active_expired_default_module(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_bank(root)
            module = json.loads((root / "modules.json").read_text())
            module[0]["valid_from"] = "2025-08-29"
            module[0]["valid_until"] = "2026-08-28"
            (root / "modules.json").write_text(json.dumps(module), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expired"):
                ScenarioCatalog.load(root, as_of=self.AS_OF)


if __name__ == "__main__":
    unittest.main()
