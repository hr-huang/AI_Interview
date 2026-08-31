from datetime import date
import unittest

from profile_agent.schemas.interview_schema import AskAction, GeneratedQuestion
from profile_agent.schemas.scenario_rag_schema import (
    LockedScenarioContext,
    QuestionProvenance,
    ScenarioBankManifest,
    ScenarioCard,
    ScenarioCandidate,
    ScenarioCandidateSet,
    ScenarioConstraint,
    ScenarioModule,
    ScenarioRetrievalRequest,
    ScenarioSourceRecord,
)


class ScenarioRagSchemaTests(unittest.TestCase):
    def test_source_record_exposes_typed_traceability_fields(self) -> None:
        source = ScenarioSourceRecord(
            source_id="anthropic-demystifying-evals-2026-01-09",
            title="Demystifying evals for AI agents",
            source_type="external_engineering_practice",
            publisher="Anthropic",
            source_url="https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents",
            published_at="2026-01-09",
            retrieved_at="2026-08-25",
            supports_dimension_ids=["role_dim_04", "role_dim_05", "role_dim_06"],
            notes="Role Pack 已审核的工程实践来源。",
        )

        self.assertEqual(source.publisher, "Anthropic")
        self.assertEqual(source.published_at, date(2026, 1, 9))
        self.assertEqual(source.retrieved_at, date(2026, 8, 25))
        self.assertEqual(
            source.supports_dimension_ids,
            ["role_dim_04", "role_dim_05", "role_dim_06"],
        )
        self.assertEqual(
            str(source.source_url),
            "https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents",
        )

    def test_source_record_rejects_invalid_traceability_metadata(self) -> None:
        valid = {
            "source_id": "source_demo",
            "publisher": "审核发布方",
            "source_url": "https://example.com/source",
            "published_at": "2026-01-09",
            "retrieved_at": "2026-08-25",
            "supports_dimension_ids": ["role_dim_01"],
        }
        invalid_overrides = (
            {"publisher": "   "},
            {"source_url": "file:///tmp/source"},
            {"published_at": "not-a-date"},
            {"retrieved_at": "2026-13-01"},
            {"supports_dimension_ids": []},
            {"supports_dimension_ids": ["role_dim_01", "role_dim_01"]},
            {"supports_dimension_ids": ["role_dim_07"]},
        )

        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                ScenarioSourceRecord(**(valid | overrides))

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
            candidate_brief="  你正在为一家电商平台设计智能客服 Agent，帮助用户完成售前咨询和售后服务。  ",
            modules=["ecommerce_agent_architecture"],
            source_ids=["source_01"],
            valid_from=date(2026, 8, 29),
            valid_until=date(2027, 2, 28),
        )
        self.assertEqual(
            card.candidate_brief,
            "你正在为一家电商平台设计智能客服 Agent，帮助用户完成售前咨询和售后服务。",
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

    def test_candidate_brief_rejects_newlines_and_question_marks(self) -> None:
        common = {
            "scenario_id": "ecommerce_service",
            "title": "电商智能客服",
            "business_goal": "支持商品咨询、订单查询和退款",
            "valid_from": date(2026, 8, 29),
        }
        with self.assertRaises(ValueError):
            ScenarioCard(**common, candidate_brief="第一句\n第二句")
        with self.assertRaises(ValueError):
            ScenarioCard(**common, candidate_brief="这是候选人可见的场景吗？")
        with self.assertRaises(ValueError):
            ScenarioCard(**common, candidate_brief="第一句。第二句。第三句。")
        with self.assertRaises(ValueError):
            ScenarioCard(**common, candidate_brief="场" * 1000 + "。")
        for separator in ("\t", "\v", "\f", "\u2028", "\u2029"):
            with self.subTest(separator=repr(separator)), self.assertRaises(ValueError):
                ScenarioCard(
                    **common,
                    candidate_brief=f"第一句{separator}第二句。",
                )

    def test_locked_context_exposes_only_independent_candidate_brief_projection(self) -> None:
        context = LockedScenarioContext(
            scenario_id="ecommerce_service",
            module_id="ecommerce_agent_architecture",
            retrieval_unit_id="ecommerce_service::ecommerce_agent_architecture",
            primary_dimension_id="role_dim_01",
            business_goal="内部业务目标",
            opening_goal="内部 opening goal",
            candidate_brief="候选人可见的业务场景简介。",
            retrieval_status="hit",
        )
        self.assertEqual(context.candidate_brief, "候选人可见的业务场景简介。")

        legacy = LockedScenarioContext(
            scenario_id="ecommerce_service",
            module_id="ecommerce_agent_architecture",
            retrieval_unit_id="ecommerce_service::ecommerce_agent_architecture",
            primary_dimension_id="role_dim_01",
            business_goal="旧业务目标",
            opening_goal="旧 opening goal",
            retrieval_status="hit",
        )
        self.assertIsNone(legacy.candidate_brief)

    def test_locked_context_discards_legacy_canonical_objects_and_internal_fields(self) -> None:
        legacy_payload = {
            "scenario_id": "ecommerce_service",
            "module_id": "ecommerce_agent_architecture",
            "retrieval_unit_id": "ecommerce_service::ecommerce_agent_architecture",
            "primary_dimension_id": "role_dim_01",
            "business_goal": "支持订单查询和退款",
            "candidate_brief": "为电商平台设计候选人可见的客服场景。",
            "candidate_focus": "状态一致性设计",
            "opening_goal": "验证整体架构设计",
            "retrieval_status": "hit",
            "scenario": {
                "scenario_id": "ecommerce_service",
                "title": "内部 canonical scenario",
                "business_goal": "内部业务目标",
                "base_constraints": ["PRIVATE_BASE_CONSTRAINT"],
                "modules": ["ecommerce_agent_architecture"],
                "valid_from": date(2026, 8, 29),
            },
            "module": {
                "module_id": "ecommerce_agent_architecture",
                "scenario_id": "ecommerce_service",
                "primary_dimension_id": "role_dim_01",
                "supported_requirement_types": ["system_design"],
                "supported_modes": ["scenario"],
                "difficulties": ["intermediate"],
                "opening_goal": "内部开场目标",
                "semantic_text": "PRIVATE_SEMANTIC_TEXT",
                "evidence_signals": ["PRIVATE_EVIDENCE_SIGNAL"],
                "critical_errors": ["PRIVATE_CRITICAL_ERROR"],
                "valid_from": date(2026, 8, 29),
            },
        }

        context = LockedScenarioContext.model_validate(legacy_payload)
        dumped = context.model_dump(mode="json")

        self.assertEqual(context.primary_dimension_id, "role_dim_01")
        self.assertEqual(context.candidate_focus, "状态一致性设计")
        self.assertFalse(hasattr(context, "scenario"))
        self.assertFalse(hasattr(context, "module"))
        for forbidden in (
            "scenario",
            "module",
            "opening_goal",
            "evidence_signals",
            "critical_errors",
            "base_constraints",
            "hidden_phrases",
        ):
            self.assertNotIn(forbidden, dumped)
        serialized = repr(dumped)
        for forbidden in (
            "PRIVATE_BASE_CONSTRAINT",
            "PRIVATE_EVIDENCE_SIGNAL",
            "PRIVATE_CRITICAL_ERROR",
            "PRIVATE_SEMANTIC_TEXT",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_legacy_context_reads_dimension_from_module_before_discarding_it(self) -> None:
        payload = {
            "scenario_id": "ecommerce_service",
            "module_id": "ecommerce_agent_architecture",
            "retrieval_unit_id": "ecommerce_service::ecommerce_agent_architecture",
            "business_goal": "支持订单查询和退款",
            "opening_goal": "PRIVATE_OPENING_GOAL",
            "retrieval_status": "hit",
            "module": {"primary_dimension_id": "role_dim_01"},
        }

        context = LockedScenarioContext.model_validate(payload)

        self.assertEqual(context.primary_dimension_id, "role_dim_01")
        self.assertNotIn("module", context.model_dump(mode="json"))
        self.assertNotIn("opening_goal", context.model_dump(mode="json"))

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
        self.assertEqual(
            context.model_dump(mode="json")["selected_constraint"],
            {"fact": "退款实际已经执行成功，但接口响应超时"},
        )
        self.assertNotIn("opening_goal", context.model_dump(mode="json"))

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

    def test_candidate_preserves_reranker_components_and_candidate_set_margin(self) -> None:
        top = ScenarioCandidate(
            retrieval_unit_id="ecommerce_service::ecommerce_agent_architecture",
            score=0.91,
            dense_score=0.8,
            lexical_score=0.7,
            raw_reranker_score=4.2,
            normalized_reranker_score=1.0,
        )
        second = ScenarioCandidate(
            retrieval_unit_id="travel_planner::travel_agent_architecture",
            score=0.66,
            dense_score=0.6,
            lexical_score=0.5,
            raw_reranker_score=1.2,
            normalized_reranker_score=0.0,
        )

        result = ScenarioCandidateSet(
            status="hit",
            candidates=[top, second],
            top1_margin=0.25,
        )

        self.assertEqual(result.top1_margin, 0.25)
        self.assertEqual(result.candidates[0].raw_reranker_score, 4.2)
        self.assertEqual(result.candidates[1].normalized_reranker_score, 0.0)

    def test_candidate_component_scores_must_be_finite(self) -> None:
        with self.assertRaises(ValueError):
            ScenarioCandidate(
                retrieval_unit_id="ecommerce_service::ecommerce_agent_architecture",
                raw_reranker_score=float("nan"),
            )
        with self.assertRaises(ValueError):
            ScenarioCandidateSet(
                status="hit",
                candidates=[
                    ScenarioCandidate(
                        retrieval_unit_id="ecommerce_service::ecommerce_agent_architecture",
                    )
                ],
                top1_margin=float("inf"),
            )

    def test_non_hit_or_short_candidate_sets_cannot_claim_a_top1_margin(self) -> None:
        candidate = ScenarioCandidate(
            retrieval_unit_id="ecommerce_service::ecommerce_agent_architecture",
            score=0.9,
        )
        with self.assertRaises(ValueError):
            ScenarioCandidateSet(status="no_match", top1_margin=0.1)
        with self.assertRaises(ValueError):
            ScenarioCandidateSet(status="hit", candidates=[candidate], top1_margin=0.1)


if __name__ == "__main__":
    unittest.main()
