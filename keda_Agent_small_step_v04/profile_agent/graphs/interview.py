"""Interruptible dynamic interview graph.

The question generation and answer interrupt deliberately live in separate
nodes.  LangGraph replays the node containing ``interrupt`` when a resume
command arrives; keeping generation in the preceding node therefore prevents
the same turn from receiving a second generated question.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from profile_agent.schemas.interview_schema import (
    AskAction,
    FinishAction,
    GeneratedQuestion,
)
from profile_agent.schemas.report_schema import AssessmentReport
from profile_agent.schemas.runtime_schema import (
    AnswerProcessingResult,
    InterviewTurn,
)
from profile_agent.services.answer_processor_service import process_answer
from profile_agent.services.assessment_report_service import (
    generate_assessment_report,
)
from profile_agent.services.question_generator_service import generate_question
from profile_agent.services.runtime_state_service import (
    initialize_runtime_state,
    record_question_asked,
    request_stop,
)
from profile_agent.services.supervisor_service import (
    build_supervisor_context,
    decide_next_action,
)
from profile_agent.state.main_state import MainState


QuestionGenerator = Callable[..., GeneratedQuestion]
AnswerProcessor = Callable[..., AnswerProcessingResult]
ReportGenerator = Callable[..., AssessmentReport]
NowProvider = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_generated_question(value: GeneratedQuestion | Any) -> GeneratedQuestion:
    if isinstance(value, GeneratedQuestion):
        return value
    return GeneratedQuestion.model_validate(value)


def _as_answer_processing_result(
    value: AnswerProcessingResult | Any,
) -> AnswerProcessingResult:
    if isinstance(value, AnswerProcessingResult):
        return value
    return AnswerProcessingResult.model_validate(value)


def _turn_by_id(turns: list[InterviewTurn], turn_id: str) -> tuple[int, InterviewTurn]:
    for index, turn in enumerate(turns):
        if turn.id == turn_id:
            return index, turn
    raise ValueError(f"不存在的当前 InterviewTurn: {turn_id}")


def build_interview_graph(
    question_generator: QuestionGenerator | None = None,
    answer_processor: AnswerProcessor | None = None,
    checkpointer: Any | None = None,
    now_provider: NowProvider | None = None,
    report_generator: ReportGenerator | None = None,
):
    """Build the interruptible interview graph.

    ``question_generator``, ``answer_processor`` and ``report_generator``
    default to the real service functions but can be replaced with
    deterministic fakes.  The compiled graph always has a checkpointer so
    callers can resume with a ``Command(resume=...)`` using the same
    configurable ``thread_id``.
    """

    question_generator = (
        generate_question if question_generator is None else question_generator
    )
    answer_processor = (
        process_answer if answer_processor is None else answer_processor
    )
    report_generator = (
        generate_assessment_report
        if report_generator is None
        else report_generator
    )
    now_provider = _utc_now if now_provider is None else now_provider
    checkpointer = InMemorySaver() if checkpointer is None else checkpointer

    def initialize_interview(state: MainState) -> dict[str, Any]:
        runtime_state = state.get("runtime_state")
        if runtime_state is None:
            runtime_state = initialize_runtime_state(
                state["interview_plan"],
                started_at=now_provider(),
            )

        return {
            "runtime_state": runtime_state,
            "interview_turns": list(state.get("interview_turns") or []),
            "evidences": list(state.get("evidences") or []),
        }

    def supervisor(state: MainState) -> dict[str, Any]:
        plan = state["interview_plan"]
        runtime_state = state["runtime_state"]
        turns = list(state.get("interview_turns") or [])
        evidences = list(state.get("evidences") or [])
        claim_registry = state.get("claim_registry")

        context = build_supervisor_context(
            plan=plan,
            runtime_state=runtime_state,
            turns=turns,
            evidences=evidences,
            claim_registry=claim_registry,
            now=now_provider(),
        )
        action = decide_next_action(context)
        updates: dict[str, Any] = {"next_action": action}

        if isinstance(action, FinishAction):
            # Persist the stop request before the conditional edge routes to
            # END, so the completed checkpoint contains the terminal reason.
            updates["runtime_state"] = request_stop(
                runtime_state,
                action.reason,
            )

        return updates

    def generate_question_node(state: MainState) -> dict[str, Any]:
        action = state.get("next_action")
        if not isinstance(action, AskAction):
            raise ValueError("generate_question 节点需要 AskAction")

        plan = state["interview_plan"]
        runtime_state = state["runtime_state"]
        now = now_provider()
        question = _as_generated_question(
            question_generator(
                action=action,
                plan=plan,
                claim_registry=state.get("claim_registry"),
                recent_turns=list(state.get("interview_turns") or []),
            )
        )
        question_text = question.text.strip()
        if not question_text:
            raise ValueError("生成的问题文本不能为空")

        sequence_number = runtime_state.question_count + 1
        turn = InterviewTurn(
            id=f"turn_{sequence_number:03d}",
            sequence_number=sequence_number,
            target_id=action.target_id,
            primary_requirement_id=action.primary_requirement_id,
            question_mode=action.question_mode,
            question=question_text,
            answer=None,
            asked_at=now,
        )
        # The runtime update is made in the same node as the persisted
        # unanswered turn, before control reaches the interrupt node.
        updated_runtime = record_question_asked(
            plan=plan,
            runtime_state=runtime_state,
            target_id=action.target_id,
            primary_requirement_id=action.primary_requirement_id,
            now=now,
        )
        turns = list(state.get("interview_turns") or [])
        turns.append(turn)

        return {
            "runtime_state": updated_runtime,
            "interview_turns": turns,
            "current_question": GeneratedQuestion(text=question_text),
            "current_turn_id": turn.id,
        }

    def wait_for_answer(state: MainState) -> dict[str, Any]:
        turn_id = state.get("current_turn_id")
        if not turn_id:
            raise ValueError("wait_for_answer 节点缺少 current_turn_id")

        turns = list(state.get("interview_turns") or [])
        turn_index, turn = _turn_by_id(turns, turn_id)
        action = state.get("next_action")
        payload: dict[str, Any] = {
            "question": turn.question,
            "turn_id": turn.id,
        }
        if isinstance(action, AskAction):
            payload["action"] = action.model_dump(mode="json")

        answer = interrupt(payload)
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("回答不能为空")

        turns[turn_index] = turn.model_copy(
            update={
                "answer": answer,
                "answered_at": now_provider(),
            }
        )
        return {"interview_turns": turns}

    def process_answer_node(state: MainState) -> dict[str, Any]:
        turn_id = state.get("current_turn_id")
        if not turn_id:
            raise ValueError("process_answer 节点缺少 current_turn_id")

        turns = list(state.get("interview_turns") or [])
        _, turn = _turn_by_id(turns, turn_id)
        result = _as_answer_processing_result(
            answer_processor(
                plan=state["interview_plan"],
                runtime_state=state["runtime_state"],
                turn=turn,
                existing_evidences=list(state.get("evidences") or []),
                claim_registry=state.get("claim_registry"),
            )
        )
        evidences = list(state.get("evidences") or [])
        evidences.extend(result.new_evidences)
        return {
            "evidences": evidences,
            "runtime_state": result.runtime_state,
        }

    def generate_report_node(state: MainState) -> dict[str, Any]:
        report = report_generator(
            plan=state["interview_plan"],
            runtime_state=state["runtime_state"],
            turns=list(state.get("interview_turns") or []),
            evidences=list(state.get("evidences") or []),
            claim_registry=state.get("claim_registry"),
            target_role=state.get("target_role"),
            scoring_blueprint=state.get("scoring_blueprint"),
            candidate_id=state.get("assessment_id", "未提供"),
            resume_profile=state.get("resume_profile"),
            job_profile=state.get("job_profile"),
        )
        return {"assessment_report": AssessmentReport.model_validate(report)}

    def route_after_supervisor(state: MainState) -> str:
        action = state.get("next_action")
        if isinstance(action, AskAction):
            return "ask"
        if isinstance(action, FinishAction):
            return "finish"
        raise ValueError("supervisor 必须产生 AskAction 或 FinishAction")

    builder = StateGraph(MainState)
    builder.add_node("initialize_interview", initialize_interview)
    builder.add_node("supervisor", supervisor)
    builder.add_node("generate_question", generate_question_node)
    builder.add_node("wait_for_answer", wait_for_answer)
    builder.add_node("process_answer", process_answer_node)
    builder.add_node("generate_report", generate_report_node)

    builder.add_edge(START, "initialize_interview")
    builder.add_edge("initialize_interview", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "ask": "generate_question",
            "finish": "generate_report",
        },
    )
    builder.add_edge("generate_question", "wait_for_answer")
    builder.add_edge("wait_for_answer", "process_answer")
    builder.add_edge("process_answer", "supervisor")
    builder.add_edge("generate_report", END)

    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_interview_graph"]
