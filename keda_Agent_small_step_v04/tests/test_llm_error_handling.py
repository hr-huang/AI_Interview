"""LLM 供应商错误和命令行提示的回归测试."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from httpx import Request, Response
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


class LLMErrorHandlingTests(unittest.TestCase):
    def test_build_model_requires_mimo_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "MIMO_API_KEY"):
                LLM()._build_model()

    def test_build_model_uses_official_mimo_defaults(self) -> None:
        with (
            patch.dict(os.environ, {"MIMO_API_KEY": "test-key"}, clear=True),
            patch.object(llm_module, "ChatOpenAI") as chat_openai,
        ):
            LLM()._build_model()

        kwargs = chat_openai.call_args.kwargs
        self.assertEqual(kwargs["model"], "mimo-v2.5")
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["base_url"], "https://api.xiaomimimo.com/v1")

    def test_structured_http_402_is_translated_to_actionable_chinese_error(self) -> None:
        wrapper = LLM()
        wrapper._model = FailingModel()  # type: ignore[assignment]

        try:
            wrapper.structured([("system", "return json")], ExampleSchema)
        except BaseException as exc:
            self.assertIsInstance(exc, RuntimeError)
            self.assertNotIsInstance(exc, APIStatusError)
            self.assertIn("余额不足", str(exc))
            self.assertIn("MIMO_API_KEY", str(exc))
        else:
            self.fail("预期 HTTP 402 被转换为可操作的运行时错误")

if __name__ == "__main__":
    unittest.main()
