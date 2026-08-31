from __future__ import annotations

from datetime import date
from datetime import datetime, timezone
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from profile_agent.schemas.scenario_calibration_schema import (
    ScenarioCalibrationCaseResult,
    ScenarioCalibrationReport,
    ScenarioCalibrationRunMetadata,
    ScenarioRetrievalCase,
)
from profile_agent.services.scenario_calibration_service import (
    ScenarioCalibrationAcceptance,
    evaluate_scenario_retrieval,
    load_scenario_retrieval_cases,
)
from profile_agent.services.scenario_bank_service import ScenarioCatalog
from profile_agent.schemas.scenario_rag_schema import (
    ScenarioCandidate,
    ScenarioCandidateSet,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "scenario_rag"
    / "retrieval_cases.json"
)

EXPECTED_CASE_INTENTS = {
    "task_routing": (
        "多步骤业务流程中的任务拆分、工具路由与人工接管边界",
        "role_dim_01",
        {"ecommerce_agent_architecture", "travel_agent_architecture", "itops_agent_architecture"},
        {"coding_agent_architecture"},
    ),
    "multi_agent_handoff": (
        "多个 Agent 或节点之间如何分工、传递状态并完成安全交接",
        "role_dim_01",
        {"recruitment_agent_architecture", "coding_agent_architecture", "ecommerce_agent_architecture"},
        {"sales_agent_architecture"},
    ),
    "shared_state_conflict": (
        "电商客服订单查询与退款并发更新共享订单状态时，如何处理冲突和一致性",
        "role_dim_01",
        {"ecommerce_agent_architecture"},
        {"recruitment_agent_architecture"},
    ),
    "termination_loop": (
        "Agent 在失败重试、重新规划或人工升级之间如何避免无限循环并可靠终止",
        "role_dim_01",
        {"coding_agent_architecture", "itops_agent_architecture", "travel_agent_architecture"},
        {"recruitment_agent_architecture"},
    ),
    "ambiguous_business_goal": (
        "旅行推荐业务只说提高推荐效果时，如何澄清目标、约束、输入输出和可验收结果",
        "role_dim_02",
        {"travel_business_modeling"},
        {"marketing_business_modeling"},
    ),
    "workflow_decomposition": (
        "营销运营要提高多渠道活动转化，如何围绕品牌调性和曝光目标拆成内容投放工作流、阶段输入输出与转化验收任务",
        "role_dim_02",
        {"marketing_business_modeling"},
        {"travel_business_modeling"},
    ),
    "success_metric": (
        "如何定义业务成功指标、误报成本和模型输出质量的可量化验收标准",
        "role_dim_02",
        {"cost_monitor_business_modeling", "sales_business_modeling", "travel_business_modeling"},
        {"marketing_business_modeling"},
    ),
    "human_boundary": (
        "招聘评估筛选与录用流程中，哪些判断应由人确认，如何把自动化边界写成可验收规则",
        "role_dim_02",
        {"recruitment_business_modeling"},
        {"cost_monitor_business_modeling"},
    ),
    "memory_lifecycle": (
        "销售 CRM 客户长期偏好与跟进记忆如何写入、更新、过期、删除，并在客户阶段变化时保持隐私边界",
        "role_dim_03",
        {"sales_context_memory_tools"},
        {"travel_context_tools"},
    ),
    "knowledge_version": (
        "企业制度知识库每天更新，如何按制度文档版本号和生效时间检索，避免引用旧制度，并保留制度引用追溯",
        "role_dim_03",
        {"knowledge_rag_memory", "travel_context_tools", "data_analysis_context_tools"},
        {"sales_context_memory_tools"},
    ),
    "tool_context_boundary": (
        "工具返回的结构化结果如何进入 Context，并在权限、字段和状态边界内继续推理",
        "role_dim_03",
        {"data_analysis_context_tools", "ecommerce_context_tools", "travel_context_tools"},
        {"knowledge_rag_memory"},
    ),
    "citation_traceability": (
        "RAG 回答如何保留原始证据、引用来源和版本，使结论可回溯",
        "role_dim_03",
        {"knowledge_rag_memory", "recruitment_context_memory", "data_analysis_context_tools"},
        {"ecommerce_context_tools"},
    ),
    "ai_delivery_pipeline": (
        "AI 生成或修改交付物如何经过审查、测试、部署门禁和生产验收",
        "role_dim_04",
        {"coding_ai_delivery", "knowledge_production_delivery", "marketing_ai_delivery"},
        {"data_analysis_ai_delivery"},
    ),
    "regression_verification": (
        "Prompt、代码或指标口径变更后如何设计固定样本回归和失败定位",
        "role_dim_04",
        {"coding_ai_delivery", "knowledge_production_delivery", "data_analysis_ai_delivery"},
        {"marketing_ai_delivery"},
    ),
    "model_change_rollout": (
        "模型或生成策略升级时如何做版本管理、灰度发布、质量回归和回滚",
        "role_dim_04",
        {"knowledge_production_delivery", "marketing_ai_delivery", "coding_ai_delivery"},
        {"data_analysis_ai_delivery"},
    ),
    "coding_review": (
        "代码仓库中的 AI 生成代码如何结合单元测试、集成测试做逻辑审查和隐藏回归验收",
        "role_dim_04",
        {"coding_ai_delivery", "data_analysis_ai_delivery"},
        {"knowledge_production_delivery"},
    ),
    "tool_idempotency": (
        "工具调用超时或重试时如何保证幂等、权限校验、审计和人工接管",
        "role_dim_05",
        {"ecommerce_safety_evaluation", "cost_monitor_observability", "itops_observability_safety"},
        {"knowledge_security_evaluation"},
    ),
    "prompt_injection": (
        "检索到恶意文档或不可信指令时如何隔离 Prompt Injection、权限和发布风险",
        "role_dim_05",
        {"knowledge_security_evaluation", "coding_security_evaluation", "marketing_safety_evaluation"},
        {"recruitment_safety_evaluation"},
    ),
    "retrieval_trust": (
        "企业知识库 RAG 如何校验文档权限、来源和事实，并在无证据时阻止高风险结论",
        "role_dim_05",
        {"knowledge_security_evaluation", "recruitment_safety_evaluation", "cost_monitor_observability"},
        {"marketing_safety_evaluation"},
    ),
    "call_chain_attribution": (
        "如何把一次 LLM 决策关联到 trace_id、span_id、RAG 检索、Tool 调用和日志，并核对 Token 成本、模型供应商账单，支持故障与风险审计",
        "role_dim_05",
        {"cost_monitor_observability", "itops_observability_safety", "coding_security_evaluation"},
        {"ecommerce_safety_evaluation"},
    ),
    "llm_cost_reduction": (
        "如何通过上下文裁剪、缓存、模型选择和批处理降低 LLM 调用成本并守住质量",
        "role_dim_06",
        {"travel_cost_optimization", "cost_monitor_performance", "knowledge_cost_performance"},
        {"coding_cost_performance"},
    ),
    "model_routing": (
        "不同复杂度和风险的任务如何路由到不同模型并监控成本、质量和延迟",
        "role_dim_06",
        {"cost_monitor_performance", "travel_cost_optimization", "itops_performance_cost"},
        {"knowledge_cost_performance"},
    ),
    "cache_quality_tradeoff": (
        "缓存命中带来成本收益时如何处理数据新鲜度、失效策略和回答质量回归",
        "role_dim_06",
        {"knowledge_cost_performance", "travel_cost_optimization", "cost_monitor_performance"},
        {"ecommerce_performance_cost"},
    ),
    "latency_budget": (
        "电商大促 800 RPS 压测和容量规划下，如何分配端到端 P95/P99 延迟预算、工具等待、负载和人工队列成本",
        "role_dim_06",
        {"ecommerce_performance_cost", "coding_cost_performance", "itops_performance_cost"},
        {"travel_cost_optimization"},
    ),
}


EXPECTED_QUERY_ANCHORS = {
    "shared_state_conflict": ("电商客服", "订单查询", "退款", "共享订单状态"),
    "ambiguous_business_goal": ("旅行推荐", "提高推荐效果"),
    "workflow_decomposition": ("营销运营", "多渠道活动", "品牌", "曝光", "转化"),
    "human_boundary": ("招聘评估筛选", "录用", "人确认"),
    "knowledge_version": ("企业制度知识库", "制度文档版本号", "生效时间", "旧制度", "制度引用追溯"),
    "coding_review": ("代码仓库", "单元测试", "集成测试"),
    "retrieval_trust": ("企业知识库", "RAG", "文档权限", "来源", "事实", "无证据", "高风险"),
    "call_chain_attribution": ("LLM", "trace_id", "span_id", "RAG", "Tool", "日志", "Token 成本", "模型供应商账单"),
    "latency_budget": ("电商大促", "800 RPS", "压测", "容量规划", "P95", "P99", "工具等待", "人工队列"),
}


EXPECTED_TOP3_BOUNDARY_CASES = {
    "workflow_decomposition": {
        "acceptable": {"marketing_business_modeling"},
        "forbidden": {"travel_business_modeling"},
        "query_anchors": ("多渠道活动", "品牌调性", "曝光", "转化"),
    },
    "memory_lifecycle": {
        "acceptable": {"sales_context_memory_tools"},
        "forbidden": {"travel_context_tools"},
        "query_anchors": ("销售 CRM", "客户长期偏好", "跟进记忆", "客户阶段", "隐私"),
    },
    "knowledge_version": {
        "acceptable": {"knowledge_rag_memory", "travel_context_tools", "data_analysis_context_tools"},
        "forbidden": {"sales_context_memory_tools"},
        "query_anchors": ("制度文档版本号", "生效时间", "旧制度", "制度引用追溯"),
    },
    "coding_review": {
        "acceptable": {"coding_ai_delivery", "data_analysis_ai_delivery"},
        "forbidden": {"knowledge_production_delivery"},
        "query_anchors": ("代码仓库", "AI 生成代码", "单元测试", "集成测试"),
    },
    "call_chain_attribution": {
        "acceptable": {"cost_monitor_observability", "itops_observability_safety", "coding_security_evaluation"},
        "forbidden": {"ecommerce_safety_evaluation"},
        "query_anchors": ("Token 成本", "模型供应商账单", "trace_id", "span_id"),
    },
    "latency_budget": {
        "acceptable": {"ecommerce_performance_cost", "coding_cost_performance", "itops_performance_cost"},
        "forbidden": {"travel_cost_optimization"},
        "query_anchors": ("压测", "容量规划", "P95", "P99", "负载"),
    },
}


class FakeRetriever:
    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, date, int]] = []

    def retrieve(self, case: ScenarioRetrievalCase, *, as_of: date, limit: int = 3):
        self.calls.append((case.case_id, as_of, limit))
        return self.outcomes[case.case_id]


def _hit(*module_ids: str) -> ScenarioCandidateSet:
    return ScenarioCandidateSet(
        status="hit",
        candidates=[
            ScenarioCandidate(retrieval_unit_id=f"s::{module_id}")
            for module_id in module_ids
        ],
    )


class ScenarioCalibrationSchemaTests(unittest.TestCase):
    def test_report_round_trips_safe_run_metadata(self) -> None:
        metadata = ScenarioCalibrationRunMetadata(
            embedding_provider="siliconflow",
            embedding_model="BAAI/bge-m3",
            reranker_provider="siliconflow",
            reranker_model="BAAI/bge-reranker-v2-m3",
            qdrant_collection="scenario_modules",
            qdrant_index_version="scenario-modules-v1",
            bank_version=1,
            role_family="ai_application_engineering",
            role_profile_version="2026-H2",
            as_of=date(2026, 8, 30),
            created_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
            bank_manifest_hash="sha256:abc123",
        )
        report = ScenarioCalibrationReport(
            case_count=0,
            top1_acceptable_rate=0,
            top3_recall=0,
            forbidden_hit_count=0,
            fallback_count=0,
            metadata=metadata,
        )

        payload = report.model_dump(mode="json")
        restored = ScenarioCalibrationReport.model_validate(payload)

        self.assertEqual(restored.metadata, metadata)
        self.assertEqual(payload["metadata"]["as_of"], "2026-08-30")
        self.assertEqual(payload["metadata"]["created_at"], "2026-08-30T12:00:00Z")
        serialized = json.dumps(payload, ensure_ascii=False)
        for sensitive in ("api_key", "token", "authorization", "base_url", "sk-"):
            self.assertNotIn(sensitive, serialized.lower())

    def test_old_report_without_metadata_remains_readable(self) -> None:
        report = ScenarioCalibrationReport(
            case_count=0,
            top1_acceptable_rate=0,
            top3_recall=0,
            forbidden_hit_count=0,
            fallback_count=0,
        )
        self.assertIsNone(report.metadata)

    def test_old_report_payload_defaults_new_forbidden_top1_fields(self) -> None:
        report = ScenarioCalibrationReport.model_validate(
            {
                "case_count": 0,
                "top1_acceptable_rate": 0,
                "top3_recall": 0,
                "forbidden_hit_count": 0,
                "fallback_count": 0,
                "case_results": [],
            }
        )
        self.assertEqual(report.forbidden_top1_hit_count, 0)
        result = ScenarioCalibrationCaseResult.model_validate(
            {"case_id": "legacy", "status": "no_match"}
        )
        self.assertFalse(result.top1_forbidden)

    def test_fixture_has_exactly_four_cases_per_dimension(self) -> None:
        cases = load_scenario_retrieval_cases(FIXTURE)
        self.assertEqual(len(cases), 24)
        counts: dict[str, int] = {}
        for case in cases:
            counts[case.primary_dimension_id] = counts.get(case.primary_dimension_id, 0) + 1
        self.assertEqual(counts, {f"role_dim_0{i}": 4 for i in range(1, 7)})

    def test_fixture_expectations_are_same_dimension_and_multi_world(self) -> None:
        catalog = ScenarioCatalog.load(as_of=date(2026, 8, 30))
        for case in load_scenario_retrieval_cases(FIXTURE):
            with self.subTest(case=case.case_id):
                self.assertGreaterEqual(len(case.acceptable_module_ids), 1)
                self.assertTrue(case.forbidden_module_ids)
                for module_id in [
                    *case.acceptable_module_ids,
                    *case.forbidden_module_ids,
                ]:
                    module = catalog.get_module(module_id)
                    self.assertEqual(module.primary_dimension_id, case.primary_dimension_id)
                    self.assertIn(case.question_mode, module.supported_modes)
                    self.assertIn(case.requirement_type, module.supported_requirement_types)
                    self.assertIn(case.difficulty, module.difficulties)

    def test_fixture_business_intent_matches_reviewed_snapshot(self) -> None:
        cases = {
            case.case_id: case
            for case in load_scenario_retrieval_cases(FIXTURE)
        }
        self.assertEqual(set(cases), set(EXPECTED_CASE_INTENTS))
        for case_id, (query, dimension_id, acceptable, forbidden) in EXPECTED_CASE_INTENTS.items():
            case = cases[case_id]
            with self.subTest(case=case_id):
                self.assertEqual(case.query, query)
                self.assertEqual(case.primary_dimension_id, dimension_id)
                self.assertEqual(set(case.acceptable_module_ids), acceptable)
                self.assertEqual(set(case.forbidden_module_ids), forbidden)

    def test_nine_tuned_queries_keep_explicit_business_anchors(self) -> None:
        cases = {
            case.case_id: case
            for case in load_scenario_retrieval_cases(FIXTURE)
        }
        for case_id, anchors in EXPECTED_QUERY_ANCHORS.items():
            with self.subTest(case=case_id):
                query = cases[case_id].query
                for anchor in anchors:
                    self.assertIn(anchor, query)

    def test_top3_boundary_cases_keep_reviewed_positive_negative_contract(self) -> None:
        cases = {
            case.case_id: case
            for case in load_scenario_retrieval_cases(FIXTURE)
        }
        self.assertEqual(set(cases) & set(EXPECTED_TOP3_BOUNDARY_CASES), set(EXPECTED_TOP3_BOUNDARY_CASES))
        for case_id, expected in EXPECTED_TOP3_BOUNDARY_CASES.items():
            case = cases[case_id]
            with self.subTest(case=case_id):
                self.assertEqual(set(case.acceptable_module_ids), expected["acceptable"])
                self.assertEqual(set(case.forbidden_module_ids), expected["forbidden"])
                for anchor in expected["query_anchors"]:
                    self.assertIn(anchor, case.query)

    def test_case_rejects_duplicate_or_overlapping_module_ids(self) -> None:
        with self.assertRaises(ValidationError):
            ScenarioRetrievalCase(
                case_id="case",
                query="检索",
                primary_dimension_id="role_dim_01",
                requirement_type="system_design",
                question_mode="scenario",
                difficulty="intermediate",
                acceptable_module_ids=["a", "a"],
            )
        with self.assertRaises(ValidationError):
            ScenarioRetrievalCase(
                case_id="case",
                query="检索",
                primary_dimension_id="role_dim_01",
                requirement_type="system_design",
                question_mode="scenario",
                difficulty="intermediate",
                acceptable_module_ids=["a"],
                forbidden_module_ids=["a"],
            )

    def test_report_requires_result_count_to_match_case_count(self) -> None:
        result = ScenarioCalibrationCaseResult(
            case_id="case",
            status="hit",
            top1_module_id="module",
            top3_module_ids=["module"],
            top1_acceptable=True,
            acceptable_in_top3=True,
        )
        with self.assertRaises(ValidationError):
            ScenarioCalibrationReport(
                case_count=2,
                top1_acceptable_rate=0.5,
                top3_recall=0.5,
                forbidden_hit_count=0,
                fallback_count=0,
                case_results=[result],
            )


class ScenarioCalibrationServiceTests(unittest.TestCase):
    def test_evaluate_carries_explicit_run_metadata(self) -> None:
        case = ScenarioRetrievalCase(
            case_id="case",
            query="任务路由",
            primary_dimension_id="role_dim_01",
            requirement_type="system_design",
            question_mode="scenario",
            difficulty="intermediate",
            acceptable_module_ids=["right"],
        )
        metadata = ScenarioCalibrationRunMetadata(
            embedding_provider="fake-embedding",
            embedding_model="fake-embed-v1",
            reranker_provider="fake-reranker",
            reranker_model="fake-rerank-v1",
            qdrant_collection="scenario_modules",
            qdrant_index_version="scenario-modules-v1",
            bank_version=1,
            role_family="ai_application_engineering",
            role_profile_version="2026-H2",
            as_of=date(2026, 8, 30),
            created_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        )

        report = evaluate_scenario_retrieval(
            [case],
            FakeRetriever({"case": _hit("right")}),
            date(2026, 8, 30),
            metadata=metadata,
        )

        self.assertEqual(report.metadata, metadata)

    def test_evaluate_uses_top_three_and_counts_fallback_outcomes(self) -> None:
        cases = [
            ScenarioRetrievalCase(
                case_id="good",
                query="任务路由",
                primary_dimension_id="role_dim_01",
                requirement_type="system_design",
                question_mode="scenario",
                difficulty="intermediate",
                acceptable_module_ids=["right"],
                forbidden_module_ids=["wrong"],
            ),
            ScenarioRetrievalCase(
                case_id="bad",
                query="任务路由",
                primary_dimension_id="role_dim_01",
                requirement_type="system_design",
                question_mode="scenario",
                difficulty="intermediate",
                acceptable_module_ids=["right"],
                forbidden_module_ids=["wrong"],
            ),
            ScenarioRetrievalCase(
                case_id="none",
                query="任务路由",
                primary_dimension_id="role_dim_01",
                requirement_type="system_design",
                question_mode="scenario",
                difficulty="intermediate",
                acceptable_module_ids=["right"],
            ),
        ]
        fake = FakeRetriever(
            {
                "good": _hit("right", "other", "wrong"),
                "bad": _hit("other", "wrong", "other2"),
                "none": ScenarioCandidateSet(status="no_match"),
            }
        )

        report = evaluate_scenario_retrieval(
            cases,
            fake,
            date(2026, 8, 30),
        )

        self.assertIsInstance(report, ScenarioCalibrationReport)
        self.assertEqual(report.case_count, 3)
        self.assertAlmostEqual(report.top1_acceptable_rate, 1 / 3)
        self.assertAlmostEqual(report.top3_recall, 1 / 3)
        self.assertEqual(report.forbidden_hit_count, 2)
        self.assertEqual(report.fallback_count, 1)
        self.assertEqual(
            fake.calls,
            [
                ("good", date(2026, 8, 30), 3),
                ("bad", date(2026, 8, 30), 3),
                ("none", date(2026, 8, 30), 3),
            ],
        )

    def test_forbidden_hit_is_limited_to_declared_forbidden_ids(self) -> None:
        case = ScenarioRetrievalCase(
            case_id="case",
            query="安全边界",
            primary_dimension_id="role_dim_05",
            requirement_type="debugging",
            question_mode="scenario",
            difficulty="advanced",
            acceptable_module_ids=["safe"],
            forbidden_module_ids=["wrong"],
        )
        fake = FakeRetriever({"case": _hit("safe", "other", "wrong", "ignored")})
        report = evaluate_scenario_retrieval([case], fake, date(2026, 8, 30))
        self.assertEqual(report.forbidden_hit_count, 1)
        self.assertEqual(
            report.case_results[0].top3_module_ids,
            ["safe", "other", "wrong"],
        )

    def test_rank_two_forbidden_is_top3_diagnostic_but_passes_top1_gate(self) -> None:
        cases = [
            ScenarioRetrievalCase(
                case_id=f"case-{index}",
                query="任务路由",
                primary_dimension_id="role_dim_01",
                requirement_type="system_design",
                question_mode="scenario",
                difficulty="intermediate",
                acceptable_module_ids=["right"],
                forbidden_module_ids=["wrong"],
            )
            for index in range(4)
        ]
        fake = FakeRetriever(
            {
                case.case_id: _hit(
                    "right", "wrong", "other"
                ) if case.case_id == "case-0" else _hit("right")
                for case in cases
            }
        )

        report = evaluate_scenario_retrieval(cases, fake, date(2026, 8, 30))
        acceptance = ScenarioCalibrationAcceptance(report)

        self.assertEqual(report.forbidden_hit_count, 1)
        self.assertEqual(report.forbidden_top1_hit_count, 0)
        self.assertFalse(report.case_results[0].top1_forbidden)
        self.assertTrue(acceptance.passed)

    def test_rank_one_forbidden_fails_top1_gate(self) -> None:
        cases = [
            ScenarioRetrievalCase(
                case_id=f"case-{index}",
                query="任务路由",
                primary_dimension_id="role_dim_01",
                requirement_type="system_design",
                question_mode="scenario",
                difficulty="intermediate",
                acceptable_module_ids=["right"],
                forbidden_module_ids=["wrong"],
            )
            for index in range(4)
        ]
        fake = FakeRetriever(
            {
                case.case_id: _hit(
                    "wrong", "right", "other"
                ) if case.case_id == "case-0" else _hit("right")
                for case in cases
            }
        )

        report = evaluate_scenario_retrieval(cases, fake, date(2026, 8, 30))
        acceptance = ScenarioCalibrationAcceptance(report)

        self.assertEqual(report.forbidden_hit_count, 1)
        self.assertEqual(report.forbidden_top1_hit_count, 1)
        self.assertTrue(report.case_results[0].top1_forbidden)
        self.assertFalse(acceptance.passed)
        self.assertIn("forbidden_top1", acceptance.failed_checks)

    def test_retriever_result_type_mismatch_is_explicit(self) -> None:
        case = ScenarioRetrievalCase(
            case_id="case",
            query="任务路由",
            primary_dimension_id="role_dim_01",
            requirement_type="system_design",
            question_mode="scenario",
            difficulty="intermediate",
            acceptable_module_ids=["right"],
        )

        with self.assertRaises(TypeError):
            evaluate_scenario_retrieval(
                [case],
                FakeRetriever({"case": {"status": "hit", "candidates": []}}),
                date(2026, 8, 30),
            )

    def test_retriever_exception_is_not_reclassified_as_unavailable(self) -> None:
        case = ScenarioRetrievalCase(
            case_id="case",
            query="任务路由",
            primary_dimension_id="role_dim_01",
            requirement_type="system_design",
            question_mode="scenario",
            difficulty="intermediate",
            acceptable_module_ids=["right"],
        )

        class ExplodingRetriever:
            def retrieve(self, case, *, as_of: date, limit: int = 3):
                raise RuntimeError("provider failure")

        with self.assertRaisesRegex(RuntimeError, "provider failure"):
            evaluate_scenario_retrieval(
                [case],
                ExplodingRetriever(),
                date(2026, 8, 30),
            )

    def test_loader_rejects_a_case_bank_that_is_not_exactly_24(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            load_scenario_retrieval_cases(payload[:23])


if __name__ == "__main__":
    unittest.main()
