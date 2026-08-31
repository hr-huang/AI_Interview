from collections import Counter
from datetime import date
from pathlib import Path
import unittest
from urllib.parse import urlparse

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

EXPECTED_SOURCE_IDS = {
    "source_internal_review",
    "jd-agent-2026-219868",
    "shlab-agent-intern-2026-04-28",
    "anthropic-writing-tools-for-agents-2025-09-11",
    "anthropic-demystifying-evals-2026-01-09",
}
EXPECTED_EXTERNAL_SOURCE_METADATA = {
    "jd-agent-2026-219868": {
        "publisher": "京东招聘",
        "title": "国际产研 AI Agent算法/工程专家",
        "url": "https://zhaopin.jd.com/web/job-info-detail?requementId=219868",
        "published_at": "2026-06-29",
        "retrieved_at": "2026-08-25",
        "supports_dimension_ids": {
            "role_dim_01", "role_dim_03", "role_dim_04",
            "role_dim_05", "role_dim_06",
        },
    },
    "shlab-agent-intern-2026-04-28": {
        "publisher": "上海人工智能实验室",
        "title": "【27届留用实习生】-Agent研发-工程平台中心",
        "url": "https://www.shlab.org.cn/joinus/detail/7631158899357649171?mode=campus&subject=7619221867426433326",
        "published_at": "2026-04-28",
        "retrieved_at": "2026-08-25",
        "supports_dimension_ids": {
            "role_dim_01", "role_dim_02", "role_dim_03",
            "role_dim_04", "role_dim_05", "role_dim_06",
        },
    },
    "anthropic-writing-tools-for-agents-2025-09-11": {
        "publisher": "Anthropic",
        "title": "Writing effective tools for AI agents — with agents",
        "url": "https://www.anthropic.com/engineering/writing-tools-for-agents",
        "published_at": "2025-09-11",
        "retrieved_at": "2026-08-25",
        "supports_dimension_ids": {
            "role_dim_01", "role_dim_03", "role_dim_04", "role_dim_06",
        },
    },
    "anthropic-demystifying-evals-2026-01-09": {
        "publisher": "Anthropic",
        "title": "Demystifying evals for AI agents",
        "url": "https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents",
        "published_at": "2026-01-09",
        "retrieved_at": "2026-08-25",
        "supports_dimension_ids": {
            "role_dim_04", "role_dim_05", "role_dim_06",
        },
    },
}
EXPECTED_INTERNAL_SOURCE_METADATA = {
    "publisher": "Scenario Bank 内部审核组",
    "published_at": "2026-08-29",
    "retrieved_at": "2026-08-29",
    "supports_dimension_ids": {
        "role_dim_01", "role_dim_02", "role_dim_03",
        "role_dim_04", "role_dim_05", "role_dim_06",
    },
}
ANTHROPIC_SOURCES_BY_DIMENSION = {
    "role_dim_01": {"anthropic-writing-tools-for-agents-2025-09-11"},
    "role_dim_03": {"anthropic-writing-tools-for-agents-2025-09-11"},
    "role_dim_04": {
        "anthropic-writing-tools-for-agents-2025-09-11",
        "anthropic-demystifying-evals-2026-01-09",
    },
    "role_dim_05": {"anthropic-demystifying-evals-2026-01-09"},
    "role_dim_06": {
        "anthropic-writing-tools-for-agents-2025-09-11",
        "anthropic-demystifying-evals-2026-01-09",
    },
}
EXPECTED_TUNED_SEMANTIC_ANCHORS = {
    "travel_business_modeling": ("旅行推荐", "机票", "酒店", "景点", "行程", "预算", "偏好", "出行约束"),
    "marketing_business_modeling": ("营销运营", "多渠道活动", "内容投放", "品牌调性", "曝光", "转化"),
    "travel_context_tools": ("旅行规划", "酒店价格", "景点信息", "出行日期", "行程上下文", "缓存失效"),
    "sales_context_memory_tools": ("销售跟进", "客户长期偏好", "跟进记忆", "CRM", "客户阶段", "线索失效"),
    "knowledge_rag_memory": ("企业制度知识库", "制度文档版本号", "生效时间", "当前制度", "旧制度", "引用追溯"),
    "knowledge_production_delivery": ("企业知识库", "文档索引发布", "知识库版本迁移", "检索质量评测", "文档发布门禁", "线上回滚"),
    "knowledge_security_evaluation": ("企业知识库", "RAG", "文档权限", "事实核验", "无证据", "高风险结论"),
    "coding_ai_delivery": ("AI 编程", "代码仓库", "AI 生成代码", "单元测试", "集成测试"),
    "data_analysis_ai_delivery": ("数据分析", "SQL", "SQL 单元测试", "集成测试", "查询测试"),
    "cost_monitor_observability": ("LLM", "Tool", "API", "Trace", "Span", "trace_id", "span_id", "Token 成本", "模型供应商账单", "日志归因"),
    "ecommerce_safety_evaluation": ("电商客服", "订单查询", "退款", "支付", "权限校验", "幂等", "超时重试"),
    "travel_cost_optimization": ("旅行推荐 Agent", "机票", "酒店", "景点", "搜索范围", "预订接口费用", "行程质量", "模型路由"),
    "ecommerce_performance_cost": ("电商客服大促", "800 RPS", "压测", "容量规划", "P95", "P99", "延迟预算", "工具等待", "负载测试", "人工复核队列"),
    "coding_cost_performance": ("AI 编程 Agent", "大代码库", "单元测试", "集成测试", "压测", "容量规划", "P95", "P99", "延迟预算", "负载测试"),
    "itops_performance_cost": ("IT 运维", "告警聚合", "压测", "容量规划", "P95", "P99", "延迟预算", "负载测试"),
}
ROLE_SOURCE_IDS = {"jd-agent-2026-219868", "shlab-agent-intern-2026-04-28"}
EXPECTED_CODING_MODULES = {
    "data_analysis_context_tools",
    "knowledge_production_delivery",
    "coding_ai_delivery",
    "data_analysis_ai_delivery",
    "coding_security_evaluation",
    "coding_cost_performance",
}

# This is deliberately an explicit review record, not a family/ID-prefix
# default.  A new or renamed Module must be added here before it can enter the
# frozen release.  Keep the values aligned with the reviewed Scenario Bank
# metadata so hard eligibility filters are observable in review.
EXPECTED_METADATA = {
    "ecommerce_agent_architecture": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["system_design", "problem_solving"],
        "difficulties": ["intermediate"],
    },
    "travel_agent_architecture": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["system_design", "problem_solving", "debugging"],
        "difficulties": ["intermediate"],
    },
    "recruitment_agent_architecture": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["system_design", "problem_solving"],
        "difficulties": ["intermediate"],
    },
    "coding_agent_architecture": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["system_design", "problem_solving", "debugging"],
        "difficulties": ["intermediate"],
    },
    "itops_agent_architecture": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["system_design", "problem_solving", "debugging"],
        "difficulties": ["intermediate", "advanced"],
    },
    "sales_agent_architecture": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["system_design", "problem_solving"],
        "difficulties": ["intermediate"],
    },
    "travel_business_modeling": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["knowledge", "problem_solving"],
        "difficulties": ["foundation", "intermediate"],
    },
    "cost_monitor_business_modeling": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["knowledge", "problem_solving"],
        "difficulties": ["foundation", "intermediate"],
    },
    "marketing_business_modeling": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["knowledge", "problem_solving"],
        "difficulties": ["foundation", "intermediate"],
    },
    "recruitment_business_modeling": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["knowledge", "problem_solving"],
        "difficulties": ["foundation", "intermediate"],
    },
    "data_analysis_business_modeling": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["knowledge", "problem_solving"],
        "difficulties": ["foundation", "intermediate"],
    },
    "sales_business_modeling": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["knowledge", "problem_solving"],
        "difficulties": ["foundation", "intermediate"],
    },
    "ecommerce_context_tools": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["implementation", "debugging", "system_design"],
        "difficulties": ["intermediate"],
    },
    "travel_context_tools": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["implementation", "debugging", "system_design"],
        "difficulties": ["intermediate"],
    },
    "knowledge_rag_memory": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["knowledge", "implementation", "debugging", "system_design", "experience_verification"],
        "difficulties": ["foundation", "intermediate"],
    },
    "recruitment_context_memory": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["knowledge", "implementation", "debugging", "system_design", "experience_verification"],
        "difficulties": ["intermediate", "advanced"],
    },
    "data_analysis_context_tools": {
        "supported_modes": ["scenario", "system_design", "coding"],
        "supported_requirement_types": ["knowledge", "implementation", "debugging", "system_design", "experience_verification"],
        "difficulties": ["intermediate"],
    },
    "sales_context_memory_tools": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["implementation", "debugging", "system_design"],
        "difficulties": ["intermediate"],
    },
    "knowledge_production_delivery": {
        "supported_modes": ["scenario", "system_design", "coding"],
        "supported_requirement_types": ["implementation", "debugging", "system_design", "experience_verification"],
        "difficulties": ["intermediate"],
    },
    "marketing_ai_delivery": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["implementation", "system_design", "experience_verification"],
        "difficulties": ["intermediate"],
    },
    "coding_ai_delivery": {
        "supported_modes": ["scenario", "system_design", "coding"],
        "supported_requirement_types": ["implementation", "debugging", "system_design", "experience_verification"],
        "difficulties": ["intermediate"],
    },
    "data_analysis_ai_delivery": {
        "supported_modes": ["scenario", "system_design", "coding"],
        "supported_requirement_types": ["implementation", "debugging", "system_design", "experience_verification"],
        "difficulties": ["intermediate"],
    },
    "ecommerce_safety_evaluation": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["debugging", "system_design", "problem_solving"],
        "difficulties": ["intermediate", "advanced"],
    },
    "cost_monitor_observability": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["implementation", "debugging", "system_design", "problem_solving", "experience_verification"],
        "difficulties": ["intermediate"],
    },
    "knowledge_security_evaluation": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["knowledge", "system_design", "problem_solving"],
        "difficulties": ["intermediate", "advanced"],
    },
    "marketing_safety_evaluation": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["knowledge", "system_design", "problem_solving"],
        "difficulties": ["intermediate"],
    },
    "recruitment_safety_evaluation": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["knowledge", "system_design", "problem_solving"],
        "difficulties": ["intermediate", "advanced"],
    },
    "coding_security_evaluation": {
        "supported_modes": ["scenario", "system_design", "coding"],
        "supported_requirement_types": ["implementation", "debugging", "system_design", "problem_solving", "experience_verification"],
        "difficulties": ["intermediate", "advanced"],
    },
    "itops_observability_safety": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["implementation", "debugging", "system_design", "problem_solving", "experience_verification"],
        "difficulties": ["intermediate", "advanced"],
    },
    "ecommerce_performance_cost": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["system_design", "problem_solving"],
        "difficulties": ["intermediate", "advanced"],
    },
    "travel_cost_optimization": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["system_design", "problem_solving"],
        "difficulties": ["intermediate"],
    },
    "cost_monitor_performance": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["system_design", "problem_solving", "debugging"],
        "difficulties": ["intermediate"],
    },
    "knowledge_cost_performance": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["system_design", "problem_solving", "debugging"],
        "difficulties": ["intermediate", "advanced"],
    },
    "coding_cost_performance": {
        "supported_modes": ["scenario", "system_design", "coding"],
        "supported_requirement_types": ["implementation", "debugging", "system_design", "problem_solving", "experience_verification"],
        "difficulties": ["intermediate", "advanced"],
    },
    "itops_performance_cost": {
        "supported_modes": ["scenario", "system_design"],
        "supported_requirement_types": ["system_design", "problem_solving", "debugging"],
        "difficulties": ["intermediate", "advanced"],
    },
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

    def test_release_has_candidate_safe_brief_for_every_scenario(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=date(2026, 8, 29))
        for scenario in catalog.scenarios.values():
            brief = scenario.candidate_brief
            self.assertIsNotNone(brief, scenario.scenario_id)
            self.assertEqual(brief, brief.strip(), scenario.scenario_id)
            self.assertNotRegex(brief, r"[\r\n?？]", scenario.scenario_id)
            self.assertIn(brief.count("。"), (1, 2), scenario.scenario_id)

            forbidden = list(scenario.base_constraints)
            for module_id in scenario.modules:
                module = catalog.get_module(module_id)
                forbidden.extend(module.evidence_signals)
                forbidden.extend(module.critical_errors)
                for constraint in catalog.constraints_for_module(module_id):
                    forbidden.extend(
                        [
                            constraint.description,
                            constraint.fact or "",
                        ]
                    )
            for value in forbidden:
                if value.strip():
                    self.assertNotIn(value, brief, f"{scenario.scenario_id}: {value}")

    def test_release_uses_explicit_reviewed_metadata_for_every_module(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=date(2026, 8, 29))
        actual_ids = {module.module_id for module in catalog.active_modules}
        self.assertEqual(actual_ids, set(EXPECTED_METADATA))

        for module in catalog.active_modules:
            expected = EXPECTED_METADATA[module.module_id]
            self.assertEqual(set(module.supported_modes), set(expected["supported_modes"]), module.module_id)
            self.assertEqual(set(module.supported_requirement_types), set(expected["supported_requirement_types"]), module.module_id)
            self.assertEqual(set(module.difficulties), set(expected["difficulties"]), module.module_id)
            self.assertIn("intermediate", module.difficulties, module.module_id)
            if module.primary_dimension_id in {"role_dim_01", "role_dim_02"}:
                self.assertNotIn("coding", module.supported_modes, module.module_id)

        self.assertGreater(
            len({tuple(module.supported_modes) for module in catalog.active_modules}),
            1,
        )
        self.assertGreater(
            len({tuple(module.supported_requirement_types) for module in catalog.active_modules}),
            1,
        )
        self.assertGreater(
            len({tuple(module.difficulties) for module in catalog.active_modules}),
            1,
        )
        self.assertEqual(
            {
                module.module_id
                for module in catalog.active_modules
                if "coding" in module.supported_modes
            },
            EXPECTED_CODING_MODULES,
        )

    def test_release_keeps_tuned_modules_anchored_to_business_worlds(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=date(2026, 8, 29))
        for module_id, anchors in EXPECTED_TUNED_SEMANTIC_ANCHORS.items():
            with self.subTest(module=module_id):
                semantic_text = catalog.get_module(module_id).semantic_text
                for anchor in anchors:
                    self.assertIn(anchor, semantic_text)

    def test_release_contains_reviewed_multi_agent_loop_constraint(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=date(2026, 8, 29))
        self.assertEqual(len(catalog.constraints), 38)
        constraint = catalog.get_constraint("coding_architecture_multi_agent_loop")
        self.assertEqual(constraint.scenario_id, "coding_review_agent")
        self.assertEqual(constraint.module_id, "coding_agent_architecture")
        self.assertEqual(constraint.difficulty, "intermediate")
        self.assertEqual(constraint.evidence_gap_tags, ["Multi-Agent", "循环检测", "终止条件"])
        self.assertIn("coding_architecture_multi_agent_loop", catalog.get_module("coding_agent_architecture").constraint_ids)

    def test_release_registers_only_reviewed_external_sources(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=date(2026, 8, 29))
        self.assertEqual(set(catalog.manifest.source_registry_ids), EXPECTED_SOURCE_IDS)
        self.assertEqual(set(catalog.source_registry), EXPECTED_SOURCE_IDS)

        internal = catalog.source_registry["source_internal_review"]
        self.assertEqual(internal.source_type, "internal_review")
        self.assertEqual(internal.publisher, EXPECTED_INTERNAL_SOURCE_METADATA["publisher"])
        self.assertEqual(
            internal.published_at.isoformat(),
            EXPECTED_INTERNAL_SOURCE_METADATA["published_at"],
        )
        self.assertEqual(
            internal.retrieved_at.isoformat(),
            EXPECTED_INTERNAL_SOURCE_METADATA["retrieved_at"],
        )
        self.assertEqual(
            set(internal.supports_dimension_ids),
            EXPECTED_INTERNAL_SOURCE_METADATA["supports_dimension_ids"],
        )
        for source_id, expected in EXPECTED_EXTERNAL_SOURCE_METADATA.items():
            with self.subTest(source_id=source_id):
                source = catalog.source_registry[source_id]
                self.assertNotEqual(source.source_type, "internal_review")
                self.assertEqual(source.title, expected["title"])
                self.assertEqual(str(source.source_url), expected["url"])
                self.assertIn(urlparse(str(source.source_url)).scheme, {"http", "https"})
                self.assertEqual(source.publisher, expected["publisher"])
                self.assertEqual(source.published_at.isoformat(), expected["published_at"])
                self.assertEqual(source.retrieved_at.isoformat(), expected["retrieved_at"])
                self.assertEqual(
                    set(source.supports_dimension_ids),
                    expected["supports_dimension_ids"],
                )
                self.assertTrue(source.notes)
                self.assertFalse(source.notes.lstrip().startswith("{"))

    def test_every_production_object_has_external_source_support(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=date(2026, 8, 29))

        def assert_external(source_ids: list[str], object_id: str) -> None:
            self.assertTrue(source_ids, object_id)
            self.assertTrue(
                set(source_ids).issubset(EXPECTED_SOURCE_IDS),
                object_id,
            )
            self.assertTrue(
                any(source_id != "source_internal_review" for source_id in source_ids),
                object_id,
            )

        for scenario in catalog.active_scenarios:
            with self.subTest(kind="scenario", object_id=scenario.scenario_id):
                assert_external(scenario.source_ids, scenario.scenario_id)
        for module in catalog.active_modules:
            with self.subTest(kind="module", object_id=module.module_id):
                assert_external(module.source_refs, module.module_id)
        for constraint in catalog.constraints.values():
            with self.subTest(kind="constraint", object_id=constraint.constraint_id):
                assert_external(constraint.source_refs, constraint.constraint_id)

    def test_engineering_modules_have_applicable_anthropic_support(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=date(2026, 8, 29))
        for module in catalog.active_modules:
            with self.subTest(module=module.module_id):
                source_ids = set(module.source_refs)
                if module.primary_dimension_id == "role_dim_02":
                    self.assertTrue(source_ids & ROLE_SOURCE_IDS)
                    continue
                self.assertTrue(
                    source_ids & ANTHROPIC_SOURCES_BY_DIMENSION[module.primary_dimension_id],
                    module.module_id,
                )
                self.assertTrue(source_ids & ROLE_SOURCE_IDS, module.module_id)

                supported_dimensions = set()
                for source_id in source_ids:
                    source = catalog.source_registry[source_id]
                    if source_id == "source_internal_review":
                        continue
                    supported_dimensions.update(source.supports_dimension_ids)
                self.assertIn(module.primary_dimension_id, supported_dimensions, module.module_id)


if __name__ == "__main__":
    unittest.main()
