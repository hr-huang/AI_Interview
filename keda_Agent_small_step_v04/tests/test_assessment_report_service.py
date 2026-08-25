from __future__ import annotations

from datetime import date, datetime, timezone
import unittest

from profile_agent.schemas.claim_schema import ClaimItem, ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
    InterviewPlan,
)
from profile_agent.schemas.report_schema import (
    RequirementBindingDraft,
    RequirementScoringBinding,
    ReportNarrativeDraft,
    RoleCompetencyProfile,
    RubricMatch,
    RubricMatchBatch,
    RubricQuality,
    ScoringBlueprint,
)
from profile_agent.schemas.runtime_schema import (
    Evidence,
    InterviewRuntimeState,
    InterviewTurn,
    RequirementProgress,
)
from profile_agent.schemas.job_schema import JobProfile, JobRequirement
from profile_agent.schemas.resume_schema import ResumeProfile
from profile_agent.services.report_writer_service import fallback_report_narrative


class FakeSemanticServices:
    def __init__(self, *, writer=None, score_engine=None) -> None:
        self.calls: list[str] = []
        self.received_blueprints: list[ScoringBlueprint] = []
        self.writer = writer
        self.score_engine = score_engine
        self.blueprint = _make_blueprint()
        self.matches = _make_matches()

    def build_blueprint(self, plan, role_profile):
        self.calls.append("blueprint")
        return self.blueprint

    def match_rubric(self, plan, blueprint, role_profile, turns, evidences):
        self.calls.append("matches")
        self.received_blueprints.append(blueprint)
        return self.matches

    def write_narrative(self, snapshot, evidences, role_profile):
        self.calls.append("writer")
        if self.writer is not None:
            return self.writer(snapshot, evidences, role_profile)
        return fallback_report_narrative(snapshot, evidences, role_profile)

    def as_mapping(self) -> dict[str, object]:
        services = {
            "blueprint_builder": self.build_blueprint,
            "rubric_matcher": self.match_rubric,
            "narrative_writer": self.write_narrative,
        }
        if self.score_engine is not None:
            services["score_engine"] = self.score_engine
        return services


def _make_plan() -> InterviewPlan:
    targets = []
    modes = [
        "system_design",
        "scenario",
        "project_deep_dive",
        "coding",
        "scenario",
        "follow_up",
    ]
    for index, mode in enumerate(modes, start=1):
        requirement_id = f"req_{index:02d}"
        targets.append(
            AssessmentTarget(
                id=f"target_{index:02d}",
                objective=f"验证维度 {index} 的能力",
                target_type="system_design",
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id=requirement_id,
                        description=f"能够完成维度 {index} 的关键任务",
                    )
                ],
                related_claim_ids=(
                    ["claim_01"]
                    if index == 1
                    else ["claim_02"]
                    if index == 5
                    else []
                ),
                priority="high",
                must_cover=True,
                time_budget_minutes=5,
                preferred_modes=[mode],
            )
        )
    return InterviewPlan(
        duration_minutes=45,
        max_questions=10,
        closing_buffer_minutes=2,
        targets=targets,
    )


def _make_runtime(plan: InterviewPlan, *, stopped: bool = True) -> InterviewRuntimeState:
    progress = {
        requirement.id: RequirementProgress(
            requirement_id=requirement.id,
            status="sufficient" if stopped else "in_progress",
            attempt_count=1,
        )
        for target in plan.targets
        for requirement in target.evidence_requirements
    }
    return InterviewRuntimeState(
        question_count=7,
        started_at=datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc),
        current_target_id="target_05",
        requirement_progress=progress,
        visited_target_ids=[target.id for target in plan.targets],
        stop_requested=stopped,
        stop_reason="回答收集完成" if stopped else None,
    )


def _make_turns() -> list[InterviewTurn]:
    modes = [
        "system_design",
        "scenario",
        "project_deep_dive",
        "coding",
        "scenario",
        "follow_up",
        "follow_up",
    ]
    requirements = ["req_01", "req_02", "req_03", "req_04", "req_05", "req_06", "req_05"]
    turns = []
    for sequence_number, (mode, requirement_id) in enumerate(
        zip(modes, requirements),
        start=1,
    ):
        target_number = int(requirement_id[-2:])
        timestamp = datetime(
            2026,
            8,
            21,
            9,
            sequence_number,
            tzinfo=timezone.utc,
        )
        turns.append(
            InterviewTurn(
                id=f"turn_{sequence_number:02d}",
                sequence_number=sequence_number,
                target_id=f"target_{target_number:02d}",
                primary_requirement_id=requirement_id,
                question_mode=mode,
                question=f"请说明 {requirement_id} 的设计与取舍。",
                answer="候选人给出了结构化回答。",
                asked_at=timestamp,
                answered_at=timestamp,
            )
        )
    return turns


def _make_evidence() -> list[Evidence]:
    evidences = []
    for index in range(1, 5):
        claim_ids = ["claim_01"] if index == 1 else []
        evidences.append(
            Evidence(
                id=f"ev_{index:02d}",
                turn_id=f"turn_{index:02d}",
                requirement_ids=[f"req_{index:02d}"],
                related_claim_ids=claim_ids,
                polarity="supporting",
                strength="strong",
                observation=f"候选人清晰完成 req_{index:02d} 的结构化论证。",
                source_excerpt=f"req_{index:02d} 的设计、边界和验证路径。",
            )
        )
    evidences.extend(
        [
            Evidence(
                id="ev_05",
                turn_id="turn_05",
                requirement_ids=["req_05"],
                related_claim_ids=[],
                polarity="supporting",
                strength="strong",
                observation="候选人说明了可靠性与安全边界。",
                source_excerpt="我会设置重试、幂等、评测和人工接管。",
            ),
            Evidence(
                id="ev_05_limit",
                turn_id="turn_07",
                requirement_ids=["req_05"],
                related_claim_ids=["claim_02"],
                polarity="contradicting",
                strength="medium",
                observation="迁移场景中模型直接触发了高风险操作。",
                source_excerpt="让模型直接执行高风险操作。",
            ),
        ]
    )
    return evidences


def _quality() -> RubricQuality:
    return RubricQuality(
        correctness="strong",
        specificity="strong",
        reasoning="strong",
        tradeoff_awareness="strong",
        transferability="unverified",
    )


def _make_matches() -> RubricMatchBatch:
    matches = []
    for index in range(1, 5):
        prefix = f"d0{index}"
        matches.append(
            RubricMatch(
                evidence_id=f"ev_{index:02d}",
                requirement_id=f"req_{index:02d}",
                matched_minimum_criteria=[f"{prefix}_min_01", f"{prefix}_min_02"],
                matched_excellence_signals=[f"{prefix}_exc_01"],
                quality=_quality(),
            )
        )
    matches.append(
        RubricMatch(
            evidence_id="ev_05",
            requirement_id="req_05",
            matched_minimum_criteria=["d05_min_01", "d05_min_02"],
            matched_excellence_signals=["d05_exc_01"],
            quality=_quality(),
        )
    )
    matches.append(
        RubricMatch(
            evidence_id="ev_05_limit",
            requirement_id="req_05",
            matched_critical_errors=["d05_err_01"],
            quality=RubricQuality(
                correctness="medium",
                specificity="medium",
                reasoning="medium",
                tradeoff_awareness="medium",
                transferability="unverified",
            ),
        )
    )
    return RubricMatchBatch(matches=matches)


def _make_blueprint() -> ScoringBlueprint:
    return ScoringBlueprint(
        role_family="ai_application_engineering",
        role_profile_version="2026-H2",
        bindings=[
            RequirementScoringBinding(
                requirement_id=f"req_{index:02d}",
                primary_dimension_id=f"role_dim_{index:02d}",
                weight_within_dimension=1.0,
                rubric_id=f"role_dim_{index:02d}",
            )
            for index in range(1, 7)
        ],
    )


def _make_claim_registry() -> ClaimRegistry:
    return ClaimRegistry(
        claims=[
            ClaimItem(
                id="claim_01",
                text="能够设计 Agent Workflow",
                source_section="project",
                claim_type="experience",
            ),
            ClaimItem(
                id="claim_02",
                text="具备可靠性与安全设计经验",
                source_section="project",
                claim_type="experience",
            ),
        ]
    )


def _make_services(*, writer=None, score_engine=None) -> FakeSemanticServices:
    return FakeSemanticServices(writer=writer, score_engine=score_engine)


def _generate(
    services: FakeSemanticServices,
    *,
    runtime: InterviewRuntimeState | None = None,
    role_family: str = "ai_application_engineering",
    scoring_blueprint: ScoringBlueprint | None = None,
    blueprint_builder=None,
    candidate_id: str = "未提供",
    resume_profile: ResumeProfile | None = None,
    job_profile: JobProfile | None = None,
    evidences: list[Evidence] | None = None,
):
    from profile_agent.services.assessment_report_service import (
        generate_assessment_report,
    )

    plan = _make_plan()
    kwargs = {}
    if scoring_blueprint is not None:
        kwargs["scoring_blueprint"] = scoring_blueprint
    if blueprint_builder is not None:
        kwargs["blueprint_builder"] = blueprint_builder
    return generate_assessment_report(
        target_role="AI Agent / AI应用工程师",
        plan=plan,
        runtime_state=runtime or _make_runtime(plan),
        turns=_make_turns(),
        evidences=list(reversed(evidences or _make_evidence())),
        claim_registry=_make_claim_registry(),
        role_family=role_family,
        role_profile_version="2026-H2",
        semantic_services=services.as_mapping(),
        candidate_id=candidate_id,
        resume_profile=resume_profile,
        job_profile=job_profile,
        **kwargs,
    )


class AssessmentReportServiceTest(unittest.TestCase):
    def test_enterprise_assembly_preserves_candidate_context_and_decision(self) -> None:
        from profile_agent.services.score_engine_service import (
            calculate_score_snapshot,
        )

        def conditional_score_engine(profile, blueprint, assessments, claims):
            snapshot = calculate_score_snapshot(
                profile,
                blueprint,
                assessments,
                claims,
            )
            return snapshot.model_copy(
                update={
                    "job_match": snapshot.job_match.model_copy(
                        update={"limiting_reasons": []}
                    )
                }
            )

        resume_profile = ResumeProfile(
            education=["本科：计算机科学与技术"],
            skills=["Python", "Agent"],
        )
        job_profile = JobProfile(
            role="AI 应用工程师",
            responsibilities=["建设 Agent 应用"],
            requirements=[
                JobRequirement(name="Agent Workflow", description="状态与工具边界"),
                JobRequirement(name="RAG", description="检索增强生成"),
            ],
        )

        report = _generate(
            _make_services(score_engine=conditional_score_engine),
            candidate_id="ast_001",
            resume_profile=resume_profile,
            job_profile=job_profile,
        )

        self.assertEqual(report.candidate_overview.candidate_id, "ast_001")
        self.assertIn("本科", report.candidate_overview.education_summary or "")
        self.assertTrue(report.candidate_overview.jd_focus)
        self.assertEqual(
            report.candidate_overview.interview_rounds,
            len(_make_turns()),
        )
        self.assertEqual(
            report.enterprise_assessment.decision,
            "CONDITIONAL_PROCEED",
        )

    def test_enterprise_writer_failure_uses_dimension_fallback_and_guard(self) -> None:
        from profile_agent.services.score_engine_service import (
            calculate_score_snapshot,
        )

        def conditional_score_engine(profile, blueprint, assessments, claims):
            snapshot = calculate_score_snapshot(
                profile,
                blueprint,
                assessments,
                claims,
            )
            return snapshot.model_copy(
                update={
                    "job_match": snapshot.job_match.model_copy(
                        update={"limiting_reasons": []}
                    )
                }
            )

        def fail_writer(snapshot, evidences, role_profile):
            raise RuntimeError("narrative writer offline")

        report = _generate(
            _make_services(
                writer=fail_writer,
                score_engine=conditional_score_engine,
            ),
        )

        self.assertTrue(report.enterprise_assessment.overall_assessment)
        self.assertLessEqual(len(report.enterprise_assessment.reinterview_plan), 3)
        self.assertEqual(
            report.enterprise_assessment.decision,
            "CONDITIONAL_PROCEED",
        )

    def test_supplied_blueprint_is_reused_without_builder_and_reaches_matcher(self) -> None:
        services = _make_services()
        frozen = _make_blueprint()

        def fail_builder(*args, **kwargs):
            raise AssertionError("a supplied blueprint must skip the builder")

        report = _generate(
            services,
            scoring_blueprint=frozen,
            blueprint_builder=fail_builder,
        )

        self.assertIsNotNone(report)
        self.assertEqual(services.calls, ["matches", "writer"])
        self.assertEqual(len(services.received_blueprints), 1)
        self.assertEqual(
            services.received_blueprints[0].model_dump(),
            frozen.model_dump(),
        )

    def test_missing_blueprint_invokes_builder_once(self) -> None:
        services = _make_services()

        _generate(services)

        self.assertEqual(services.calls.count("blueprint"), 1)

    def test_complete_pipeline_produces_explainable_report(self) -> None:
        services = _make_services()

        report = _generate(services)

        self.assertEqual(services.calls, ["blueprint", "matches", "writer"])
        self.assertEqual(report.target_role, "AI Agent / AI应用工程师")
        self.assertEqual(
            [item.dimension_id for item in report.score_snapshot.radar_dimensions],
            [f"role_dim_{index:02d}" for index in range(1, 7)],
        )
        self.assertTrue(report.score_snapshot.job_match.published)
        self.assertEqual(
            {item.claim_id for item in report.score_snapshot.claim_verifications},
            {"claim_01", "claim_02"},
        )
        self.assertTrue(report.interview_path)
        self.assertTrue(report.narrative.strengths)
        self.assertTrue(report.narrative.risks)
        self.assertTrue(report.narrative.development_actions)

    def test_unverified_dimension_is_unscored_and_gray_ready(self) -> None:
        report = _generate(_make_services())

        radar = report.score_snapshot.radar_dimensions[-1]

        self.assertEqual(radar.dimension_id, "role_dim_06")
        self.assertEqual(radar.level, "UNVERIFIED")
        self.assertIsNone(radar.score)
        self.assertEqual(radar.coverage, 0.0)
        self.assertTrue(any(reason.kind == "unverified" for reason in radar.score_reasons))

    def test_each_scored_radar_dimension_has_two_reasons(self) -> None:
        report = _generate(_make_services())

        for radar in report.score_snapshot.radar_dimensions:
            if radar.level == "UNVERIFIED":
                continue
            with self.subTest(dimension=radar.dimension_id):
                self.assertGreaterEqual(len(radar.score_reasons), 2)
                for reason in radar.score_reasons:
                    self.assertIn(
                        reason.kind,
                        {"strength", "risk", "critical_error", "unverified"},
                    )

    def test_interview_path_uses_turn_order_and_evidence_links(self) -> None:
        report = _generate(_make_services())

        self.assertEqual(
            [step.turn_id for step in report.interview_path],
            [f"turn_{index:02d}" for index in range(1, 8)],
        )
        evidence_by_turn = {
            evidence.turn_id: evidence.id for evidence in _make_evidence()
        }
        for step in report.interview_path:
            expected_evidence_ids = {
                evidence_by_turn[step.turn_id]
            } if step.turn_id in evidence_by_turn else set()
            self.assertTrue(
                set(step.evidence_ids).issubset(expected_evidence_ids)
            )

    def test_writer_failure_uses_fallback_without_losing_scores(self) -> None:
        baseline = _generate(_make_services())

        def fail_writer(snapshot, evidences, role_profile):
            raise RuntimeError("narrative writer offline")

        failing_services = _make_services(writer=fail_writer)
        recovered = _generate(failing_services)

        self.assertEqual(
            recovered.score_snapshot.model_dump(),
            baseline.score_snapshot.model_dump(),
        )
        self.assertTrue(recovered.narrative.strengths)
        self.assertTrue(recovered.narrative.risks)
        self.assertEqual(
            failing_services.calls,
            ["blueprint", "matches", "writer"],
        )

    def test_unfinished_runtime_is_rejected(self) -> None:
        plan = _make_plan()
        runtime = _make_runtime(plan, stopped=False)
        services = _make_services()

        from profile_agent.services.assessment_report_service import (
            AssessmentReportStateError,
        )

        with self.assertRaises(AssessmentReportStateError):
            _generate(services, runtime=runtime)
        self.assertEqual(services.calls, [])

    def test_same_structured_inputs_produce_identical_score_snapshot(self) -> None:
        first = _generate(_make_services())
        second = _generate(_make_services())

        self.assertEqual(
            first.score_snapshot.model_dump(),
            second.score_snapshot.model_dump(),
        )

    def test_scoring_error_is_not_swallowed_as_narrative_fallback(self) -> None:
        def fail_score_engine(*args, **kwargs):
            raise RuntimeError("score engine failure")

        services = _make_services(score_engine=fail_score_engine)

        with self.assertRaisesRegex(RuntimeError, "score engine failure"):
            _generate(services)
        self.assertEqual(services.calls, ["blueprint", "matches"])

    def test_unknown_role_pack_error_is_not_swallowed(self) -> None:
        services = _make_services()

        with self.assertRaisesRegex(ValueError, "missing_role/2026-H2"):
            _generate(services, role_family="missing_role")
        self.assertEqual(services.calls, [])


if __name__ == "__main__":
    unittest.main()
