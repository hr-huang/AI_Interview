from collections import Counter
from datetime import date
import unittest

from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
    InterviewPlan,
)
from profile_agent.schemas.report_schema import (
    CompetencyDimensionRubric,
    RequirementBindingDraft,
    RoleCompetencyProfile,
    RubricCriterion,
    ScoringBlueprint,
    ScoringBlueprintDraft,
)
from profile_agent.services.scoring_blueprint_service import (
    BlueprintValidationError,
    build_scoring_blueprint,
)


class FakeLLM:
    def __init__(self, response: ScoringBlueprintDraft) -> None:
        self.response = response
        self.calls: list[tuple[list[tuple[str, str]], type]] = []

    def structured(self, messages: list[tuple[str, str]], schema: type):
        self.calls.append((messages, schema))
        return self.response


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        duration_minutes=30,
        max_questions=10,
        closing_buffer_minutes=2,
        targets=[
            AssessmentTarget(
                id="target_01",
                objective="验证候选人设计可靠 AI 工作流的能力",
                target_type="system_design",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id="req_01",
                        description="能够拆分状态、节点和工具边界",
                    ),
                    EvidenceRequirement(
                        id="req_02",
                        description="能够解释失败恢复和人工介入",
                    ),
                    EvidenceRequirement(
                        id="req_03",
                        description="能够设计可验证的业务成功标准",
                    ),
                ],
                related_claim_ids=[],
                priority="high",
                must_cover=True,
                time_budget_minutes=20,
                preferred_modes=["system_design", "scenario"],
            )
        ],
    )


def _criterion(criterion_id: str, text: str) -> RubricCriterion:
    return RubricCriterion(id=criterion_id, text=text)


def make_role_profile() -> RoleCompetencyProfile:
    return RoleCompetencyProfile(
        role_family="ai_application_engineering",
        display_name="AI Agent / AI应用工程师",
        version="2026-H2",
        valid_from=date(2026, 7, 1),
        knowledge_as_of=date(2026, 8, 21),
        dimensions=[
            CompetencyDimensionRubric(
                id="role_dim_01",
                name="AI应用与Agent编排",
                weight=0.60,
                is_gating=True,
                minimum_criteria=[
                    _criterion("d01_min_01", "拆分节点、状态和工具边界")
                ],
                excellence_signals=[
                    _criterion("d01_exc_01", "能够比较编排方案")
                ],
                critical_errors=[
                    _criterion("d01_err_01", "无差别拆成多 Agent")
                ],
                accepted_alternatives=[],
            ),
            CompetencyDimensionRubric(
                id="role_dim_02",
                name="业务理解与任务建模",
                weight=0.40,
                is_gating=False,
                minimum_criteria=[
                    _criterion("d02_min_01", "定义成功标准和失败边界")
                ],
                excellence_signals=[
                    _criterion("d02_exc_01", "把模糊需求转成规格")
                ],
                critical_errors=[
                    _criterion("d02_err_01", "无验收标准直接选模型")
                ],
                accepted_alternatives=[],
            ),
        ],
        source_refs=["test-source"],
    )


def make_draft(
    bindings: list[RequirementBindingDraft] | None = None,
) -> ScoringBlueprintDraft:
    return ScoringBlueprintDraft(
        bindings=bindings
        or [
            RequirementBindingDraft(
                requirement_id="req_01",
                primary_dimension_id="role_dim_01",
                rubric_id="role_dim_01",
            ),
            RequirementBindingDraft(
                requirement_id="req_02",
                primary_dimension_id="role_dim_01",
                rubric_id="role_dim_01",
            ),
            RequirementBindingDraft(
                requirement_id="req_03",
                primary_dimension_id="role_dim_02",
                rubric_id="role_dim_02",
            ),
        ]
    )


class ScoringBlueprintServiceTest(unittest.TestCase):
    def test_calls_structured_llm_once(self) -> None:
        fake_llm = FakeLLM(make_draft())

        result = build_scoring_blueprint(
            make_plan(), make_role_profile(), llm_client=fake_llm
        )

        self.assertIsInstance(result, ScoringBlueprint)
        self.assertEqual(len(fake_llm.calls), 1)
        messages, schema = fake_llm.calls[0]
        self.assertIs(schema, ScoringBlueprintDraft)
        self.assertIsInstance(fake_llm.response, ScoringBlueprintDraft)
        prompt = "\n".join(content for _, content in messages)
        self.assertIn("验证候选人设计可靠 AI 工作流的能力", prompt)
        self.assertIn("能够拆分状态、节点和工具边界", prompt)
        self.assertIn("system_design", prompt)
        self.assertIn("role_dim_01", prompt)
        self.assertIn("AI应用与Agent编排", prompt)
        self.assertIn("拆分节点、状态和工具边界", prompt)
        self.assertIn("每个 Requirement 恰好绑定一次", prompt)
        self.assertIn("不得评分", prompt)

    def test_prompt_pins_exact_root_json_shape_for_mimo(self) -> None:
        fake_llm = FakeLLM(make_draft())

        build_scoring_blueprint(
            make_plan(), make_role_profile(), llm_client=fake_llm
        )

        messages, _ = fake_llm.calls[0]
        prompt = "\n".join(content for _, content in messages)
        self.assertIn('根对象只能包含 "bindings"', prompt)
        self.assertIn('"rubric_id"', prompt)
        self.assertIn("不要添加 scoring_blueprint_draft 外层字段", prompt)

    def test_binds_every_plan_requirement_exactly_once(self) -> None:
        result = build_scoring_blueprint(
            make_plan(), make_role_profile(), llm_client=FakeLLM(make_draft())
        )

        counts = Counter(binding.requirement_id for binding in result.bindings)
        self.assertEqual(set(counts), {"req_01", "req_02", "req_03"})
        self.assertEqual(set(counts.values()), {1})

    def test_two_requirements_in_same_dimension_each_get_half_weight(self) -> None:
        result = build_scoring_blueprint(
            make_plan(), make_role_profile(), llm_client=FakeLLM(make_draft())
        )

        weights = {
            binding.requirement_id: binding.weight_within_dimension
            for binding in result.bindings
        }
        self.assertAlmostEqual(weights["req_01"], 0.5)
        self.assertAlmostEqual(weights["req_02"], 0.5)
        self.assertAlmostEqual(weights["req_03"], 1.0)

    def test_missing_binding_is_rejected(self) -> None:
        draft = make_draft(make_draft().bindings[:-1])

        with self.assertRaisesRegex(BlueprintValidationError, "req_03"):
            build_scoring_blueprint(
                make_plan(), make_role_profile(), llm_client=FakeLLM(draft)
            )

    def test_duplicate_binding_is_rejected(self) -> None:
        bindings = make_draft().bindings
        duplicate = make_draft([bindings[0], bindings[0], bindings[2]])

        with self.assertRaisesRegex(BlueprintValidationError, "req_01"):
            build_scoring_blueprint(
                make_plan(), make_role_profile(), llm_client=FakeLLM(duplicate)
            )

    def test_unknown_requirement_or_dimension_is_rejected(self) -> None:
        unknown_requirement = make_draft(
            [
                RequirementBindingDraft(
                    requirement_id="req_missing",
                    primary_dimension_id="role_dim_01",
                    rubric_id="role_dim_01",
                ),
                *make_draft().bindings[1:],
            ]
        )
        with self.subTest(kind="requirement"):
            with self.assertRaisesRegex(BlueprintValidationError, "req_missing"):
                build_scoring_blueprint(
                    make_plan(),
                    make_role_profile(),
                    llm_client=FakeLLM(unknown_requirement),
                )

        unknown_dimension = make_draft(
            [
                RequirementBindingDraft(
                    requirement_id="req_01",
                    primary_dimension_id="role_dim_missing",
                    rubric_id="role_dim_missing",
                ),
                *make_draft().bindings[1:],
            ]
        )
        with self.subTest(kind="dimension"):
            with self.assertRaisesRegex(
                BlueprintValidationError, "role_dim_missing"
            ):
                build_scoring_blueprint(
                    make_plan(),
                    make_role_profile(),
                    llm_client=FakeLLM(unknown_dimension),
                )

    def test_rubric_id_must_match_primary_dimension(self) -> None:
        invalid_draft = make_draft(
            [
                RequirementBindingDraft(
                    requirement_id="req_01",
                    primary_dimension_id="role_dim_01",
                    rubric_id="rubric_other",
                ),
                *make_draft().bindings[1:],
            ]
        )

        with self.assertRaisesRegex(BlueprintValidationError, "rubric_other"):
            build_scoring_blueprint(
                make_plan(),
                make_role_profile(),
                llm_client=FakeLLM(invalid_draft),
            )

    def test_input_objects_are_not_mutated(self) -> None:
        plan = make_plan()
        role_profile = make_role_profile()
        plan_before = plan.model_dump()
        role_profile_before = role_profile.model_dump()

        build_scoring_blueprint(
            plan, role_profile, llm_client=FakeLLM(make_draft())
        )

        self.assertEqual(plan.model_dump(), plan_before)
        self.assertEqual(role_profile.model_dump(), role_profile_before)


if __name__ == "__main__":
    unittest.main()
