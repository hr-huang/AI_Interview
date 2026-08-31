from __future__ import annotations

import ipaddress
import secrets
from contextlib import contextmanager
from contextvars import ContextVar, Token
from threading import RLock
from typing import Any, Iterator, Literal, Mapping
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


ProviderName = Literal["qwen", "deepseek", "glm", "openai_compatible"]

_PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "qwen": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3.8-max",
    ),
    "deepseek": (
        "https://api.deepseek.com/v1",
        "deepseek-chat",
    ),
    "glm": (
        "https://open.bigmodel.cn/api/paas/v4",
        "glm-4.5",
    ),
}


class ModelRuntimeConfig(BaseModel):
    provider: ProviderName = "qwen"
    base_url: str = ""
    api_key: SecretStr
    model: str = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=8192, ge=256, le=65536)
    top_p: float = Field(default=0.95, gt=0, le=1)
    timeout_seconds: float = Field(default=120, ge=5, le=600)

    @field_validator("base_url", "model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_endpoint(self) -> "ModelRuntimeConfig":
        if not self.base_url:
            default = _PROVIDER_DEFAULTS.get(self.provider)
            if default is None:
                raise ValueError("自定义 OpenAI-compatible 模型必须填写 Base URL")
            self.base_url = default[0]
        if not self.model:
            default = _PROVIDER_DEFAULTS.get(self.provider)
            if default is None:
                raise ValueError("必须填写模型名称")
            self.model = default[1]

        parsed = urlparse(self.base_url)
        if parsed.scheme != "https":
            raise ValueError("Base URL 仅允许 HTTPS")
        if not parsed.hostname:
            raise ValueError("Base URL 缺少有效主机名")
        host = parsed.hostname.casefold()
        if host == "localhost" or host.endswith(".localhost"):
            raise ValueError("Base URL 不允许访问 localhost")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise ValueError("Base URL 不允许访问私有或本机网络地址")
        return self

    def public_view(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
        }


_CURRENT_MODEL_CONFIG: ContextVar[ModelRuntimeConfig | None] = ContextVar(
    "current_model_runtime_config",
    default=None,
)


def current_model_runtime_config() -> ModelRuntimeConfig | None:
    return _CURRENT_MODEL_CONFIG.get()


@contextmanager
def use_model_runtime_config(
    config: ModelRuntimeConfig | None,
) -> Iterator[None]:
    token: Token[ModelRuntimeConfig | None] = _CURRENT_MODEL_CONFIG.set(config)
    try:
        yield
    finally:
        _CURRENT_MODEL_CONFIG.reset(token)


class ModelRuntimeRegistry:
    """Keep BYOK credentials in process memory only and bind them to assessments."""

    def __init__(self) -> None:
        self._sessions: dict[str, ModelRuntimeConfig] = {}
        self._assessment_sessions: dict[str, str] = {}
        self._lock = RLock()

    def create_session(self, config: ModelRuntimeConfig) -> str:
        session_id = f"ms_{secrets.token_urlsafe(24)}"
        with self._lock:
            self._sessions[session_id] = config
        return session_id

    def bind_assessment(self, assessment_id: str, session_id: str) -> None:
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(session_id)
            self._assessment_sessions[assessment_id] = session_id

    def config_for_assessment(self, assessment_id: str) -> ModelRuntimeConfig | None:
        with self._lock:
            session_id = self._assessment_sessions.get(assessment_id)
            return self._sessions.get(session_id) if session_id else None

    def public_session(self, session_id: str) -> dict[str, str]:
        with self._lock:
            config = self._sessions[session_id]
            return {"model_session_id": session_id, **config.public_view()}

    @contextmanager
    def use_for_assessment(self, assessment_id: str) -> Iterator[None]:
        with use_model_runtime_config(self.config_for_assessment(assessment_id)):
            yield

    def release_assessment(self, assessment_id: str) -> None:
        with self._lock:
            session_id = self._assessment_sessions.pop(assessment_id, None)
            if session_id is not None:
                self._sessions.pop(session_id, None)


class ModelScopedGraph:
    """Transparent graph proxy that applies the assessment's BYOK config on invoke."""

    def __init__(self, graph: object, registry: ModelRuntimeRegistry) -> None:
        self._graph = graph
        self._registry = registry

    @staticmethod
    def _assessment_id(config: Any) -> str | None:
        if not isinstance(config, Mapping):
            return None
        configurable = config.get("configurable")
        if not isinstance(configurable, Mapping):
            return None
        thread_id = configurable.get("thread_id")
        return str(thread_id) if thread_id else None

    def invoke(self, input: Any, config: Any = None, *args: Any, **kwargs: Any):
        assessment_id = self._assessment_id(config)
        if assessment_id is None:
            return self._graph.invoke(input, config, *args, **kwargs)
        with self._registry.use_for_assessment(assessment_id):
            return self._graph.invoke(input, config, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)


__all__ = [
    "ModelRuntimeConfig",
    "ModelRuntimeRegistry",
    "ModelScopedGraph",
    "ProviderName",
    "current_model_runtime_config",
    "use_model_runtime_config",
]
