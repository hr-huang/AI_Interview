import unittest

from profile_agent.calibration.report_cases import get_report_calibration_case
from profile_agent.calibration.offline_runner import run_offline_calibration_case
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
        self.assertEqual(first.requirement_id, "req_01")
        self.assertTrue(first.requirement_label)
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
        self.assertEqual(last.evidence_ids, [])
        self.assertEqual(last.evidence_status, "none")

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
            if "ev_C03_002" in reason.evidence_ids
        ]
        self.assertTrue(limiting)
        self.assertIn("受监管", limiting[0].sources[0].answer)
        self.assertIn("迁移", limiting[0].sources[0].question)

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


if __name__ == "__main__":
    unittest.main()
