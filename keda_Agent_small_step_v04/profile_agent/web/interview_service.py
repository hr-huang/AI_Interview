"""Candidate interview session service.

The LangGraph checkpoint is the source of truth for an in-progress interview.
The web repository stores only the immutable assessment inputs, the lifecycle
status/report, and the idempotent response envelope.  This module is the
privacy boundary between those two stores: graph state is never returned
directly to the candidate.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping, TYPE_CHECKING

from langgraph.types import Command
from pydantic import BaseModel, Field, field_validator

from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.schemas.report_schema import AssessmentReport, ScoringBlueprint
from profile_agent.schemas.runtime_schema import InterviewRuntimeState, InterviewTurn
from profile_agent.web.assessment_service import jsonable
from profile_agent.web.schemas import (
    AssessmentRecord,
    AssessmentStatus,
    transition_assessment,
)

if TYPE_CHECKING:
    from profile_agent.web.container import WebContainer


class AnswerRequest(BaseModel):
    """The only candidate-controlled fields accepted by the answer endpoint."""

    turn_id: str = Field(min_length=1, max_length=128)
    answer: str = Field(min_length=1, max_length=20_000)
    idempotency_key: str = Field(min_length=1, max_length=256)

    @field_validator("turn_id", "answer", "idempotency_key")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("字段不能为空")
        return clean


class StaleTurnError(ValueError):
    """Raised when an answer targets a turn other than the current one."""

    def __init__(
        self,
        current_turn_id: str | None,
        public_state: dict[str, Any] | None = None,
    ) -> None:
        self.current_turn_id = current_turn_id
        self.public_state = public_state
        super().__init__("当前问题已更新，请使用最新问题")


class InterviewStateError(ValueError):
    """Raised when a candidate attempts an operation in the wrong state."""


class InterviewService:
    """Read/start/resume a persisted interview without exposing graph state."""

    def __init__(self, container: WebContainer) -> None:
        self.container = container
        self.repository = container.repository

    @property
    def graph(self) -> object:
        graph = self.container.interview_graph
        if graph is None:
            raise RuntimeError("面试图尚未初始化")
        return graph

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _assessment_for_token(self, token: str) -> AssessmentRecord:
        clean_token = token.strip()
        if not clean_token:
            raise KeyError(token)
        return self.repository.get_by_candidate_token_hash(self._hash(clean_token))

    @staticmethod
    def _config(assessment_id: str) -> dict[str, dict[str, str]]:
        # The assessment ID is deliberately the sole LangGraph thread ID.
        return {"configurable": {"thread_id": assessment_id}}

    def _recover_ready_checkpoint(
        self,
        record: AssessmentRecord,
    ) -> dict[str, Any] | None:
        """Repair a graph-first start whose repository CAS was interrupted."""

        try:
            checkpoint_values, checkpoint_snapshot = self._read_graph_state(
                record.id
            )
        except (AttributeError, KeyError, RuntimeError):
            return None
        if not checkpoint_values.get("current_turn_id"):
            return None

        in_progress = transition_assessment(
            record,
            AssessmentStatus.IN_PROGRESS,
        )
        if not self.repository.save_if_version(
            in_progress,
            record.version,
        ):
            current = self.repository.get(record.id)
            return self._public_state_for_record(
                current,
                checkpoint_values,
                checkpoint_snapshot,
            )
        return self._public_state(
            in_progress,
            checkpoint_values,
            snapshot=checkpoint_snapshot,
        )

    def get_session(self, token: str) -> dict[str, Any]:
        # SqliteSaver shares one sqlite connection with answer/start.  Reads
        # must use the same lock as invokes to avoid concurrent connection use.
        with self.container.interview_lock:
            return self._get_session_unlocked(token)

    def _get_session_unlocked(self, token: str) -> dict[str, Any]:
        record = self._assessment_for_token(token)
        if record.status is AssessmentStatus.READY:
            recovered = self._recover_ready_checkpoint(record)
            if recovered is not None:
                return recovered
            return {"state": "ready", "target_role": record.target_role}
        if record.status is AssessmentStatus.COMPLETE:
            return {"state": "complete"}
        if record.status is AssessmentStatus.REPORTING:
            if record.report is not None:
                return {"state": "complete"}
            return {"state": "reporting"}
        if record.status is not AssessmentStatus.IN_PROGRESS:
            raise InterviewStateError(
                f"当前评估状态不支持候选人面试: {record.status.value}"
            )

        values, snapshot = self._read_graph_state(record.id)
        return self._public_state(record, values, snapshot=snapshot)

    def start(self, token: str) -> dict[str, Any]:
        # Start and answer are serialized so two browser requests cannot both
        # resume the same checkpoint before either idempotency row is written.
        with self.container.interview_lock:
            record = self._assessment_for_token(token)
            if record.status is AssessmentStatus.COMPLETE:
                return {"state": "complete"}
            if record.status is not AssessmentStatus.READY:
                return self._get_session_unlocked(token)

            # A process can die after the graph checkpoint commits its first
            # interrupt but before the repository status CAS.  Recover that
            # checkpoint instead of invoking the graph with the initial state
            # a second time (which would generate another question).
            recovered = self._recover_ready_checkpoint(record)
            if recovered is not None:
                return recovered

            initial = self._deserialize_frozen_state(record)
            result = self.graph.invoke(initial, self._config(record.id))
            values, snapshot = self._result_state(record.id, result)

            in_progress = transition_assessment(
                record,
                AssessmentStatus.IN_PROGRESS,
            )
            if not self.repository.save_if_version(
                in_progress,
                record.version,
            ):
                current = self.repository.get(record.id)
                return self._public_state_for_record(current, values, snapshot)
            return self._public_state(
                in_progress,
                values,
                snapshot=snapshot,
            )

    def answer(self, token: str, request: AnswerRequest) -> dict[str, Any]:
        token_hash = self._hash(token.strip())
        with self.container.interview_lock:
            cached = self.repository.get_answer_response(
                token_hash,
                request.idempotency_key,
            )
            if cached is not None:
                return cached

            record = self._assessment_for_token(token)
            if record.status is AssessmentStatus.COMPLETE:
                return {"state": "complete"}
            if record.status is not AssessmentStatus.IN_PROGRESS:
                raise InterviewStateError(
                    f"当前评估状态不支持提交回答: {record.status.value}"
                )

            values, snapshot = self._read_graph_state(record.id)
            terminal_report = values.get("assessment_report")
            if terminal_report is not None and not self._has_interrupt(
                values,
                snapshot,
            ):
                # The resume may have committed a terminal checkpoint before
                # the process died.  Complete persistence from that checkpoint
                # and cache this request, without resuming an ended graph.
                response = self._complete_with_report(record, terminal_report)
                self.repository.save_answer_response(
                    token_hash,
                    request.idempotency_key,
                    response,
                )
                return response

            current_turn_id = values.get("current_turn_id")
            public_state = self._public_state(
                record,
                values,
                snapshot=snapshot,
            )
            if current_turn_id != request.turn_id:
                raise StaleTurnError(current_turn_id, public_state)

            # Do not write an idempotency row until invoke and report CAS have
            # completed.  A graph failure can therefore be retried safely.
            result = self.graph.invoke(
                Command(resume=request.answer),
                self._config(record.id),
            )
            values, result_snapshot = self._result_state(record.id, result)
            report = values.get("assessment_report")
            if report is not None and not self._has_interrupt(
                result,
                result_snapshot,
            ):
                response = self._complete_with_report(record, report)
            else:
                response = self._public_state(
                    record,
                    values,
                    snapshot=result_snapshot,
                )

            self.repository.save_answer_response(
                token_hash,
                request.idempotency_key,
                response,
            )
            return response

    def _complete_with_report(
        self,
        record: AssessmentRecord,
        report: Any,
    ) -> dict[str, Any]:
        report_payload = jsonable(report)
        # Validate before persisting so a malformed graph result cannot mark
        # an assessment complete while storing arbitrary data.
        report_payload = AssessmentReport.model_validate(report_payload).model_dump(
            mode="json"
        )
        current = self.repository.get(record.id)
        if current.status is AssessmentStatus.COMPLETE:
            return {"state": "complete"}
        if current.status is not AssessmentStatus.IN_PROGRESS:
            raise InterviewStateError(
                f"报告完成时评估状态不是 IN_PROGRESS: {current.status.value}"
            )

        reporting = transition_assessment(current, AssessmentStatus.REPORTING).model_copy(
            update={"report": report_payload}
        )
        if not self.repository.save_if_version(reporting, current.version):
            latest = self.repository.get(record.id)
            if latest.status is AssessmentStatus.COMPLETE:
                return {"state": "complete"}
            raise InterviewStateError("评估报告保存冲突，请稍后重试")

        complete = transition_assessment(reporting, AssessmentStatus.COMPLETE)
        if not self.repository.save_if_version(complete, reporting.version):
            latest = self.repository.get(record.id)
            if latest.status is AssessmentStatus.COMPLETE:
                return {"state": "complete"}
            raise InterviewStateError("评估完成状态保存冲突，请稍后重试")
        return {"state": "complete"}

    def _deserialize_frozen_state(self, record: AssessmentRecord) -> dict[str, Any]:
        if record.final_plan is None or record.scoring_blueprint is None:
            raise InterviewStateError("评估计划尚未冻结")

        # ``pre_interview_state`` was intentionally stored as JSON.  Restore
        # the typed values consumed by the interview graph before invoking it.
        state: dict[str, Any] = dict(record.pre_interview_state or {})
        state.update(
            {
                "resume_text": record.resume_text,
                "jd_text": record.jd_text,
                "target_role": record.target_role,
                "interview_duration_minutes": record.interview_duration_minutes,
                "interview_plan": InterviewPlan.model_validate(record.final_plan),
                "scoring_blueprint": ScoringBlueprint.model_validate(
                    record.scoring_blueprint
                ),
                "claim_registry": ClaimRegistry.model_validate(
                    state.get("claim_registry") or {}
                ),
                "interview_turns": [],
                "evidences": [],
            }
        )
        return state

    def _read_graph_state(
        self,
        assessment_id: str,
    ) -> tuple[dict[str, Any], object | None]:
        raw = self.graph.get_state(self._config(assessment_id))
        if isinstance(raw, Mapping):
            values = raw.get("values", raw)
        else:
            values = getattr(raw, "values", {})
        if isinstance(values, BaseModel):
            values = values.model_dump(mode="python")
        if not isinstance(values, Mapping):
            values = {}
        return dict(values), raw

    def _result_state(
        self,
        assessment_id: str,
        result: Any,
    ) -> tuple[dict[str, Any], object | None]:
        if isinstance(result, Mapping):
            values = dict(result)
        elif isinstance(result, BaseModel):
            values = result.model_dump(mode="python")
        else:
            values = {}

        # Some injected graphs return only changed fields.  Merge those with
        # the checkpoint, while keeping the invoke result's interrupt marker.
        try:
            checkpoint_values, snapshot = self._read_graph_state(assessment_id)
        except (AttributeError, KeyError, RuntimeError):
            return values, None
        merged = dict(checkpoint_values)
        merged.update(values)
        return merged, snapshot

    @staticmethod
    def _has_interrupt(result: Any, snapshot: object | None) -> bool:
        if isinstance(result, Mapping):
            marker = result.get("__interrupt__") or result.get("interrupts")
            if marker:
                return True
        if snapshot is not None:
            marker = getattr(snapshot, "interrupts", None)
            if marker:
                return True
            if isinstance(snapshot, Mapping):
                marker = snapshot.get("__interrupt__") or snapshot.get("interrupts")
                if marker:
                    return True
        return False

    def _public_state_for_record(
        self,
        record: AssessmentRecord,
        values: dict[str, Any],
        snapshot: object | None,
    ) -> dict[str, Any]:
        if record.status is AssessmentStatus.COMPLETE:
            return {"state": "complete"}
        return self._public_state(record, values, snapshot=snapshot)

    def _public_state(
        self,
        record: AssessmentRecord,
        values: Mapping[str, Any],
        *,
        snapshot: object | None = None,
    ) -> dict[str, Any]:
        if record.status is AssessmentStatus.COMPLETE:
            return {"state": "complete"}

        turns = self._public_turns(values.get("interview_turns") or [])
        current_turn_id = values.get("current_turn_id")
        current_turn = next(
            (turn for turn in turns if turn["id"] == current_turn_id),
            None,
        )
        if current_turn is None and values.get("current_question") is not None:
            question = values["current_question"]
            if isinstance(question, BaseModel):
                question = question.model_dump(mode="python")
            if isinstance(question, Mapping) and current_turn_id:
                current_turn = {
                    "id": str(current_turn_id),
                    "sequence_number": len(turns) + 1,
                    "question": str(question.get("text", "")),
                    "answer": None,
                }

        runtime = values.get("runtime_state")
        started_at = getattr(runtime, "started_at", None)
        if started_at is None and isinstance(runtime, Mapping):
            started_at = runtime.get("started_at")
        elapsed_seconds = self._elapsed_seconds(started_at)

        if current_turn is None:
            # A checkpoint without an active interrupt is either still being
            # finalized or is a fake graph's empty state.  It contains no
            # enterprise details in this public fallback.
            return {
                "state": "waiting_for_answer",
                "target_role": record.target_role,
                "phase": "waiting",
                "elapsed_seconds": elapsed_seconds,
                "turns": turns,
            }
        return {
            "state": "waiting_for_answer",
            "target_role": record.target_role,
            "phase": "question",
            "elapsed_seconds": elapsed_seconds,
            "turns": turns,
            "turn": current_turn,
        }

    @staticmethod
    def _elapsed_seconds(started_at: Any) -> int:
        if isinstance(started_at, str):
            try:
                started_at = datetime.fromisoformat(started_at)
            except ValueError:
                return 0
        if not isinstance(started_at, datetime):
            return 0
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        return max(
            0,
            int((datetime.now(timezone.utc) - started_at).total_seconds()),
        )

    @staticmethod
    def _public_turns(raw_turns: Any) -> list[dict[str, Any]]:
        public: list[dict[str, Any]] = []
        for raw_turn in raw_turns:
            try:
                turn = InterviewTurn.model_validate(raw_turn)
            except Exception:
                if not isinstance(raw_turn, Mapping):
                    continue
                turn = raw_turn
            if isinstance(turn, InterviewTurn):
                public.append(
                    {
                        "id": turn.id,
                        "sequence_number": turn.sequence_number,
                        "question": turn.question,
                        "answer": turn.answer,
                    }
                )
            else:
                public.append(
                    {
                        "id": str(turn.get("id", "")),
                        "sequence_number": turn.get("sequence_number", len(public) + 1),
                        "question": str(turn.get("question", "")),
                        "answer": turn.get("answer"),
                    }
                )
        return public


__all__ = [
    "AnswerRequest",
    "InterviewService",
    "InterviewStateError",
    "StaleTurnError",
]
