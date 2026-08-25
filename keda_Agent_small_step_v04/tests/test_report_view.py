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
        view = build_report_view(
            run.report,
            case.plan,
            case.turns,
            case.evidences,
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
        self.assertEqual(candidate_overview["candidate_id"], "未提供")

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
            [item.dimension_id for item in view.radar_dimensions],
            [item.id for item in profile.dimensions],
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
