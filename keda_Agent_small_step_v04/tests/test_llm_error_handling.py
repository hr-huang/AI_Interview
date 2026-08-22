"""LLM 供应商错误和命令行提示的回归测试."""

from __future__ import annotations

import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from httpx import Request, Response
from langchain_core.exceptions import OutputParserException
from openai import APIStatusError
from pydantic import BaseModel

import profile_agent.llm as llm_module
from profile_agent.llm import LLM


def insufficient_balance_error() -> APIStatusError:
    """构造与 MiMo HTTP 402 响应等价的 OpenAI SDK 异常."""

    return APIStatusError(
        "Insufficient Balance",
        response=Response(
            402,
            request=Request(
                "POST",
                "https://api.xiaomimimo.com/v1/chat/completions",
            ),
        ),
        body={"error": {"message": "Insufficient Balance"}},
    )


class FailingModel:
    def with_structured_output(self, schema, method):
        return self

    def invoke(self, messages):
        raise insufficient_balance_error()


class ExampleSchema(BaseModel):
    value: str


class FlakyStructuredModel:
    def __init__(self) -> None:
        self.calls = []

    def with_structured_output(self, schema, method):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        if len(self.calls) == 1:
            raise OutputParserException("invalid structured output")
        return ExampleSchema(value="recovered")


class LLMErrorHandlingTests(unittest.TestCase):
    def test_build_model_requires_qwen_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "QWEN_API_KEY"):
                LLM()._build_model()

    def test_build_model_uses_official_qwen38_max_defaults(self) -> None:
        with (
            patch.dict(os.environ, {"QWEN_API_KEY": "test-key"}, clear=True),
            patch.object(llm_module, "ChatOpenAI") as chat_openai,
        ):
            LLM()._build_model()

        kwargs = chat_openai.call_args.kwargs
        self.assertEqual(kwargs["model"], "qwen3.8-max")
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(
            kwargs["base_url"],
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.assertEqual(kwargs["max_tokens"], 8192)
        self.assertEqual(kwargs["timeout"], 600.0)
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})

    def test_structured_http_402_is_translated_to_actionable_chinese_error(self) -> None:
        wrapper = LLM()
        wrapper._model = FailingModel()  # type: ignore[assignment]

        try:
            wrapper.structured([("system", "return json")], ExampleSchema)
        except BaseException as exc:
            self.assertIsInstance(exc, RuntimeError)
            self.assertNotIsInstance(exc, APIStatusError)
            self.assertIn("余额不足", str(exc))
            self.assertIn("QWEN_API_KEY", str(exc))
        else:
            self.fail("预期 HTTP 402 被转换为可操作的运行时错误")

    def test_structured_output_parser_failure_is_retried_once_with_correction(self) -> None:
        wrapper = LLM()
        model = FlakyStructuredModel()
        wrapper._model = model  # type: ignore[assignment]

        result = wrapper.structured([("system", "return json")], ExampleSchema)

        self.assertEqual(result, ExampleSchema(value="recovered"))
        self.assertEqual(len(model.calls), 2)
        self.assertIn("上一轮输出未通过结构校验", model.calls[1][-1][1])

    def test_structured_questions_answers_and_errors_append_to_one_redacted_jsonl_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "qwen-trace.jsonl"
            wrapper = LLM()
            model = FlakyStructuredModel()
            wrapper._model = model  # type: ignore[assignment]

            with patch.dict(
                os.environ,
                {
                    "QWEN_API_KEY": "super-secret-key",
                    "LLM_TRACE_PATH": str(trace_path),
                },
                clear=False,
            ):
                wrapper.structured(
                    [("system", "return json"), ("human", "super-secret-key")],
                    ExampleSchema,
                )

            records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record["status"] for record in records], ["parse_error", "ok"])
            self.assertTrue(all(record["provider"] == "qwen" for record in records))
            self.assertTrue(all(record["model"] == "qwen3.8-max" for record in records))
            self.assertNotIn("super-secret-key", trace_path.read_text(encoding="utf-8"))
            self.assertIn("[REDACTED]", trace_path.read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
