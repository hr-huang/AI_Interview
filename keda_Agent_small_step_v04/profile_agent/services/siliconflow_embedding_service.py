"""Secret-safe HTTP client for SiliconFlow's BGE-M3 embeddings endpoint."""

from __future__ import annotations

import logging
import math
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx


logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-m3"
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_PROVIDER = "siliconflow"
DEFAULT_DIMENSION = 1024
DEFAULT_INDEX_VERSION = "questions-v1"
_RETRYABLE_STATUS_CODES = frozenset({429}) | frozenset(range(500, 600))
_INVALID_RESPONSE = object()


class EmbeddingConfigurationError(ValueError):
    """Raised when the shared embedding/index identity is not usable."""


@dataclass(frozen=True)
class EmbeddingConfig:
    """Canonical provider identity shared by writers, readers, and clients."""

    provider: str
    model: str
    dimension: int
    index_version: str
    base_url: str


def _env_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value).strip()
    return value.strip()


def _first_non_blank(
    env: Mapping[str, Any],
    names: Sequence[str],
    default: str,
) -> str:
    for name in names:
        value = env.get(name)
        text = _env_text(value)
        if text:
            return text
    return default.strip()


def _require_config_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EmbeddingConfigurationError(f"{label} must not be blank")
    return value.strip()


def parse_embedding_dimension(value: Any) -> int:
    """Parse a positive integer dimension without silently truncating values."""

    if isinstance(value, bool):
        raise EmbeddingConfigurationError(
            "embedding dimension must be a positive integer"
        )
    if isinstance(value, int):
        dimension = value
    elif isinstance(value, str) and re.fullmatch(r"[+]?[0-9]+", value.strip()):
        try:
            dimension = int(value.strip())
        except (TypeError, ValueError, OverflowError) as exc:
            raise EmbeddingConfigurationError(
                "embedding dimension must be a positive integer"
            ) from exc
    else:
        raise EmbeddingConfigurationError(
            "embedding dimension must be a positive integer"
        )
    if dimension <= 0:
        raise EmbeddingConfigurationError(
            "embedding dimension must be a positive integer"
        )
    return dimension


def _validate_base_url(value: Any) -> str:
    base_url = _require_config_text(value, "embedding base URL")
    try:
        parsed = urlparse(base_url)
        # Accessing .port forces urlparse to validate malformed port syntax.
        _ = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise EmbeddingConfigurationError("embedding base URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EmbeddingConfigurationError("embedding base URL is invalid")
    return base_url


def resolve_embedding_config(
    env: Mapping[str, Any] | None = None,
    *,
    model: str | None = None,
    provider: str | None = None,
    dimension: int | str | None = None,
    index_version: str | None = None,
    base_url: str | None = None,
) -> EmbeddingConfig:
    """Resolve the canonical provider/index identity from one environment.

    ``QUESTION_RAG_EMBEDDING_MODEL`` is the preferred model setting, while
    ``SILICONFLOW_EMBEDDING_MODEL`` remains a compatibility fallback.  Blank
    environment values are treated as unset; explicit function overrides are
    validated as values and therefore cannot be blank.
    """

    environment = os.environ if env is None else env
    if not isinstance(environment, Mapping):
        raise EmbeddingConfigurationError("environment configuration is invalid")
    resolved_model = _require_config_text(
        model
        if model is not None
        else _first_non_blank(
            environment,
            ("QUESTION_RAG_EMBEDDING_MODEL", "SILICONFLOW_EMBEDDING_MODEL"),
            DEFAULT_MODEL,
        ),
        "embedding model",
    )
    resolved_provider = _require_config_text(
        provider
        if provider is not None
        else _first_non_blank(
            environment,
            ("QUESTION_RAG_EMBEDDING_PROVIDER",),
            DEFAULT_PROVIDER,
        ),
        "embedding provider",
    )
    resolved_dimension = parse_embedding_dimension(
        dimension
        if dimension is not None
        else _first_non_blank(
            environment,
            ("QUESTION_RAG_EMBEDDING_DIMENSION",),
            str(DEFAULT_DIMENSION),
        )
    )
    resolved_index_version = _require_config_text(
        index_version
        if index_version is not None
        else _first_non_blank(
            environment,
            ("QUESTION_RAG_INDEX_VERSION",),
            DEFAULT_INDEX_VERSION,
        ),
        "index version",
    )
    resolved_base_url = _validate_base_url(
        base_url
        if base_url is not None
        else _first_non_blank(
            environment,
            ("SILICONFLOW_EMBEDDING_BASE_URL",),
            DEFAULT_BASE_URL,
        )
    )
    return EmbeddingConfig(
        provider=resolved_provider,
        model=resolved_model,
        dimension=resolved_dimension,
        index_version=resolved_index_version,
        base_url=resolved_base_url,
    )


@runtime_checkable
class EmbeddingClient(Protocol):
    """Minimal embedding-provider boundary used by retrieval services."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input text, preserving input order."""


class EmbeddingProviderError(RuntimeError):
    """Raised when the embedding provider cannot return valid vectors."""


class SiliconFlowEmbeddingClient:
    """Synchronous SiliconFlow embeddings client with bounded safe retries.

    The client sends a batch as one ``/embeddings`` request.  Response vectors
    are sorted by the provider's explicit ``index`` field instead of trusting
    response order.  API keys, request texts, and provider response bodies are
    deliberately absent from errors and logs.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        provider: str = DEFAULT_PROVIDER,
        dimension: int | None = None,
        timeout_seconds: float = 30.0,
        max_attempts: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("没有配置 SiliconFlow API key")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("embedding model 不能为空")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("embedding base URL 不能为空")
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("embedding provider 不能为空")
        if dimension is not None:
            try:
                dimension = parse_embedding_dimension(dimension)
            except EmbeddingConfigurationError as exc:
                raise ValueError(str(exc)) from exc
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须为正数")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts 必须为正整数")

        self.api_key = api_key.strip()
        self.model = model.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.provider = provider.strip()
        if dimension is not None:
            self.dimension = dimension
        self.timeout_seconds = float(timeout_seconds)
        self.max_attempts = max_attempts
        self._http_client = (
            http_client
            if http_client is not None
            else httpx.Client(timeout=self.timeout_seconds)
        )
        self._owns_http_client = http_client is None

    @classmethod
    def from_env(
        cls,
        *,
        http_client: httpx.Client | None = None,
        env: Mapping[str, Any] | None = None,
    ) -> "SiliconFlowEmbeddingClient":
        """Build a client from environment variables without reading secrets aloud."""

        environment = os.environ if env is None else env
        config = resolve_embedding_config(environment)
        api_key = _env_text(environment.get("SILICONFLOW_API_KEY"))
        if not api_key:
            raise ValueError("没有配置 SILICONFLOW_API_KEY, 请先设置该环境变量")

        return cls(
            api_key=api_key,
            model=config.model,
            base_url=config.base_url,
            provider=config.provider,
            dimension=config.dimension,
            http_client=http_client,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a non-empty batch and return vectors in input order."""

        batch = self._validate_texts(texts)
        request_payload = {"model": self.model, "input": batch}
        endpoint = f"{self.base_url}/embeddings"

        for attempt in range(1, self.max_attempts + 1):
            response: httpx.Response | None = None
            transport_failed = False
            unexpected_failure = False
            try:
                response = self._http_client.post(
                    endpoint,
                    json=request_payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.timeout_seconds,
                )
            except httpx.TransportError:
                transport_failed = True
            except Exception:
                # The exception may contain request details supplied by an
                # adapter, so never include it in a log record or exception.
                unexpected_failure = True

            if response is None and not transport_failed:
                unexpected_failure = True

            if unexpected_failure:
                logger.warning(
                    "embedding provider client failure on attempt %d",
                    attempt,
                )
                raise EmbeddingProviderError(
                    "SiliconFlow embedding request failed."
                )

            if transport_failed:
                if attempt < self.max_attempts:
                    self._log_retry(attempt, reason="transport")
                    self._sleep_before_retry(attempt)
                    continue
                logger.warning("embedding provider transport failure after final attempt")
                raise EmbeddingProviderError(
                    "SiliconFlow embedding request failed."
                )

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < self.max_attempts:
                self._log_retry(attempt, reason=f"http_{response.status_code}")
                self._sleep_before_retry(attempt)
                continue

            if response.status_code < 200 or response.status_code >= 300:
                logger.warning(
                    "embedding provider returned HTTP status %d on attempt %d",
                    response.status_code,
                    attempt,
                )
                raise EmbeddingProviderError(
                    f"SiliconFlow embedding request failed (HTTP {response.status_code})."
                )

            return self._parse_response(
                response,
                expected_count=len(batch),
                expected_dimension=getattr(self, "dimension", None),
            )

        # The finite range above always returns or raises.  Keep a defensive
        # branch in case the loop is changed later.
        raise EmbeddingProviderError("SiliconFlow embedding request failed.")

    def close(self) -> None:
        """Close the internally-created HTTP client, leaving injected clients alone."""

        if self._owns_http_client:
            self._http_client.close()

    def __enter__(self) -> "SiliconFlowEmbeddingClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    @staticmethod
    def _validate_texts(texts: Sequence[str]) -> list[str]:
        if isinstance(texts, (str, bytes, bytearray)):
            raise TypeError("texts 必须是字符串序列")
        try:
            batch = list(texts)
        except TypeError:
            batch = None
        if batch is None:
            raise TypeError("texts 必须是字符串序列")
        if not batch:
            raise ValueError("texts 至少需要一个待嵌入文本")
        if any(not isinstance(text, str) for text in batch):
            raise TypeError("texts 必须全部为字符串")
        return batch

    @staticmethod
    def _parse_response(
        response: httpx.Response,
        *,
        expected_count: int,
        expected_dimension: int | None = None,
    ) -> list[list[float]]:
        try:
            payload: Any = response.json()
        except Exception:
            payload = _INVALID_RESPONSE

        if payload is _INVALID_RESPONSE:
            raise EmbeddingProviderError("SiliconFlow embedding response was invalid.")

        if not isinstance(payload, dict):
            raise EmbeddingProviderError("SiliconFlow embedding response was invalid.")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise EmbeddingProviderError("SiliconFlow embedding response was invalid.")

        vectors_by_index: dict[int, list[float]] = {}
        response_dimension: int | None = None
        for item in data:
            if not isinstance(item, dict):
                raise EmbeddingProviderError("SiliconFlow embedding response was invalid.")
            index = item.get("index")
            embedding = item.get("embedding")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or index < 0
                or index >= expected_count
                or index in vectors_by_index
                or not isinstance(embedding, list)
                or not embedding
            ):
                raise EmbeddingProviderError("SiliconFlow embedding response was invalid.")

            vector: list[float] = []
            for value in embedding:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise EmbeddingProviderError(
                        "SiliconFlow embedding response was invalid."
                    )
                conversion_failed = False
                try:
                    numeric_value = float(value)
                except (OverflowError, ValueError, TypeError):
                    conversion_failed = True
                    numeric_value = 0.0
                if conversion_failed:
                    raise EmbeddingProviderError(
                        "SiliconFlow embedding response was invalid."
                    )
                if not math.isfinite(numeric_value):
                    raise EmbeddingProviderError(
                        "SiliconFlow embedding response was invalid."
                    )
                vector.append(numeric_value)

            if not vector:
                raise EmbeddingProviderError(
                    "SiliconFlow embedding response was invalid."
                )
            if expected_dimension is not None and len(vector) != expected_dimension:
                raise EmbeddingProviderError(
                    "SiliconFlow embedding response was invalid."
                )
            if response_dimension is None:
                response_dimension = len(vector)
            elif len(vector) != response_dimension:
                raise EmbeddingProviderError(
                    "SiliconFlow embedding response was invalid."
                )
            vectors_by_index[index] = vector

        if set(vectors_by_index) != set(range(expected_count)):
            raise EmbeddingProviderError("SiliconFlow embedding response was invalid.")
        return [vectors_by_index[index] for index in range(expected_count)]

    @staticmethod
    def _log_retry(attempt: int, *, reason: str) -> None:
        logger.warning(
            "retrying embedding provider request after attempt %d (%s)",
            attempt,
            reason,
        )

    @staticmethod
    def _sleep_before_retry(attempt: int) -> None:
        # Keep retry latency bounded and independent of provider response text.
        time.sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_DIMENSION",
    "DEFAULT_INDEX_VERSION",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "EmbeddingConfig",
    "EmbeddingClient",
    "EmbeddingConfigurationError",
    "EmbeddingProviderError",
    "parse_embedding_dimension",
    "resolve_embedding_config",
    "SiliconFlowEmbeddingClient",
]
