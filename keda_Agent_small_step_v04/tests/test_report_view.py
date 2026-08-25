import json
import unittest

from profile_agent.calibration.report_cases import (
    build_public_student_showcase_case,
    get_report_calibration_case,
)
from profile_agent.calibration.offline_runner import run_offline_calibration_case
from profile_agent.schemas.report_schema import EvidenceExcerpt
from profile_agent.services.role_profile_service import load_role_profile
from profile_agent.web.report_view import build_report_view


class ReportViewTest(unittest.TestCase):
    def test_projects_interview_transcript_in_turn_order(self) -> None:
        case = get_report_calibration_case("C01")
        run = run_offline_calibration_case(case)
        view = build_report_view(
            run.report,
            case.plan,
            case.turns,
            case.evidences,
            load_role_profile("ai_application_engineering", "2026-H2"),
            demo=True,
        )

        self.assertEqual(len(view.interview_transcript), 6)
        first = view.interview_transcript[0]
        self.assertEqual(first.sequence_number, 1)
        self.assertEqual(first.question, case.turns[0].question)
        self.assertEqual(first.answer, case.turns[0].answer)
        self.assertTrue(first.requirement_label)
        self.assertEqual(getattr(first, "evidence_cta", None), "查看本轮依据")
        self.assertEqual(first.evidence_status, "supporting")

    def test_transcript_turn_without_evidence_is_explicitly_uncovered(self) -> None:
        case = get_report_calibration_case("C03")
        run = run_offline_calibration_case(case)
        extra_turn = case.turns[-1].model_copy(
            update={
                "id": "turn_C03_003",
                "sequence_number": 3,
                "question": "补充一个尚未回答的问题。",
                "answer": None,
                "answered_at": None,
            }
        )
        view = build_report_view(
            run.report,
            case.plan,
            [*case.turns, extra_turn],
            case.evidences,
            load_role_profile("ai_application_engineering", "2026-H2"),
            demo=True,
        )

        last = view.interview_transcript[-1]
        self.assertEqual(last.turn_id, extra_turn.id)
        self.assertEqual(last.evidence_status, "none")
        self.assertEqual(getattr(last, "evidence_cta", None), "查看本轮依据")

    def test_reason_links_to_question_answer_and_evidence(self) -> None:
        case = get_report_calibration_case("C03")
        run = run_offline_calibration_case(case)
        evidences = [
            evidence.model_copy(
                update={"source_excerpt": "迁移到受监管的理赔场景"}
            )
            if evidence.id == "ev_C03_002"
            else evidence
            for evidence in case.evidences
        ]
        view = build_report_view(
            run.report,
            case.plan,
            case.turns,
            evidences,
            load_role_profile("ai_application_engineering", "2026-H2"),
            demo=True,
        )
        limiting = [
            reason
            for dimension in view.radar_dimensions
            for reason in dimension.reasons
            if reason.sources and reason.sources[0].turn_id == case.turns[1].id
        ]
        self.assertTrue(limiting)
        self.assertIn("受监管", getattr(limiting[0].sources[0], "quote", ""))
        self.assertIn("依据", getattr(limiting[0].sources[0], "interpretation", ""))

    def test_public_projection_contains_safe_enterprise_report(self) -> None:
        case = build_public_student_showcase_case()
        run = run_offline_calibration_case(case)
        report = run.report.model_copy(deep=True)
        report.enterprise_assessment = report.enterprise_assessment.model_copy(
            update={
                "evidence_excerpts": [
                    EvidenceExcerpt(
                        evidence_id="ev_DEMO_STUDENT_001",
                        turn_id=case.turns[0].id,
                        conclusion="回答支持当前能力判断。",
                        quote="我把流程改为简历解析、岗位检索、证据匹配和人工确认四个节点",
                        interpretation="能够说明流程拆分与人工介入边界。",
                        limitation="迁移到更大规模场景仍需复核。",
                    )
                ]
            }
        )
        view = build_report_view(
            report,
            case.plan,
            case.turns,
            case.evidences,
            load_role_profile("ai_application_engineering", "2026-H2"),
            demo=True,
        )

        payload = view.model_dump(mode="json")
        enterprise = payload.get("enterprise_assessment")
        self.assertIsNotNone(enterprise)
        candidate_overview = payload.get("candidate_overview")
        self.assertIsNotNone(candidate_overview)
        self.assertEqual(
            enterprise["decision"],
            "CONDITIONAL_PROCEED",
        )
        self.assertLessEqual(
            len(enterprise["reinterview_plan"]),
            3,
        )
        self.assertTrue(enterprise["overall_assessment"])
        self.assertNotIn("candidate_id", candidate_overview)

        serialized = json.dumps(payload, ensure_ascii=False)
        for token in ("RubricMatch", "Requirement", "d03_min_02", "ev_DEMO_STUDENT"):
            self.assertNotIn(token, serialized)
        public_keys = {
            key
            for item in _walk_dicts(payload)
            for key in item
        }
        self.assertNotIn("evidence_id", public_keys)
        self.assertNotIn("evidence_ids", public_keys)
        self.assertNotIn("rubric_signal_ids", public_keys)
        self.assertNotIn("requirement_id", public_keys)
        self.assertNotIn("claim_id", public_keys)
        self.assertNotIn("dimension_id", public_keys)
        self.assertNotIn("dimension_ids", public_keys)
        self.assertNotIn("role_dim_", serialized)

        transcript_answers = {
            item["turn_id"]: item["answer"] or ""
            for item in payload["interview_transcript"]
        }
        excerpts = enterprise["evidence_excerpts"]
        self.assertTrue(excerpts)
        for excerpt in excerpts:
            self.assertIn(excerpt["quote"], transcript_answers[excerpt["turn_id"]])
            self.assertEqual(
                set(excerpt),
                {"turn_id", "conclusion", "quote", "interpretation", "limitation"},
            )

    def test_enterprise_excerpt_requires_existing_evidence_and_matching_turn(self) -> None:
        case = build_public_student_showcase_case()
        run = run_offline_calibration_case(case)
        for update, expected in (
            ({"evidence_id": "missing-evidence"}, "missing-evidence"),
            (
                {"turn_id": case.turns[1].id},
                "Enterprise evidence excerpt",
            ),
        ):
            with self.subTest(update=update):
                excerpt = EvidenceExcerpt(
                    evidence_id=case.evidences[0].id,
                    turn_id=case.turns[0].id,
                    conclusion="回答支持当前能力判断。",
                    quote="课程团队最初使用单 Agent",
                    interpretation="能够说明流程拆分与人工介入边界。",
                    limitation="迁移到更大规模场景仍需复核。",
                ).model_copy(update=update)
                report = run.report.model_copy(deep=True)
                report.enterprise_assessment = (
                    report.enterprise_assessment.model_copy(
                        update={"evidence_excerpts": [excerpt]}
                    )
                )
                with self.assertRaisesRegex(ValueError, expected):
                    build_report_view(
                        report,
                        case.plan,
                        case.turns,
                        case.evidences,
                        load_role_profile(
                            "ai_application_engineering", "2026-H2"
                        ),
                        demo=True,
                    )

    def test_enterprise_excerpt_requires_raw_non_whitespace_quote_in_answer(self) -> None:
        case = build_public_student_showcase_case()
        run = run_offline_calibration_case(case)
        excerpt = EvidenceExcerpt(
            evidence_id=case.evidences[0].id,
            turn_id=case.turns[0].id,
            conclusion="回答支持当前能力判断。",
            quote="  课程团队最初使用单 Agent  ",
            interpretation="能够说明流程拆分与人工介入边界。",
            limitation="迁移到更大规模场景仍需复核。",
        )
        report = run.report.model_copy(deep=True)
        report.enterprise_assessment = report.enterprise_assessment.model_copy(
            update={"evidence_excerpts": [excerpt]}
        )
        with self.assertRaisesRegex(ValueError, "quote"):
            build_report_view(
                report,
                case.plan,
                case.turns,
                case.evidences,
                load_role_profile("ai_application_engineering", "2026-H2"),
                demo=True,
            )

    def test_full_answer_excerpt_is_omitted_but_transcript_stays_raw(self) -> None:
        case = get_report_calibration_case("C03")
        run = run_offline_calibration_case(case)
        full_answer = case.turns[0].answer
        evidence = case.evidences[0]
        excerpt = EvidenceExcerpt(
            evidence_id=evidence.id,
            turn_id=case.turns[0].id,
            conclusion="回答支持当前能力判断。",
            quote=full_answer,
            interpretation="能够说明流程拆分与人工介入边界。",
            limitation="迁移到更大规模场景仍需复核。",
        )
        report = run.report.model_copy(deep=True)
        report.enterprise_assessment = report.enterprise_assessment.model_copy(
            update={"evidence_excerpts": [excerpt]}
        )
        view = build_report_view(
            report,
            case.plan,
            case.turns,
            case.evidences,
            load_role_profile("ai_application_engineering", "2026-H2"),
            demo=True,
        )
        payload = view.model_dump(mode="json")
        self.assertEqual(payload["interview_transcript"][0]["answer"], full_answer)
        self.assertEqual(payload["enterprise_assessment"]["evidence_excerpts"], [])
        self.assertNotIn(full_answer, json.dumps(payload["enterprise_assessment"], ensure_ascii=False))

    def test_short_unpunctuated_full_answer_is_not_copied_to_source(self) -> None:
        case = get_report_calibration_case("C03")
        run = run_offline_calibration_case(case)
        turns = [
            turn.model_copy(update={"answer": "E3 visa"})
            if turn.id == case.turns[0].id
            else turn
            for turn in case.turns
        ]
        evidences = [
            evidence.model_copy(update={"source_excerpt": "E3 visa"})
            if evidence.id == case.evidences[0].id
            else evidence
            for evidence in case.evidences
        ]
        report = run.report.model_copy(deep=True)
        report.enterprise_assessment = report.enterprise_assessment.model_copy(
            update={
                "evidence_excerpts": [
                    EvidenceExcerpt(
                        evidence_id=case.evidences[0].id,
                        turn_id=case.turns[0].id,
                        conclusion="回答支持当前能力判断。",
                        quote="E3 visa",
                        interpretation="能够说明原始回答保持不变。",
                        limitation="迁移能力仍需独立验证。",
                    )
                ]
            }
        )
        view = build_report_view(
            report,
            case.plan,
            turns,
            evidences,
            load_role_profile("ai_application_engineering", "2026-H2"),
            demo=True,
        )
        payload = view.model_dump(mode="json")
        self.assertEqual(payload["interview_transcript"][0]["answer"], "E3 visa")
        self.assertEqual(payload["enterprise_assessment"]["evidence_excerpts"], [])
        self.assertNotIn("E3 visa", json.dumps(payload["enterprise_assessment"], ensure_ascii=False))

    def test_server_copy_rejects_untranslated_internal_token_without_broad_e_deletion(self) -> None:
        case = build_public_student_showcase_case()
        run = run_offline_calibration_case(case)
        report = run.report.model_copy(deep=True)
        report.enterprise_assessment = report.enterprise_assessment.model_copy(
            update={"overall_assessment": "错误文本 d03_min_02"}
        )
        with self.assertRaisesRegex(ValueError, "内部"):
            build_report_view(
                report,
                case.plan,
                case.turns,
                case.evidences,
                load_role_profile("ai_application_engineering", "2026-H2"),
                demo=True,
            )

        with self.assertRaisesRegex(ValueError, "Demo case description"):
            build_report_view(
                run.report,
                case.plan,
                case.turns,
                case.evidences,
                load_role_profile("ai_application_engineering", "2026-H2"),
                demo=True,
                demo_case_title="错误演示",
                demo_case_description="错误 d03_min_02",
            )

        report.enterprise_assessment = report.enterprise_assessment.model_copy(
            update={"overall_assessment": "E3 visa"}
        )
        view = build_report_view(
            report,
            case.plan,
            case.turns,
            case.evidences,
            load_role_profile("ai_application_engineering", "2026-H2"),
            demo=True,
        )
        self.assertEqual(view.enterprise_assessment.overall_assessment, "E3 visa")

        view = build_report_view(
            run.report,
            case.plan,
            case.turns,
            case.evidences,
            load_role_profile("ai_application_engineering", "2026-H2"),
            demo=True,
            demo_case_title="E3 visa",
            demo_case_description="E3 visa",
        )
        self.assertEqual(view.demo_case_title, "E3 visa")
        self.assertEqual(view.demo_case_description, "E3 visa")
    def test_unverified_dimension_keeps_score_none(self) -> None:
        case = get_report_calibration_case("C06")
        run = run_offline_calibration_case(case)
        view = build_report_view(
            run.report,
            case.plan,
            case.turns,
            case.evidences,
            load_role_profile("ai_application_engineering", "2026-H2"),
            demo=True,
        )
        self.assertTrue(
            any(
                item.level == "UNVERIFIED" and item.score is None
                for item in view.radar_dimensions
            )
        )

    def test_preserves_role_pack_dimension_order(self) -> None:
        case = get_report_calibration_case("C06")
        run = run_offline_calibration_case(case)
        profile = load_role_profile("ai_application_engineering", "2026-H2")
        view = build_report_view(
            run.report,
            case.plan,
            case.turns,
            case.evidences,
            profile,
            demo=True,
        )
        self.assertEqual(
            [item.name for item in view.radar_dimensions],
            [item.name for item in profile.dimensions],
        )

    def test_dangling_evidence_id_is_rejected(self) -> None:
        case = get_report_calibration_case("C03")
        run = run_offline_calibration_case(case)
        report = run.report.model_copy(deep=True)
        report.score_snapshot.radar_dimensions[0].score_reasons[0].evidence_ids = [
            "missing-evidence"
        ]
        with self.assertRaisesRegex(ValueError, "missing-evidence"):
            build_report_view(
                report,
                case.plan,
                case.turns,
                case.evidences,
                load_role_profile("ai_application_engineering", "2026-H2"),
                demo=True,
            )

    def test_source_excerpt_must_come_from_turn_answer(self) -> None:
        case = get_report_calibration_case("C03")
        run = run_offline_calibration_case(case)
        evidences = [
            evidence.model_copy(update={"source_excerpt": "伪造的回答摘录"})
            if evidence.id == "ev_C03_001"
            else evidence
            for evidence in case.evidences
        ]
        with self.assertRaisesRegex(ValueError, "source_excerpt"):
            build_report_view(
                run.report,
                case.plan,
                case.turns,
                evidences,
                load_role_profile("ai_application_engineering", "2026-H2"),
                demo=True,
            )


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


if __name__ == "__main__":
    unittest.main()
