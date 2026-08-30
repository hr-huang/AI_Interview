from datetime import date
import unittest

from profile_agent.schemas.interview_schema import AskAction, GeneratedQuestion
from profile_agent.schemas.scenario_rag_schema import (
    LockedScenarioContext,
    QuestionProvenance,
    ScenarioBankManifest,
    ScenarioCard,
    ScenarioConstraint,
    ScenarioModule,
    ScenarioRetrievalRequest,
)


class ScenarioRagSchemaTests(unittest.TestCase):
    def test_module_has_one_official_dimension_and_stable_retrieval_unit(self) -> None:
        module = ScenarioModule(
            module_id="ecommerce_agent_architecture",
            scenario_id="ecommerce_service",
            primary_dimension_id="role_dim_01",
            supported_requirement_types=["system_design"],
            supported_modes=["system_design", "scenario"],
            difficulties=["foundation", "intermediate"],
            opening_goal="验证整体组件划分和任务路由",
            semantic_text="电商客服 Agent 整体架构 任务路由 工具边界 人工接管",
            evidence_signals=["任务拆分", "人工接管"],
            critical_errors=["让模型无约束直接退款"],
            constraint_ids=["refund_timeout_after_success"],
            default_for_dimension=True,
            status="active",
            valid_from=date(2026, 8, 29),
            valid_until=date(2027, 2, 28),
        )
        self.assertEqual(
            module.retrieval_unit_id,
            "ecommerce_service::ecommerce_agent_architecture",
        )
        self.assertEqual(module.primary_dimension_id, "role_dim_01")

    def test_card_module_constraint_and_request_contracts_are_candidate_safe(self) -> None:
        card = ScenarioCard(
            scenario_id="ecommerce_service",
            title="电商智能客服",
            business_goal="支持商品咨询、订单查询和退款",
            modules=["ecommerce_agent_architecture"],
            source_ids=["source_01"],
            valid_from=date(2026, 8, 29),
            valid_until=date(2027, 2, 28),
        )
        constraint = ScenarioConstraint(
            constraint_id="refund_timeout_after_success",
            scenario_id=card.scenario_id,
            module_id="ecommerce_agent_architecture",
            evidence_gap_tags=["幂等", "失败恢复"],
            difficulty="intermediate",
            fact="退款实际已经执行成功，但接口响应超时",
        )
        request = ScenarioRetrievalRequest(
            primary_dimension_id="role_dim_01",
            question_mode="system_design",
            requirement_type="system_design",
            difficulty="foundation",
            objective="验证候选人能否设计 Agent 业务链路",
            evidence_gap=["任务路由"],
        )
        self.assertEqual(card.role_family, "ai_application_engineering")
        self.assertEqual(constraint.fact, "退款实际已经执行成功，但接口响应超时")
        self.assertIn("任务路由", request.semantic_query)
        self.assertNotIn("姓名", request.semantic_query)

    def test_provenance_and_locked_context_keep_selected_constraint_explicit(self) -> None:
        provenance = QuestionProvenance(
            target_requirement_id="req_01",
            primary_dimension_id="role_dim_01",
            retrieval_unit_id="ecommerce_service::ecommerce_agent_architecture",
            scenario_id="ecommerce_service",
            module_id="ecommerce_agent_architecture",
            selected_constraint_id="refund_timeout_after_success",
            revealed_constraint_ids=["refund_timeout_after_success"],
            retrieval_status="hit",
        )
        context = LockedScenarioContext(
            scenario_id="ecommerce_service",
            module_id="ecommerce_agent_architecture",
            retrieval_unit_id="ecommerce_service::ecommerce_agent_architecture",
            business_goal="支持商品咨询、订单查询和退款",
            opening_goal="验证整体组件划分和任务路由",
            selected_constraint=ScenarioConstraint(
                constraint_id="refund_timeout_after_success",
                scenario_id="ecommerce_service",
                module_id="ecommerce_agent_architecture",
                evidence_gap_tags=["幂等"],
                difficulty="intermediate",
                fact="退款实际已经执行成功，但接口响应超时",
            ),
            revealed_constraint_ids=["refund_timeout_after_success"],
            retrieval_status="hit",
            provenance=provenance,
        )
        self.assertEqual(context.provenance.selected_constraint_id, "refund_timeout_after_success")

    def test_generated_question_remains_llm_text_only_compatible(self) -> None:
        question = GeneratedQuestion.model_validate({"text": "问题"})
        self.assertEqual(question.text, "问题")

    def test_manifest_uses_unified_role_identity(self) -> None:
        manifest = ScenarioBankManifest(
            role_family="ai_application_engineering",
            role_profile_version="2026-H2",
            scenario_count=10,
            retrieval_module_count=35,
        )
        self.assertEqual(manifest.role_family, "ai_application_engineering")
        self.assertEqual(manifest.role_profile_version, "2026-H2")


if __name__ == "__main__":
    unittest.main()
