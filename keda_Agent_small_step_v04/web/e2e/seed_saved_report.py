"""Seed one deterministic saved report for the Playwright contract suite.

This is a test fixture, not a production endpoint.  It uses the same public
report assembly path as a completed assessment, while avoiding a provider
call so the E2E suite stays deterministic and offline.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from profile_agent.calibration.offline_runner import run_offline_calibration_case
from profile_agent.calibration.report_cases import build_public_student_showcase_case
from profile_agent.graphs.interview import build_interview_graph
from profile_agent.web.repository import SqliteAssessmentRepository
from profile_agent.web.schemas import AssessmentRecord, AssessmentStatus


ASSESSMENT_ID = "ast_e2e_report"


def main() -> None:
    database_path = Path(os.environ["WEB_DATABASE_PATH"])
    checkpoint_path = Path(os.environ["WEB_CHECKPOINT_PATH"])
    case = build_public_student_showcase_case()
    run = run_offline_calibration_case(case)

    repository = SqliteAssessmentRepository(database_path)
    try:
        record = AssessmentRecord.new(
            assessment_id=ASSESSMENT_ID,
            target_role=case.target_role,
            jd_text="Agent Workflow",
            resume_text="candidate",
        ).model_copy(
            update={
                "status": AssessmentStatus.COMPLETE,
                "report": run.report.model_dump(mode="json"),
                "final_plan": case.plan.model_dump(mode="json"),
            }
        )
        repository.create(record)
    finally:
        repository.close()

    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    try:
        saver = SqliteSaver(connection)
        saver.setup()
        graph = build_interview_graph(checkpointer=saver)
        graph.update_state(
            {"configurable": {"thread_id": ASSESSMENT_ID}},
            {
                "interview_plan": case.plan.model_dump(mode="json"),
                "interview_turns": [
                    item.model_dump(mode="json") for item in case.turns
                ],
                "evidences": [
                    item.model_dump(mode="json") for item in case.evidences
                ],
            },
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
