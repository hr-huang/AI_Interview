"""Run the pre-interview and interactive interview graphs from a terminal."""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from langgraph.types import Command

from profile_agent.graphs.interview import build_interview_graph
from profile_agent.graphs.pre_interview import pre_interview_graph
from profile_agent.schemas.interview_schema import FinishAction
from profile_agent.schemas.report_schema import AssessmentReport
from profile_agent.services.assessment_report_service import AssessmentReportStateError

try:
    from profile_agent.llm import LLMProviderError
except ImportError:  # pragma: no cover - compatibility with the current v0.4 wrapper
    class LLMProviderError(Exception):
        """Compatibility error name for projects without a provider exception."""


try:
    from langchain_core.exceptions import ModelError
except ImportError:  # pragma: no cover - dependency is installed with the project
    class ModelError(Exception):
        """Fallback type for provider errors from older LangChain versions."""


try:
    from openai import OpenAIError
except ImportError:  # pragma: no cover - dependency is installed with the project
    class OpenAIError(Exception):
        """Fallback type for OpenAI-compatible provider errors."""


DEMO_INITIAL_STATE: dict[str, Any] = {
    "resume_text": """
张三, 本科, 数据科学专业.
个人简介: 具备 AI 应用开发经验, 熟悉 LangGraph.

实习经历:
A科技有限公司, AI应用开发实习生, 2026.05-2026.08.
参与招聘智能体开发, 使用 LangGraph 搭建 Resume Understanding 与 Job Understanding 并行流程,
使用 FastAPI 开发后端接口; 参与优化后处理效率提升 40%.

项目经历:
招聘智能体: 本人负责整体 Workflow 设计, 使用 Python、LangGraph、FastAPI.
""",
    "jd_text": """
AI Agent 开发工程师:
负责 Agent 应用开发和 Workflow 设计;
要求熟练使用 Python, 能够独立设计 Agent Workflow, 熟悉 LangGraph, 理解 Tool Calling,
并具备实际故障定位能力.
""",
    "target_role": "AI Agent 开发工程师",
}


def _interrupt_payload(result: Mapping[str, Any]) -> Mapping[str, Any] | None:
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None

    if isinstance(interrupts, Mapping):
        interrupt = interrupts
    else:
        interrupt = interrupts[0]

    if isinstance(interrupt, Mapping) and "value" in interrupt:
        payload = interrupt["value"]
    else:
        payload = getattr(interrupt, "value", interrupt)
    if not isinstance(payload, Mapping):
        raise TypeError("面试中断 payload 必须是 mapping")
    return payload


def _finish_reason(result: Mapping[str, Any]) -> str | None:
    action = result.get("next_action")
    if isinstance(action, FinishAction):
        return action.reason

    if isinstance(action, Mapping) and action.get("action") == "finish":
        return action.get("reason")

    if getattr(action, "action", None) == "finish":
        return getattr(action, "reason", None)

    runtime_state = result.get("runtime_state")
    if isinstance(runtime_state, Mapping):
        return runtime_state.get("stop_reason")
    return getattr(runtime_state, "stop_reason", None)


def run_interview_session(
    graph: Any,
    initial_state: Mapping[str, Any],
    *,
    input_fn: Callable[[], str] = input,
    output_fn: Callable[[str], None] = print,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Drive an interruptible interview graph until it reaches a finish state."""

    session_thread_id = str(uuid.uuid4()) if thread_id is None else thread_id
    config = {"configurable": {"thread_id": session_thread_id}}
    result = graph.invoke(initial_state, config)

    while True:
        payload = _interrupt_payload(result)
        if payload is None:
            break

        output_fn(payload["question"])
        answer = input_fn()
        result = graph.invoke(Command(resume=answer), config)

    reason = _finish_reason(result)
    if reason is not None:
        output_fn(f"结束原因：{reason}")

    assessment_report = result.get("assessment_report")
    if assessment_report is not None:
        report_json = json.dumps(
            AssessmentReport.model_validate(assessment_report).model_dump(
                mode="json"
            ),
            ensure_ascii=False,
            indent=2,
        )
        output_fn("评估报告：")
        output_fn(report_json)
    return result


def _report_startup_failure(error: BaseException) -> None:
    detail = str(error).strip()
    if detail:
        print(f"启动失败：{detail}", file=sys.stderr)
    else:
        print("启动失败", file=sys.stderr)


def main() -> int:
    """Build the plan first, then start the interactive interview."""

    try:
        pre_interview_result = pre_interview_graph.invoke(DEMO_INITIAL_STATE)
    except (LLMProviderError, ModelError, OpenAIError, ValueError) as error:
        _report_startup_failure(error)
        return 1

    try:
        interview_graph = build_interview_graph()
        run_interview_session(interview_graph, pre_interview_result)
    except (
        LLMProviderError,
        ModelError,
        OpenAIError,
        AssessmentReportStateError,
    ) as error:
        _report_startup_failure(error)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\n已退出", file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEMO_INITIAL_STATE", "main", "run_interview_session"]
