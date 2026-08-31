"""Drive a real interruptible interview graph with frozen candidate answers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from langgraph.types import Command

from profile_agent.calibration.interview_assertions import evaluate_interview_path
from profile_agent.calibration.schemas import (
    InterviewCalibrationCase,
    InterviewCalibrationRun,
)
from profile_agent.calibration.scripted_candidate import select_scripted_answer
from profile_agent.graphs.interview import build_interview_graph
from profile_agent.graphs.pre_interview import pre_interview_graph
from profile_agent.schemas.interview_schema import InterviewPlan


class InterviewCalibrationRunnerError(RuntimeError):
    """Raised when the calibration driver cannot safely complete a session."""


def extract_interrupt_payload(
    result: Mapping[str, Any],
) -> Mapping[str, object] | None:
    """Normalise LangGraph interrupt containers across supported versions."""

    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None

    if isinstance(interrupts, Mapping):
        interrupt_value: object = interrupts
    else:
        try:
            interrupt_value = interrupts[0]
        except (IndexError, TypeError) as error:
            raise InterviewCalibrationRunnerError(
                "无法解析 LangGraph interrupt 容器"
            ) from error

    if isinstance(interrupt_value, Mapping) and "value" in interrupt_value:
        payload = interrupt_value["value"]
    else:
        payload = getattr(interrupt_value, "value", interrupt_value)

    if not isinstance(payload, Mapping):
        raise InterviewCalibrationRunnerError("LangGraph interrupt payload 不是 Mapping")
    return payload


def run_interview_calibration_case(
    case: InterviewCalibrationCase,
    *,
    run_number: int = 1,
    pre_interview_runner: Callable[[Mapping[str, object]], Mapping[str, Any]] | None = None,
    graph_builder: Callable[[], Any] | None = None,
    answer_selector: Callable[..., tuple[str, str]] | None = None,
) -> InterviewCalibrationRun:
    """Run one frozen candidate through the full dynamic interview path."""

    if run_number <= 0:
        raise ValueError("run_number 必须大于 0")
    if pre_interview_runner is None:
        pre_interview_runner = pre_interview_graph.invoke
    if graph_builder is None:
        graph_builder = build_interview_graph
    if answer_selector is None:
        answer_selector = select_scripted_answer

    pre_interview_input = {
        "resume_text": case.resume_text,
        "jd_text": case.jd_text,
        "target_role": case.target_role,
    }
    initial_state = dict(pre_interview_runner(pre_interview_input))
    plan = InterviewPlan.model_validate(initial_state.get("interview_plan"))

    graph = graph_builder()
    config = {
        "configurable": {
            "thread_id": f"calibration-{case.id}-{run_number}",
        }
    }
    result = graph.invoke(initial_state, config)
    usage_counts: dict[str, int] = {}
    selected_rule_ids: list[str] = []
    resume_count = 0
    resume_ceiling = case.path_expectation.max_questions + 1

    while (payload := extract_interrupt_payload(result)) is not None:
        if resume_count >= resume_ceiling:
            raise InterviewCalibrationRunnerError(
                f"动态面试 resume 次数超过防御上限 {resume_ceiling}"
            )
        answer, rule_id = answer_selector(
            payload=payload,
            plan=plan,
            rules=case.answer_rules,
            usage_counts=usage_counts,
        )
        usage_counts[rule_id] = usage_counts.get(rule_id, 0) + 1
        selected_rule_ids.append(rule_id)
        resume_count += 1
        result = graph.invoke(Command(resume=answer), config)

    if hasattr(graph, "get_state"):
        final_state = dict(graph.get_state(config).values)
    else:
        final_state = dict(result)
    assertions = evaluate_interview_path(
        case,
        final_state,
        selected_rule_ids,
    )
    return InterviewCalibrationRun(
        case_id=case.id,
        run_number=run_number,
        initial_state=initial_state,
        final_state=final_state,
        selected_rule_ids=selected_rule_ids,
        assertions=assertions,
    )


__all__ = [
    "InterviewCalibrationRunnerError",
    "extract_interrupt_payload",
    "run_interview_calibration_case",
]
