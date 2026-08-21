from contextlib import redirect_stderr
from io import StringIO
from unittest import TestCase
from unittest.mock import patch
from uuid import UUID

from langgraph.types import Command

import run_interview_demo
from profile_agent.schemas.interview_schema import FinishAction


class FakeInterrupt:
    def __init__(self, payload: dict) -> None:
        self.value = payload


class FakeGraph:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[object, dict]] = []

    def invoke(self, value: object, config: dict) -> dict:
        self.calls.append((value, config))
        return self.responses.pop(0)


class InterviewDemoSessionTest(TestCase):
    def test_session_prints_each_question_once_and_resumes_once_per_answer(self) -> None:
        graph = FakeGraph(
            [
                {"__interrupt__": [FakeInterrupt({"question": "问题一"})]},
                {"__interrupt__": [FakeInterrupt({"question": "问题二"})]},
                {
                    "next_action": FinishAction(reason="已覆盖核心要求"),
                    "final": True,
                },
            ]
        )
        initial_state = {"interview_plan": "plan"}
        answers = ["回答一", "回答二"]
        input_calls: list[bool] = []
        output: list[str] = []

        def read_answer() -> str:
            input_calls.append(True)
            return answers.pop(0)

        result = run_interview_demo.run_interview_session(
            graph,
            initial_state,
            input_fn=read_answer,
            output_fn=output.append,
            thread_id="test-thread",
        )

        self.assertEqual(output, ["问题一", "问题二", "结束原因：已覆盖核心要求"])
        self.assertEqual(len(input_calls), 2)
        self.assertEqual(len(graph.calls), 3)
        self.assertIs(graph.calls[0][0], initial_state)
        self.assertIsInstance(graph.calls[1][0], Command)
        self.assertIsInstance(graph.calls[2][0], Command)
        self.assertEqual(graph.calls[1][0].resume, "回答一")
        self.assertEqual(graph.calls[2][0].resume, "回答二")
        self.assertEqual(
            [call[1] for call in graph.calls],
            [
                {"configurable": {"thread_id": "test-thread"}},
                {"configurable": {"thread_id": "test-thread"}},
                {"configurable": {"thread_id": "test-thread"}},
            ],
        )
        self.assertEqual(result["final"], True)

    def test_session_generates_thread_id_when_not_provided(self) -> None:
        graph = FakeGraph(
            [
                {"next_action": FinishAction(reason="没有问题需要提出")},
            ]
        )

        with patch.object(
            run_interview_demo.uuid,
            "uuid4",
            return_value=UUID("12345678-1234-5678-1234-567812345678"),
        ):
            run_interview_demo.run_interview_session(
                graph,
                {"interview_plan": "plan"},
                output_fn=lambda _: None,
            )

        self.assertEqual(
            graph.calls[0][1],
            {"configurable": {"thread_id": "12345678-1234-5678-1234-567812345678"}},
        )

    def test_session_accepts_mapping_interrupt_values(self) -> None:
        graph = FakeGraph(
            [
                {"__interrupt__": [{"value": {"question": "问题"}}]},
                {"next_action": {"action": "finish", "reason": "完成"}},
            ]
        )
        output: list[str] = []

        run_interview_demo.run_interview_session(
            graph,
            {"interview_plan": "plan"},
            input_fn=lambda: "回答",
            output_fn=output.append,
            thread_id="mapping-interrupt",
        )

        self.assertEqual(output, ["问题", "结束原因：完成"])


class InterviewDemoMainTest(TestCase):
    def test_main_runs_pre_interview_then_interview_session(self) -> None:
        events: list[object] = []
        pre_result = {
            "interview_plan": "plan",
            "claim_registry": "claims",
        }
        fake_pre_graph = type(
            "FakePreGraph",
            (),
            {"invoke": lambda _self, state: events.append(("pre", state)) or pre_result},
        )()
        fake_interview_graph = object()

        def fake_build_graph() -> object:
            events.append("build")
            return fake_interview_graph

        def fake_session(graph: object, state: dict) -> dict:
            events.append(("session", graph, state))
            return {"done": True}

        with (
            patch.object(run_interview_demo, "pre_interview_graph", fake_pre_graph),
            patch.object(run_interview_demo, "build_interview_graph", fake_build_graph),
            patch.object(run_interview_demo, "run_interview_session", fake_session),
        ):
            exit_code = run_interview_demo.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(events[0][0], "pre")
        self.assertEqual(events[1], "build")
        self.assertEqual(events[2], ("session", fake_interview_graph, pre_result))

    def test_main_reports_llm_provider_error_as_startup_failure(self) -> None:
        error = run_interview_demo.LLMProviderError("provider unavailable")
        fake_pre_graph = type(
            "FakePreGraph",
            (),
            {"invoke": lambda _self, _state: (_ for _ in ()).throw(error)},
        )()
        stderr = StringIO()

        with patch.object(run_interview_demo, "pre_interview_graph", fake_pre_graph):
            with redirect_stderr(stderr):
                exit_code = run_interview_demo.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("启动失败", stderr.getvalue())

    def test_main_reports_provider_error_while_building_or_running(self) -> None:
        error = run_interview_demo.LLMProviderError("provider unavailable")
        fake_pre_graph = type(
            "FakePreGraph",
            (),
            {"invoke": lambda _self, _state: {"interview_plan": "plan"}},
        )()
        stderr = StringIO()

        with (
            patch.object(run_interview_demo, "pre_interview_graph", fake_pre_graph),
            patch.object(run_interview_demo, "build_interview_graph", side_effect=error),
            redirect_stderr(stderr),
        ):
            exit_code = run_interview_demo.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("启动失败", stderr.getvalue())


if __name__ == "__main__":
    import unittest

    unittest.main()
