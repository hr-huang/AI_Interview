from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import unittest

from profile_agent.schemas.interview_schema import AssessmentTarget, AskAction, EvidenceRequirement, InterviewPlan
from profile_agent.schemas.runtime_schema import InterviewTurn
from profile_agent.schemas.scenario_rag_schema import QuestionProvenance, ScenarioSelection
from profile_agent.services.scenario_bank_service import ScenarioCatalog
from profile_agent.services.question_context_service import prepare_question_context


ROOT = Path(__file__).resolve().parents[1] / "profile_agent" / "knowledge" / "scenario_banks" / "ai_application_engineering_2026_h2"


def make_plan(
    mode: str = "scenario",
    *,
    candidate_focus: str | None = None,
    dimension_id: str = "role_dim_03",
    description: str = "验证 Memory 写入和删除边界",
) -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=5,
        closing_buffer_minutes=0,
        targets=[AssessmentTarget(
            id="target_01",
            objective="验证 Context 和 Memory 业务链路",
            target_type="system_design",
            competency_ids=[],
            evidence_requirements=[EvidenceRequirement(
                id="req_01",
                description=description,
                candidate_focus=candidate_focus,
                planned_role_dimension_id=dimension_id,
            )],
            related_claim_ids=[],
            priority="high",
            must_cover=True,
            time_budget_minutes=10,
            preferred_modes=[mode],
        )],
    )


class FakeRetriever:
    def __init__(self, selection: ScenarioSelection) -> None:
        self.selection = selection
        self.calls: list[object] = []

    def retrieve(self, request: object, *, as_of: date) -> ScenarioSelection:
        self.calls.append((request, as_of))
        return self.selection


def action(mode: str = "scenario") -> AskAction:
    return AskAction(
        target_id="target_01",
        primary_requirement_id="req_01",
        question_mode=mode,
        reason="验证 Memory 删除边界",
    )


class PrepareQuestionContextTests(unittest.TestCase):
    AS_OF = date(2026, 8, 29)

    def test_new_scenario_calls_retriever_once_and_builds_opening_provenance(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        retriever = FakeRetriever(ScenarioSelection(
            status="hit",
            retrieval_unit_id="enterprise_knowledge_assistant::knowledge_rag_memory",
        ))

        context = prepare_question_context(
            action=action(), plan=make_plan(candidate_focus="多轮记忆治理"), history=[], catalog=catalog,
            retriever=retriever, as_of=self.AS_OF, evidence_gap_tags=["删除"],
        )

        self.assertEqual(len(retriever.calls), 1)
        self.assertEqual(context.retrieval_unit_id, "enterprise_knowledge_assistant::knowledge_rag_memory")
        self.assertEqual(context.primary_dimension_id, "role_dim_03")
        self.assertIsNone(context.selected_constraint)
        self.assertEqual(context.revealed_constraint_ids, [])
        self.assertEqual(
            context.candidate_brief,
            catalog.get_scenario("enterprise_knowledge_assistant").candidate_brief,
        )
        self.assertEqual(context.candidate_focus, "多轮记忆治理")
        self.assertEqual(context.provenance.target_requirement_id, "req_01")
        self.assertEqual(context.provenance.retrieval_unit_id, context.retrieval_unit_id)

    def test_selection_copy_cannot_override_canonical_candidate_assets(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        scenario = catalog.get_scenario("ecommerce_service")
        module = catalog.get_module("ecommerce_safety_evaluation")
        hidden_fact = catalog.get_constraint("refund_timeout_after_success").fact
        unsafe_scenario = scenario.model_copy(
            update={
                "candidate_brief": f"{hidden_fact}。",
                "business_goal": "为电商平台提供日常客户服务。",
            }
        )

        context = prepare_question_context(
            action=action(),
            plan=make_plan(
                candidate_focus="权限校验策略",
                dimension_id="role_dim_05",
            ),
            history=[],
            catalog=catalog,
            retriever=FakeRetriever(
                ScenarioSelection(
                    status="hit",
                    retrieval_unit_id=(
                        "ecommerce_service::ecommerce_safety_evaluation"
                    ),
                    scenario=unsafe_scenario,
                    module=module,
                )
            ),
            as_of=self.AS_OF,
        )

        self.assertEqual(context.candidate_brief, scenario.candidate_brief)
        self.assertEqual(context.business_goal, scenario.business_goal)
        self.assertEqual(context.candidate_focus, "整体方案设计")
        dumped = repr(context.model_dump(mode="json"))
        self.assertNotIn(hidden_fact, dumped)
        self.assertNotIn("权限校验策略", dumped)
        self.assertNotIn("hidden_phrases", dumped)

    def test_short_focus_ngram_overlap_blocks_rewritten_hidden_facts(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        scenario = catalog.get_scenario("ecommerce_service")
        for focus in ("款项成功接口超时策略", "退款成功服务超时策略"):
            with self.subTest(focus=focus):
                context = prepare_question_context(
                    action=action(),
                    plan=make_plan(candidate_focus=focus, dimension_id="role_dim_05"),
                    history=[],
                    catalog=catalog,
                    retriever=FakeRetriever(ScenarioSelection(
                        status="hit",
                        retrieval_unit_id="ecommerce_service::ecommerce_safety_evaluation",
                    )),
                    as_of=self.AS_OF,
                )
                self.assertEqual(context.candidate_brief, scenario.candidate_brief)
                self.assertEqual(context.candidate_focus, "整体方案设计")

    def test_short_module_signals_are_never_projected_as_focus(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        cases = (
            ("cost_monitor_performance", "新鲜度", "role_dim_06"),
            ("recruitment_agent_architecture", "证据链", "role_dim_01"),
            ("sales_context_memory_tools", "Memory 删除", "role_dim_03"),
        )
        for module_id, focus, dimension_id in cases:
            with self.subTest(focus=focus):
                module = catalog.get_module(module_id)
                context = prepare_question_context(
                    action=action(),
                    plan=make_plan(candidate_focus=focus, dimension_id=dimension_id),
                    history=[],
                    catalog=catalog,
                    retriever=FakeRetriever(ScenarioSelection(
                        status="hit",
                        retrieval_unit_id=module.retrieval_unit_id,
                    )),
                    as_of=self.AS_OF,
                )
                self.assertEqual(context.candidate_focus, "整体方案设计")

    def test_all_canonical_briefs_survive_context_projection_unchanged(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        for scenario in catalog.scenarios.values():
            modules = sorted(
                (
                    module for module in catalog.active_modules
                    if module.scenario_id == scenario.scenario_id
                    and "scenario" in module.supported_modes
                    and "system_design" in module.supported_requirement_types
                    and "intermediate" in module.difficulties
                ),
                key=lambda module: module.module_id,
            )
            with self.subTest(scenario_id=scenario.scenario_id):
                self.assertTrue(modules)
                module = modules[0]
                context = prepare_question_context(
                    action=action(),
                    plan=make_plan(dimension_id=module.primary_dimension_id),
                    history=[],
                    catalog=catalog,
                    retriever=FakeRetriever(ScenarioSelection(
                        status="hit",
                        retrieval_unit_id=module.retrieval_unit_id,
                    )),
                    as_of=self.AS_OF,
                )
                self.assertEqual(context.candidate_brief, scenario.candidate_brief)
                self.assertEqual(context.business_goal, scenario.business_goal)

    def test_single_generic_term_does_not_trigger_hidden_overlap(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        scenario = catalog.get_scenario("ecommerce_service")
        generic_scenario = scenario.model_copy(
            update={"candidate_brief": "你需要为用户提供订单查询和退款申请服务。"}
        )
        catalog = replace(
            catalog,
            scenarios={**catalog.scenarios, scenario.scenario_id: generic_scenario},
        )

        context = prepare_question_context(
            action=action(),
            plan=make_plan(candidate_focus="退款治理", dimension_id="role_dim_05"),
            history=[],
            catalog=catalog,
            retriever=FakeRetriever(ScenarioSelection(
                status="hit",
                retrieval_unit_id="ecommerce_service::ecommerce_safety_evaluation",
            )),
            as_of=self.AS_OF,
        )

        self.assertEqual(context.candidate_brief, generic_scenario.candidate_brief)
        self.assertEqual(context.candidate_focus, "退款治理")

    def test_legacy_missing_or_invalid_focus_uses_safe_description_fallback(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        retriever = FakeRetriever(ScenarioSelection(
            status="hit",
            retrieval_unit_id="enterprise_knowledge_assistant::knowledge_rag_memory",
        ))
        for candidate_focus in (None, "必须直接调用工具策略"):
            with self.subTest(candidate_focus=candidate_focus):
                context = prepare_question_context(
                    action=action(),
                    plan=make_plan(
                        candidate_focus=candidate_focus,
                        description="验证候选人能否说明上下文压缩",
                    ),
                    history=[],
                    catalog=catalog,
                    retriever=retriever,
                    as_of=self.AS_OF,
                )
                self.assertEqual(context.candidate_focus, "上下文压缩")

    def test_hidden_description_fallback_fails_closed_to_generic(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        context = prepare_question_context(
            action=action(),
            plan=make_plan(
                candidate_focus=None,
                dimension_id="role_dim_05",
                description="验证退款成功响应超时策略",
            ),
            history=[],
            catalog=catalog,
            retriever=FakeRetriever(ScenarioSelection(
                status="hit",
                retrieval_unit_id="ecommerce_service::ecommerce_safety_evaluation",
            )),
            as_of=self.AS_OF,
        )
        self.assertEqual(context.candidate_focus, "整体方案设计")

    def test_foundation_bypasses_scenario_retriever(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        retriever = FakeRetriever(ScenarioSelection(status="hit", retrieval_unit_id="bad::id"))

        context = prepare_question_context(
            action=action("foundation"), plan=make_plan("foundation"), history=[],
            catalog=catalog, retriever=retriever, as_of=self.AS_OF,
        )

        self.assertIsNone(context)
        self.assertEqual(retriever.calls, [])

    def test_scenario_without_retriever_uses_deterministic_fallback(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)

        context = prepare_question_context(
            action=action(), plan=make_plan(), history=[], catalog=catalog,
            retriever=None, as_of=self.AS_OF,
        )

        self.assertEqual(context.retrieval_status, "fallback")
        self.assertEqual(context.fallback_reason, "scenario retriever unavailable")
        self.assertEqual(context.primary_dimension_id, "role_dim_03")
        self.assertIsNone(context.selected_constraint)

    def test_follow_up_reuses_latest_module_and_releases_one_unused_constraint(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        module = catalog.get_module("knowledge_rag_memory")
        first_id = module.constraint_ids[0]
        history = [InterviewTurn(
            id="turn_001", sequence_number=1, target_id="target_01",
            primary_requirement_id="req_01", question_mode="scenario", question="上一题",
            asked_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
            question_provenance=QuestionProvenance(
                target_requirement_id="req_01", primary_dimension_id="role_dim_03",
                retrieval_unit_id=module.retrieval_unit_id, scenario_id=module.scenario_id,
                module_id=module.module_id, selected_constraint_id=first_id,
                revealed_constraint_ids=[first_id], retrieval_status="hit",
            ),
        )]
        retriever = FakeRetriever(ScenarioSelection(status="hit", retrieval_unit_id="bad::id"))

        context = prepare_question_context(
            action=action("follow_up"), plan=make_plan("follow_up"), history=history,
            catalog=catalog, retriever=retriever, as_of=self.AS_OF,
        )

        self.assertEqual(retriever.calls, [])
        self.assertIsNotNone(context.selected_constraint)
        selected_id = context.provenance.selected_constraint_id
        self.assertNotEqual(selected_id, first_id)
        self.assertEqual(context.revealed_constraint_ids, [first_id, selected_id])
        self.assertEqual(context.provenance.revealed_constraint_ids, context.revealed_constraint_ids)
        dumped = context.model_dump(mode="json")
        self.assertNotIn("opening_goal", dumped)
        self.assertEqual(set(dumped["selected_constraint"]), {"fact"})
        self.assertNotIn("constraint_id", repr(dumped["selected_constraint"]))

    def test_follow_up_without_prior_scenario_bypasses(self) -> None:
        catalog = ScenarioCatalog.load(ROOT, as_of=self.AS_OF)
        retriever = FakeRetriever(ScenarioSelection(status="hit", retrieval_unit_id="bad::id"))

        context = prepare_question_context(
            action=action("follow_up"), plan=make_plan("follow_up"), history=[],
            catalog=catalog, retriever=retriever, as_of=self.AS_OF,
        )

        self.assertIsNone(context)
        self.assertEqual(retriever.calls, [])


if __name__ == "__main__":
    unittest.main()
