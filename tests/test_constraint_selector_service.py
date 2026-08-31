from datetime import date
import unittest

from profile_agent.schemas.scenario_rag_schema import ScenarioConstraint, ScenarioModule
from profile_agent.services.constraint_selector_service import select_constraint


def make_module() -> ScenarioModule:
    return ScenarioModule(
        module_id="ecommerce_safety_evaluation",
        scenario_id="ecommerce_service",
        primary_dimension_id="role_dim_05",
        supported_requirement_types=["problem_solving"],
        supported_modes=["scenario"],
        difficulties=["foundation", "intermediate"],
        opening_goal="验证高风险工具调用和安全治理",
        semantic_text="退款权限 工具风险控制 评测 人工审核",
        evidence_signals=["权限", "幂等"],
        constraint_ids=["refund_timeout_after_success", "high_value_refund"],
        valid_from=date(2026, 8, 29),
        valid_until=date(2027, 2, 28),
    )


def make_constraint(constraint_id: str, tags: list[str], fact: str) -> ScenarioConstraint:
    return ScenarioConstraint(
        constraint_id=constraint_id,
        scenario_id="ecommerce_service",
        module_id="ecommerce_safety_evaluation",
        evidence_gap_tags=tags,
        difficulty="intermediate",
        fact=fact,
    )


class ConstraintSelectorTests(unittest.TestCase):
    def test_selects_one_unused_exact_gap_constraint(self) -> None:
        selected = select_constraint(
            module=make_module(),
            constraints=[
                make_constraint("refund_timeout_after_success", ["幂等", "失败恢复"], "退款已成功但响应超时"),
                make_constraint("high_value_refund", ["高风险工具"], "退款金额异常高"),
            ],
            evidence_gap_tags=["幂等", "失败恢复"],
            revealed_ids=[],
            difficulty="intermediate",
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.constraint_id, "refund_timeout_after_success")

    def test_continue_never_reuses_revealed_constraint(self) -> None:
        selected = select_constraint(
            module=make_module(),
            constraints=[
                make_constraint("refund_timeout_after_success", ["幂等"], "退款已成功但响应超时"),
            ],
            evidence_gap_tags=["幂等"],
            revealed_ids=["refund_timeout_after_success"],
            difficulty="intermediate",
        )
        self.assertIsNone(selected)

    def test_missing_structured_gap_uses_declared_module_order(self) -> None:
        selected = select_constraint(
            module=make_module(),
            constraints=[
                make_constraint("high_value_refund", ["高风险工具"], "退款金额异常高"),
                make_constraint("refund_timeout_after_success", ["幂等"], "退款已成功但响应超时"),
            ],
            evidence_gap_tags=[],
            revealed_ids=[],
            difficulty="intermediate",
        )
        self.assertEqual(selected.constraint_id, "refund_timeout_after_success")


if __name__ == "__main__":
    unittest.main()
