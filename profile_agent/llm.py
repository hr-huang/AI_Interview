"""全项目统一的大模型入口。"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import TypeVar

from dotenv import load_dotenv
from langchain_core.exceptions import OutputParserException
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import BaseModel

from profile_agent.model_runtime import (
    ModelRuntimeConfig,
    current_model_runtime_config,
)

load_dotenv()

SchemaT = TypeVar("SchemaT", bound=BaseModel)
ResultT = TypeVar("ResultT")
_TRACE_LOCK = Lock()
_DEFAULT_QWEN_MODEL = "qwen3.8-max"
_DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_TRACE_PATH = "artifacts/calibration/llm-traces.jsonl"


class LLMProviderError(RuntimeError):
    """大模型供应商返回的可操作错误。"""


def _env_config() -> ModelRuntimeConfig:
    api_key = os.getenv("QWEN_API_KEY", "").strip()
    if not api_key:
        raise ValueError("没有配置 QWEN_API_KEY，请先在项目根目录 .env 中填写")
    base_url = os.getenv("QWEN_BASE_URL", "").strip() or _DEFAULT_QWEN_BASE_URL
    provider = "glm" if "open.bigmodel.cn" in base_url.casefold() else "qwen"
    return ModelRuntimeConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=os.getenv("QWEN_MODEL", "").strip() or _DEFAULT_QWEN_MODEL,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8192")),
        top_p=float(os.getenv("LLM_TOP_P", "0.95")),
        timeout_seconds=float(os.getenv("QWEN_TIMEOUT", "600")),
    )


def _extra_body(config: ModelRuntimeConfig) -> dict[str, object] | None:
    if config.provider == "glm":
        return {"reasoning_effort": "high"}
    if config.provider == "qwen":
        return {"enable_thinking": False}
    return None


class LLM:
    """项目级 LLM Wrapper；业务层不感知具体 provider。"""

    def __init__(self) -> None:
        self._env_model: ChatOpenAI | None = None

    @staticmethod
    def _build_model(config: ModelRuntimeConfig) -> ChatOpenAI:
        kwargs = dict(
            model=config.model,
            api_key=config.api_key.get_secret_value(),
            base_url=config.base_url,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            timeout=config.timeout_seconds,
        )
        extra_body = _extra_body(config)
        if extra_body is not None:
            kwargs["extra_body"] = extra_body
        return ChatOpenAI(**kwargs)

    @property
    def model(self) -> ChatOpenAI:
        runtime_config = current_model_runtime_config()
        if runtime_config is not None:
            # BYOK configuration is scoped by ContextVar and must never reuse
            # another assessment's provider client.
            return self._build_model(runtime_config)
        if self._env_model is None:
            self._env_model = self._build_model(_env_config())
        return self._env_model

    def invoke(self, messages):
        try:
            response = self._invoke_provider(lambda: self.model.invoke(messages))
        except BaseException as error:
            self._write_trace(call_type="text", status="error", messages=messages, error=error)
            raise
        self._write_trace(call_type="text", status="ok", messages=messages, response=response)
        return response

    @staticmethod
    def _invoke_provider(operation: Callable[[], ResultT]) -> ResultT:
        retry_delays = (5, 15)
        for attempt in range(len(retry_delays) + 1):
            try:
                return operation()
            except APIStatusError as exc:
                body = exc.body if isinstance(exc.body, dict) else {}
                error_body = body.get("error", {})
                provider_code = (
                    str(error_body.get("code", ""))
                    if isinstance(error_body, dict)
                    else ""
                )
                if provider_code == "1113":
                    raise LLMProviderError(
                        "模型 API 余额不足或无可用资源包（业务码 1113）。请充值或绑定可用资源包后重试。"
                    ) from exc
                if exc.status_code == 402:
                    raise LLMProviderError("模型 API 余额不足（HTTP 402），请检查账户余额或资源包。") from exc
                if exc.status_code != 429 or attempt == len(retry_delays):
                    raise
                time.sleep(retry_delays[attempt])
        raise AssertionError("供应商重试循环意外结束")

    @staticmethod
    def _jsonable(value):
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {str(key): LLM._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [LLM._jsonable(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "content"):
            return {"content": LLM._jsonable(value.content)}
        return str(value)

    def _trace_identity(self) -> tuple[str, str, str | None]:
        runtime_config = current_model_runtime_config()
        if runtime_config is not None:
            return (
                runtime_config.provider,
                runtime_config.model,
                runtime_config.api_key.get_secret_value(),
            )
        base_url = os.getenv("QWEN_BASE_URL", "").strip()
        provider = "glm" if "open.bigmodel.cn" in base_url.casefold() else "qwen"
        return provider, os.getenv("QWEN_MODEL", "").strip() or _DEFAULT_QWEN_MODEL, os.getenv("QWEN_API_KEY", "").strip() or None

    def _write_trace(
        self,
        *,
        call_type: str,
        status: str,
        messages,
        schema: type[BaseModel] | None = None,
        attempt: int = 1,
        response=None,
        error: BaseException | None = None,
    ) -> None:
        if os.getenv("LLM_TRACE_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
            return
        trace_path = Path(os.getenv("LLM_TRACE_PATH", "").strip() or _DEFAULT_TRACE_PATH)
        provider, model, runtime_secret = self._trace_identity()
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": model,
            "call_type": call_type,
            "schema": schema.__name__ if schema is not None else None,
            "attempt": attempt,
            "status": status,
            "messages": self._jsonable(messages),
            "response": self._jsonable(response) if response is not None else None,
            "error_type": type(error).__name__ if error is not None else None,
            "error": str(error) if error is not None else None,
            "raw_output": getattr(error, "llm_output", None) if error is not None else None,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        secrets_to_redact = [runtime_secret]
        secrets_to_redact.extend(os.getenv(name, "").strip() for name in ("QWEN_API_KEY", "MIMO_API_KEY", "DEEPSEEK_API_KEY"))
        for secret in secrets_to_redact:
            if secret:
                line = line.replace(secret, "[REDACTED]")
        with _TRACE_LOCK:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def structured(self, messages, schema: type[SchemaT]) -> SchemaT:
        messages = list(messages)
        if messages and messages[0][0] == "system":
            messages[0] = (
                messages[0][0],
                messages[0][1] + "\n\n请严格按以下 JSON 结构输出，字段名和类型必须完全匹配。",
            )
        structured_model = self.model.with_structured_output(schema, method="json_mode")
        retry_messages = messages
        for attempt in range(2):
            try:
                result = self._invoke_provider(lambda: structured_model.invoke(retry_messages))
            except OutputParserException as error:
                self._write_trace(call_type="structured", status="parse_error", messages=retry_messages, schema=schema, attempt=attempt + 1, error=error)
                if attempt == 1:
                    raise
                retry_messages = messages + [("human", "上一轮输出未通过结构校验。请重新生成，只返回严格符合目标 Schema 的 JSON，不要解释。")]
            except (APIConnectionError, APITimeoutError) as error:
                self._write_trace(call_type="structured", status="error", messages=retry_messages, schema=schema, attempt=attempt + 1, error=error)
                if attempt == 1:
                    raise
            except BaseException as error:
                self._write_trace(call_type="structured", status="error", messages=retry_messages, schema=schema, attempt=attempt + 1, error=error)
                raise
            else:
                self._write_trace(call_type="structured", status="ok", messages=retry_messages, schema=schema, attempt=attempt + 1, response=result)
                return result
        raise AssertionError("结构化输出重试循环意外结束")


llm = LLM()
