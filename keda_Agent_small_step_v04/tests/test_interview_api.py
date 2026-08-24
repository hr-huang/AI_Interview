import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import RLock

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from profile_agent.graphs.interview import build_interview_graph
from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
    GeneratedQuestion,
    InterviewPlan,
)
from profile_agent.schemas.report_schema import ScoringBlueprint
from profile_agent.schemas.runtime_schema import (
    AnswerProcessingResult,
    Evidence,
    InterviewTurn,
)
from profile_agent.services.runtime_state_service import (
    record_requirement_evidence,
)
from profile_agent.web.app import create_app
from profile_agent.web.container import WebContainer
from profile_agent.web.interview_service import InterviewService
from profile_agent.web.repository import SqliteAssessmentRepository
from profile_agent.web.schemas import AssessmentRecord, AssessmentStatus
from tests.report_test_helpers import make_test_report


class InlineDispatcher:
    def submit(self, function, *args) -> None:
        function(*args)

    def close(self) -> None:
        return None


class TrackingLock:
    def __init__(self) -> None:
        self._lock = RLock()
        self.enter_count = 0

    def __enter__(self):
        self.enter_count += 1
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._lock.release()


class CountingGraph:
    def __init__(self, graph) -> None:
        self.graph = graph
        self.invoke_count = 0
        self.resume_count = 0

    def invoke(self, value, config):
        if isinstance(value, Command):
            self.resume_count += 1
        else:
            self.invoke_count += 1
        return self.graph.invoke(value, config)

    def get_state(self, config):
        return self.graph.get_state(config)


class InterviewApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.repository = SqliteAssessmentRepository(root / "web.db")
        self.checkpoint_connection = sqlite3.connect(
            root / "checkpoints.db",
            check_same_thread=False,
        )
        self.graph = self._build_graph(answer_status="in_progress")
        self.token = "candidate-token"
        self.assessment_id = "ast_interview"
        self._create_ready_assessment()
        self.container = WebContainer.for_test(
            repository=self.repository,
            pre_interview_graph=object(),
            dispatcher=InlineDispatcher(),
            interview_graph=self.graph,
            checkpoint_connection=self.checkpoint_connection,
        )
        self.client = TestClient(create_app(self.container))

    def tearDown(self) -> None:
        self.client.close()
        self.checkpoint_connection.close()
        self.repository.close()
        self.temp_dir.cleanup()

    @staticmethod
    def _plan() -> InterviewPlan:
        return InterviewPlan(
            duration_minutes=30,
            max_questions=3,
            closing_buffer_minutes=0,
            targets=[
                AssessmentTarget(
                    id="target_a",
                    objective="验证候选人的恢复能力",
                    target_type="problem_solving",
                    competency_ids=[],
                    evidence_requirements=[
                        EvidenceRequirement(
                            id="requirement_a",
                            description="能够解释失败恢复和幂等",
                        )
                    ],
                    related_claim_ids=[],
                    priority="high",
                    must_cover=True,
                    time_budget_minutes=10,
                    preferred_modes=["scenario"],
                )
            ],
        )

    def _build_graph(
        self,
        *,
        answer_status: str,
        checkpoint_connection: sqlite3.Connection | None = None,
    ) -> CountingGraph:
        def question_generator(**_kwargs) -> GeneratedQuestion:
            return GeneratedQuestion(text="请说明你的失败恢复方案")

        def answer_processor(
            plan,
            runtime_state,
            turn: InterviewTurn,
            existing_evidences,
            claim_registry=None,
        ) -> AnswerProcessingResult:
            evidence = Evidence(
                id=f"evidence_{len(existing_evidences) + 1:03d}",
                turn_id=turn.id,
                requirement_ids=["requirement_a"],
                polarity="supporting",
                strength="strong",
                observation="候选人回答了恢复方案",
                source_excerpt=turn.answer or "",
            )
            updated_runtime = record_requirement_evidence(
                runtime_state,
                requirement_id="requirement_a",
                status=answer_status,
                supporting_evidence_ids=[evidence.id],
                contradicting_evidence_ids=[],
                known_evidence_ids={evidence.id},
            )
            return AnswerProcessingResult(
                new_evidences=[evidence],
                runtime_state=updated_runtime,
            )

        graph = build_interview_graph(
            question_generator=question_generator,
            answer_processor=answer_processor,
            report_generator=lambda **kwargs: make_test_report(
                kwargs.get("target_role") or "AI 应用工程师"
            ),
            now_provider=lambda: __import__(
                "datetime"
            ).datetime.now(__import__("datetime").timezone.utc),
            checkpointer=SqliteSaver(
                checkpoint_connection or self.checkpoint_connection
            ),
        )
        return CountingGraph(graph)

    def _create_ready_assessment(self) -> None:
        plan = self._plan()
        blueprint = ScoringBlueprint(
            role_family="ai_application_engineering",
            role_profile_version="2026-H2",
            bindings=[],
        )
        record = AssessmentRecord.new(
            assessment_id=self.assessment_id,
            target_role="AI 应用工程师",
            jd_text="Agent Workflow",
            resume_text="候选人有 checkpoint 经验",
            interview_duration_minutes=30,
        ).model_copy(
            update={
                "status": AssessmentStatus.READY,
                "pre_interview_state": {
                    "target_role": "AI 应用工程师",
                    "claim_registry": ClaimRegistry().model_dump(mode="json"),
                },
                "original_plan": plan.model_dump(mode="json"),
                "final_plan": plan.model_dump(mode="json"),
                "scoring_blueprint": blueprint.model_dump(mode="json"),
                "candidate_token_hash": hashlib.sha256(
                    self.token.encode("utf-8")
                ).hexdigest(),
            }
        )
        self.repository.create(record)

    def test_get_does_not_start_and_duplicate_answer_is_idempotent(self) -> None:
        ready = self.client.get(f"/api/interviews/{self.token}")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["state"], "ready")
        self.assertEqual(self.graph.invoke_count, 0)

        started = self.client.post(f"/api/interviews/{self.token}/start")
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["state"], "waiting_for_answer")
        turn_id = started.json()["turn"]["id"]

        payload = {
            "turn_id": turn_id,
            "answer": "我会使用 checkpoint 与幂等键",
            "idempotency_key": "answer_1",
        }
        first = self.client.post(
            f"/api/interviews/{self.token}/answers",
            json=payload,
        )
        duplicate = self.client.post(
            f"/api/interviews/{self.token}/answers",
            json=payload,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), duplicate.json())
        self.assertEqual(self.graph.resume_count, 1)

    def test_get_checkpoint_read_uses_interview_lock(self) -> None:
        started = self.client.post(f"/api/interviews/{self.token}/start")
        self.assertEqual(started.status_code, 200)
        tracking_lock = TrackingLock()
        self.container.interview_lock = tracking_lock

        response = self.client.get(f"/api/interviews/{self.token}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(tracking_lock.enter_count, 1)

    def test_stale_turn_is_rejected_without_exposing_internal_fields(self) -> None:
        started = self.client.post(f"/api/interviews/{self.token}/start")
        response = self.client.post(
            f"/api/interviews/{self.token}/answers",
            json={
                "turn_id": "turn_stale",
                "answer": "回答",
                "idempotency_key": "stale_1",
            },
        )

        self.assertEqual(started.status_code, 200)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.graph.resume_count, 0)
        for forbidden in (
            "evidences",
            "score_snapshot",
            "next_action",
            "claim_registry",
            "assessment_report",
        ):
            self.assertNotIn(forbidden, response.text)

    def test_finish_persists_report_but_candidate_only_sees_completion(self) -> None:
        self.graph = self._build_graph(answer_status="sufficient")
        self.container.interview_graph = self.graph
        started = self.client.post(f"/api/interviews/{self.token}/start")
        turn_id = started.json()["turn"]["id"]
        response = self.client.post(
            f"/api/interviews/{self.token}/answers",
            json={
                "turn_id": turn_id,
                "answer": "完成恢复方案",
                "idempotency_key": "finish_1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "complete")
        self.assertEqual(
            self.repository.get(self.assessment_id).status,
            AssessmentStatus.COMPLETE,
        )
        self.assertIsNotNone(self.repository.get(self.assessment_id).report)
        for forbidden in ("assessment_report", "score_snapshot", "evidence"):
            self.assertNotIn(forbidden, response.text)

    def test_checkpoint_restart_recovers_current_turn_without_new_question(self) -> None:
        started = self.client.post(f"/api/interviews/{self.token}/start")
        current_turn_id = started.json()["turn"]["id"]
        self.client.close()
        self.checkpoint_connection.close()
        self.repository.close()

        root = Path(self.temp_dir.name)
        repository = SqliteAssessmentRepository(root / "web.db")
        connection = sqlite3.connect(
            root / "checkpoints.db",
            check_same_thread=False,
        )
        graph = self._build_graph(
            answer_status="in_progress",
            checkpoint_connection=connection,
        )
        container = WebContainer.for_test(
            repository=repository,
            pre_interview_graph=object(),
            dispatcher=InlineDispatcher(),
            interview_graph=graph,
            checkpoint_connection=connection,
        )
        client = TestClient(create_app(container))
        try:
            recovered = client.get(f"/api/interviews/{self.token}")
            self.assertEqual(recovered.status_code, 200)
            self.assertEqual(recovered.json()["state"], "waiting_for_answer")
            self.assertEqual(
                recovered.json()["turn"]["id"],
                current_turn_id,
            )
            self.assertEqual(graph.invoke_count, 0)
        finally:
            client.close()
            connection.close()
            repository.close()

    def test_ready_checkpoint_is_repaired_without_invoking_start_again(self) -> None:
        service = InterviewService(self.container)
        record = self.repository.get(self.assessment_id)

        # Simulate a process dying after LangGraph checkpointed the first
        # question but before READY -> IN_PROGRESS reached the repository.
        self.graph.invoke(
            service._deserialize_frozen_state(record),
            service._config(record.id),
        )
        self.assertEqual(self.repository.get(self.assessment_id).status, AssessmentStatus.READY)
        invoke_count_before = self.graph.invoke_count

        recovered = self.client.post(f"/api/interviews/{self.token}/start")

        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["state"], "waiting_for_answer")
        self.assertEqual(self.graph.invoke_count, invoke_count_before)
        self.assertEqual(
            self.repository.get(self.assessment_id).status,
            AssessmentStatus.IN_PROGRESS,
        )

    def test_get_repairs_ready_checkpoint_without_invoking_start_again(self) -> None:
        service = InterviewService(self.container)
        record = self.repository.get(self.assessment_id)
        self.graph.invoke(
            service._deserialize_frozen_state(record),
            service._config(record.id),
        )
        invoke_count_before = self.graph.invoke_count

        recovered = self.client.get(f"/api/interviews/{self.token}")

        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["state"], "waiting_for_answer")
        self.assertEqual(recovered.json()["turn"]["id"], "turn_001")
        self.assertEqual(self.graph.invoke_count, invoke_count_before)
        self.assertEqual(
            self.repository.get(self.assessment_id).status,
            AssessmentStatus.IN_PROGRESS,
        )

    def test_terminal_checkpoint_is_completed_without_resuming_again(self) -> None:
        self.graph = self._build_graph(answer_status="sufficient")
        self.container.interview_graph = self.graph
        started = self.client.post(f"/api/interviews/{self.token}/start")
        turn_id = started.json()["turn"]["id"]
        service = InterviewService(self.container)

        # Simulate a process dying after resume committed the terminal report
        # to LangGraph, but before repository/report/idempotency persistence.
        self.graph.invoke(
            Command(resume="完成恢复方案"),
            service._config(self.assessment_id),
        )
        self.assertEqual(
            self.repository.get(self.assessment_id).status,
            AssessmentStatus.IN_PROGRESS,
        )
        resume_count_before = self.graph.resume_count

        recovered = self.client.post(
            f"/api/interviews/{self.token}/answers",
            json={
                "turn_id": turn_id,
                "answer": "完成恢复方案",
                "idempotency_key": "terminal-recovery-1",
            },
        )

        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json()["state"], "complete")
        self.assertEqual(self.graph.resume_count, resume_count_before)
        self.assertEqual(
            self.repository.get(self.assessment_id).status,
            AssessmentStatus.COMPLETE,
        )


if __name__ == "__main__":
    unittest.main()
