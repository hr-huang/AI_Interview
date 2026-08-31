from __future__ import annotations

import ast
from datetime import date
import inspect
import unittest

import profile_agent.services.score_engine_service as score_engine_module
from profile_agent.schemas.report_schema import (
    ClaimVerification,
    CompetencyDimensionRubric,
    RequirementEvidenceAssessment,
    RequirementScoringBinding,
    RoleCompetencyProfile,
    RubricCriterion,
    RubricQuality,
    ScoreReason,
    ScoringBlueprint,
)
from profile_agent.services.score_engine_service import (
    calculate_score_snapshot,
    score_requirement,
)


def _criterion(
    criterion_id: str,
    *,
    score_adjustment: int = 0,
) -> RubricCriterion:
    return RubricCriterion(
        id=criterion_id,
        text=criterion_id,
        score_adjustment=score_adjustment,
    )


def make_role_profile(
    *,
    weights: tuple[float, float] = (0.99, 0.01),
    gating: tuple[bool, bool] = (True, False),
) -> RoleCompetencyProfile:
    dimensions = [
        CompetencyDimensionRubric(
            id="role_dim_01",
            name="维度一",
            weight=weights[0],
            is_gating=gating[0],
            minimum_criteria=[_criterion("min_01")],
            excellence_signals=[
                _criterion("sig_plus_01", score_adjustment=2),
                _criterion("sig_plus_02", score_adjustment=3),
                _criterion("sig_plus_03", score_adjustment=3),
            ],
            critical_errors=[
                _criterion("err_01", score_adjustment=-5),
                _criterion("err_02", score_adjustment=-4),
            ],
        ),
        CompetencyDimensionRubric(
            id="role_dim_02",
            name="维度二",
            weight=weights[1],
            is_gating=gating[1],
            minimum_criteria=[_criterion("min_02")],
            excellence_signals=[_criterion("dim2_signal", score_adjustment=5)],
            critical_errors=[_criterion("dim2_error", score_adjustment=-5)],
        ),
    ]
    return RoleCompetencyProfile(
        role_family="ai_application_engineering",
        display_name="AI Agent / AI应用工程师",
        version="2026-H2",
        valid_from=date(2026, 7, 1),
        knowledge_as_of=date(2026, 8, 21),
        dimensions=dimensions,
        source_refs=["test-source"],
    )


def make_blueprint(
    *requirement_dimensions: tuple[str, str, float],
) -> ScoringBlueprint:
    if not requirement_dimensions:
        requirement_dimensions = (("req_01", "role_dim_01", 1.0),)
    return ScoringBlueprint(
        role_family="ai_application_engineering",
        role_profile_version="2026-H2",
        bindings=[
            RequirementScoringBinding(
                requirement_id=requirement_id,
                primary_dimension_id=dimension_id,
                weight_within_dimension=weight,
                rubric_id=dimension_id,
            )
            for requirement_id, dimension_id, weight in requirement_dimensions
        ],
    )


def _assessment(
    requirement_id: str,
    *,
    dimension_id: str = "role_dim_01",
    level: str = "L2",
    confidence: str = "medium",
    matched_excellence_signal_ids: list[str] | None = None,
    unresolved_critical_error_ids: list[str] | None = None,
    assessment_reasons: list[ScoreReason] | None = None,
    coverage: float = 1.0,
    satisfied_minimum_criterion_ids: list[str] | None = None,
) -> RequirementEvidenceAssessment:
    return RequirementEvidenceAssessment(
        requirement_id=requirement_id,
        dimension_id=dimension_id,
        level=level,
        coverage=coverage,
        confidence=confidence,
        satisfied_minimum_criterion_ids=satisfied_minimum_criterion_ids or [],
        matched_excellence_signal_ids=matched_excellence_signal_ids or [],
        unresolved_critical_error_ids=unresolved_critical_error_ids or [],
        accepted_alternative_ids=[],
        supporting_evidence_ids=[],
        limiting_evidence_ids=[],
        transfer_evidence_ids=[],
        quality=RubricQuality(),
        assessment_reasons=assessment_reasons or [],
    )


class ScoreEngineServiceTest(unittest.TestCase):
    def test_dimension_uses_collective_rubric_coverage_not_average_level(self) -> None:
        role_profile = make_role_profile()
        role_profile.dimensions[0].minimum_criteria.append(_criterion("min_01b"))
        blueprint = make_blueprint(
            ("req_01", "role_dim_01", 0.5),
            ("req_02", "role_dim_01", 0.5),
        )

        snapshot = calculate_score_snapshot(
            role_profile,
            blueprint,
            [
                _assessment(
                    "req_01",
                    level="L1",
                    satisfied_minimum_criterion_ids=["min_01"],
                    matched_excellence_signal_ids=["sig_plus_01"],
                ),
                _assessment(
                    "req_02",
                    level="L1",
                    satisfied_minimum_criterion_ids=["min_01b"],
                    matched_excellence_signal_ids=["sig_plus_02"],
                ),
            ],
        )

        radar = snapshot.radar_dimensions[0]
        self.assertEqual(radar.level, "L3")
        self.assertEqual(radar.score, 87)

    def test_score_engine_signature_accepts_assessments_not_raw_matches(self) -> None:
        signature = inspect.signature(calculate_score_snapshot)
        parameter_names = set(signature.parameters)

        self.assertNotIn("match_batch", parameter_names)
        self.assertNotIn("evidences", parameter_names)
        self.assertNotIn("turns", parameter_names)
        self.assertIn("role_profile", parameter_names)
        self.assertIn("blueprint", parameter_names)
        self.assertIn("assessments", parameter_names)
        self.assertIn("claim_verifications", parameter_names)

        for forbidden_name in ("RubricMatch", "Evidence", "InterviewTurn"):
            self.assertNotIn(forbidden_name, score_engine_module.__dict__)

        source = inspect.getsource(score_engine_module)
        tree = ast.parse(source)
        imported_names = {
            alias.asname or alias.name.split(".")[-1]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("RubricMatch", imported_names)
        self.assertNotIn("Evidence", imported_names)
        self.assertNotIn("InterviewTurn", imported_names)

    def test_unverified_has_no_requirement_score_and_does_not_enter_average(
        self,
    ) -> None:
        role_profile = make_role_profile()
        blueprint = make_blueprint(
            ("req_01", "role_dim_01", 0.5),
            ("req_02", "role_dim_01", 0.5),
        )
        snapshot = calculate_score_snapshot(
            role_profile,
            blueprint,
            [
                _assessment("req_01", level="L2"),
                _assessment("req_02", level="UNVERIFIED", confidence="low"),
            ],
        )

        self.assertEqual(
            [item.requirement_id for item in snapshot.requirement_scores],
            ["req_01"],
        )
        radar = snapshot.radar_dimensions[0]
        self.assertEqual(radar.score, 65)
        self.assertEqual(radar.coverage, 0.5)

    def test_level_maps_to_exact_base_score(self) -> None:
        expected = {"L0": 20, "L1": 40, "L2": 65, "L3": 82, "L4": 95}
        role_profile = make_role_profile()
        dimension = role_profile.dimensions[0]

        for level, base_score in expected.items():
            with self.subTest(level=level):
                result = score_requirement(
                    _assessment("req_01", level=level),
                    dimension,
                )
                self.assertIsNotNone(result)
                self.assertEqual(result.base_score, base_score)
                self.assertEqual(result.display_score, base_score)

    def test_adjustment_uses_unique_ids_and_is_capped_to_five(self) -> None:
        role_profile = make_role_profile()
        dimension = role_profile.dimensions[0]

        positive = score_requirement(
            _assessment(
                "req_01",
                level="L3",
                matched_excellence_signal_ids=[
                    "sig_plus_01",
                    "sig_plus_01",
                    "sig_plus_02",
                    "sig_plus_03",
                ],
            ),
            dimension,
        )
        negative = score_requirement(
            _assessment(
                "req_02",
                level="L3",
                unresolved_critical_error_ids=["err_01", "err_02", "err_02"],
            ),
            dimension,
        )

        self.assertIsNotNone(positive)
        self.assertIsNotNone(negative)
        self.assertEqual(positive.adjustment, 5)
        self.assertEqual(positive.display_score, 87)
        self.assertEqual(negative.adjustment, -5)
        self.assertEqual(negative.display_score, 77)

    def test_requirement_score_contains_numeric_fields_only(self) -> None:
        snapshot = calculate_score_snapshot(
            make_role_profile(),
            make_blueprint(),
            [_assessment("req_01", level="L3")],
        )

        self.assertEqual(
            set(snapshot.requirement_scores[0].model_dump()),
            {
                "requirement_id",
                "dimension_id",
                "base_score",
                "adjustment",
                "display_score",
            },
        )

    def test_dimension_coverage_uses_assessment_and_binding_weights(self) -> None:
        snapshot = calculate_score_snapshot(
            make_role_profile(),
            make_blueprint(
                ("req_01", "role_dim_01", 0.6),
                ("req_02", "role_dim_01", 0.4),
            ),
            [
                _assessment("req_01", level="L2"),
                _assessment("req_02", level="UNVERIFIED", confidence="low"),
            ],
        )

        self.assertEqual(snapshot.radar_dimensions[0].coverage, 0.6)

    def test_dimension_confidence_is_derived_from_member_assessments(self) -> None:
        role_profile = make_role_profile()
        blueprint = make_blueprint(
            ("req_01", "role_dim_01", 0.5),
            ("req_02", "role_dim_01", 0.5),
        )
        mixed = calculate_score_snapshot(
            role_profile,
            blueprint,
            [
                _assessment("req_01", confidence="high"),
                _assessment("req_02", confidence="low"),
            ],
        )
        all_high = calculate_score_snapshot(
            role_profile,
            blueprint,
            [
                _assessment("req_01", confidence="high"),
                _assessment("req_02", confidence="high"),
            ],
        )

        self.assertNotEqual(mixed.radar_dimensions[0].confidence, "high")
        self.assertEqual(all_high.radar_dimensions[0].confidence, "high")

    def test_job_match_unpublished_below_seventy_percent(self) -> None:
        snapshot = calculate_score_snapshot(
            make_role_profile(weights=(0.69, 0.31)),
            make_blueprint(
                ("req_01", "role_dim_01", 1.0),
                ("req_02", "role_dim_02", 1.0),
            ),
            [
                _assessment("req_01", level="L2"),
                _assessment("req_02", dimension_id="role_dim_02", level="UNVERIFIED", confidence="low"),
            ],
        )

        self.assertFalse(snapshot.job_match.published)
        self.assertIsNone(snapshot.job_match.raw_score)
        self.assertIsNone(snapshot.job_match.fit_level)
        self.assertEqual(snapshot.job_match.coverage, 0.69)

    def test_job_match_unpublished_if_gating_dimension_unverified(self) -> None:
        snapshot = calculate_score_snapshot(
            make_role_profile(weights=(0.2, 0.8), gating=(True, False)),
            make_blueprint(
                ("req_01", "role_dim_01", 1.0),
                ("req_02", "role_dim_02", 1.0),
            ),
            [
                _assessment("req_01", level="UNVERIFIED", confidence="low"),
                _assessment("req_02", dimension_id="role_dim_02", level="L4", confidence="high"),
            ],
        )

        self.assertGreaterEqual(snapshot.job_match.coverage, 0.70)
        self.assertFalse(snapshot.job_match.published)
        self.assertIsNone(snapshot.job_match.raw_score)
        self.assertIsNone(snapshot.job_match.fit_level)

    def test_gating_l0_caps_fit_level_but_preserves_raw_score(self) -> None:
        l0_reason = ScoreReason(
            reason_type="critical_error",
            text="明确存在关键错误。",
            evidence_ids=["ev_l0"],
            rubric_signal_ids=["err_01"],
        )
        snapshot = calculate_score_snapshot(
            make_role_profile(weights=(2 / 17, 15 / 17)),
            make_blueprint(
                ("req_01", "role_dim_01", 1.0),
                ("req_02", "role_dim_02", 1.0),
            ),
            [
                _assessment(
                    "req_01",
                    level="L0",
                    confidence="high",
                    unresolved_critical_error_ids=["err_01"],
                    assessment_reasons=[l0_reason],
                ),
                _assessment(
                    "req_02",
                    dimension_id="role_dim_02",
                    level="L4",
                    confidence="high",
                    matched_excellence_signal_ids=["dim2_signal"],
                ),
            ],
        )

        self.assertTrue(snapshot.job_match.published)
        self.assertEqual(snapshot.job_match.raw_score, 90)
        self.assertEqual(snapshot.job_match.fit_level, "有条件匹配")
        self.assertTrue(snapshot.job_match.limiting_reasons)

    def test_every_scored_dimension_has_two_reasons(self) -> None:
        assessment_reason = ScoreReason(
            reason_type="strength",
            text="已验证一个正向事实。",
            evidence_ids=["ev_01"],
            rubric_signal_ids=["min_01"],
        )
        snapshot = calculate_score_snapshot(
            make_role_profile(),
            make_blueprint(),
            [_assessment("req_01", assessment_reasons=[assessment_reason])],
        )

        radar = snapshot.radar_dimensions[0]
        self.assertGreaterEqual(len(radar.score_reasons), 2)
        self.assertTrue(
            any(reason.reason_type == "unverified" for reason in radar.score_reasons)
        )

    def test_same_assessments_produce_identical_snapshot(self) -> None:
        role_profile = make_role_profile()
        blueprint = make_blueprint(
            ("req_01", "role_dim_01", 1.0),
        )
        assessments = [_assessment("req_01", level="L3", confidence="high")]
        claims = [
            ClaimVerification(
                claim_id="claim_01",
                status="supported",
                supporting_evidence_ids=["ev_01"],
                explanation="存在支持证据。",
            )
        ]

        first = calculate_score_snapshot(
            role_profile,
            blueprint,
            assessments,
            claims,
        )
        second = calculate_score_snapshot(
            role_profile,
            blueprint,
            assessments,
            claims,
        )

        self.assertEqual(first.model_dump(), second.model_dump())


if __name__ == "__main__":
    unittest.main()
