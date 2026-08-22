"""全项目统一的大模型入口.

这一层只负责怎么调用模型, 不负责简历/JD/能力建模等业务逻辑.

为什么单独封装:
1. API Key、Base URL、temperature 等配置集中在一个地方;
2. Service 只关心自己的业务输入/输出, 不需要反复创建客户端;
3. 后续切换模型供应商时, 尽量不改业务层代码;
4. 普通文本调用与 Pydantic 结构化调用统一从这里出去.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TypeVar

from dotenv import load_dotenv
from langchain_core.exceptions import OutputParserException
from langchain_openai import ChatOpenAI
from openai import APIStatusError
from pydantic import BaseModel

# 从当前项目环境加载 .env.
# 在用户从项目根目录运行时, 会读取根目录 .env.
load_dotenv()

SchemaT = TypeVar("SchemaT", bound=BaseModel)
ResultT = TypeVar("ResultT")


class LLMProviderError(RuntimeError):
    """大模型供应商返回的可操作错误."""


class LLM:
    """项目级 LLM Wrapper.

    注意: 创建 LLM() 时不会立刻连接模型; 只有第一次真正 invoke 时才创建
    ChatOpenAI. 这样 Schema/Claim 等纯 Python 自检不会被 API Key 阻塞.
    """

    def __init__(self) -> None:
        self._model: ChatOpenAI | None = None

    def _build_model(self) -> ChatOpenAI:
        """根据环境变量创建真实 ChatOpenAI 模型对象."""

        api_key = os.getenv("MIMO_API_KEY", "").strip()
        if not api_key:
            raise ValueError("没有配置 MIMO_API_KEY, 请先在项目根目录 .env 中填写")

        model_name = os.getenv("MIMO_MODEL", "mimo-v2.5").strip()
        base_url = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").strip()

        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8192")),
            top_p=float(os.getenv("LLM_TOP_P", "0.95")),
            timeout=float(os.getenv("LLM_TIMEOUT", "120")),
        )

    @property
    def model(self) -> ChatOpenAI:
        """懒加载并复用同一个模型实例."""

        if self._model is None:
            self._model = self._build_model()
        return self._model

    def invoke(self, messages):
        """普通 LangChain 调用, 返回 AIMessage."""

        return self._invoke_provider(lambda: self.model.invoke(messages))

    @staticmethod
    def _invoke_provider(operation: Callable[[], ResultT]) -> ResultT:
        """调用模型并将已知的供应商错误转换为可操作提示."""

        try:
            return operation()
        except APIStatusError as exc:
            if exc.status_code == 402:
                raise LLMProviderError(
                    "MiMo API 余额不足（HTTP 402）。"
                    "请充值 MiMo 账户，或在项目根目录 .env 中更换"
                    "有余额的 MIMO_API_KEY 后重试。"
                ) from exc
            raise

    def structured(self, messages, schema: type[SchemaT]) -> SchemaT:
        """按 Pydantic Schema 获取结构化输出.

        Service 只要提供:
            messages + 目标 Schema
        就可以直接拿到 ResumeProfile / JobProfile 等 Pydantic 对象, 避免业务代码
        自己处理 json.loads()、字段缺失等细节.
        """

        # MiMo API 使用 OpenAI-compatible 接口, 这里继续使用 json_mode
        # json_mode 要求 prompt 中必须包含 "json" 关键词
        messages = list(messages)
        if messages and messages[0][0] == "system":
            messages[0] = (messages[0][0], messages[0][1] + "\n\n请严格按以下 JSON 结构输出, 字段名和类型必须完全匹配.")

        structured_model = self.model.with_structured_output(
            schema,
            method="json_mode",
        )
        retry_messages = messages
        for attempt in range(2):
            try:
                return self._invoke_provider(
                    lambda: structured_model.invoke(retry_messages)
                )
            except OutputParserException:
                if attempt == 1:
                    raise
                retry_messages = messages + [
                    (
                        "human",
                        "上一轮输出未通过结构校验。请重新生成，只返回严格符合目标 Schema 的 JSON，不要解释。",
                    )
                ]

        raise AssertionError("结构化输出重试循环意外结束")


# 全项目共享一个轻量 Wrapper; 真实客户端仍然是懒加载的.
llm = LLM()
