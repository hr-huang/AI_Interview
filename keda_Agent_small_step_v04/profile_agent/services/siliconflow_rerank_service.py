"""Secret-safe SiliconFlow reranker client for interview-question retrieval."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import os
from typing import Any

import httpx


DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"


class SiliconFlowRerankClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_RERANK_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("没有配置 SiliconFlow API key")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("reranker model 不能为空")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("reranker base URL 不能为空")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须为正数")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._http_client = http_client or httpx.Client(timeout=self.timeout_seconds)
        self._owns_http_client = http_client is None

    @classmethod
    def from_env(
        cls,
        *,
        env: Mapping[str, Any] | None = None,
        http_client: httpx.Client | None = None,
    ) -> "SiliconFlowRerankClient":
        environment = os.environ if env is None else env
        api_key = str(environment.get("SILICONFLOW_API_KEY", "")).strip()
        if not api_key:
            raise ValueError("没有配置 SILICONFLOW_API_KEY, 请先设置该环境变量")
        model = str(
            environment.get("QUESTION_RAG_RERANK_MODEL", DEFAULT_RERANK_MODEL)
        ).strip() or DEFAULT_RERANK_MODEL
        base_url = str(
            environment.get("SILICONFLOW_RERANK_BASE_URL", DEFAULT_BASE_URL)
        ).strip() or DEFAULT_BASE_URL
        return cls(
            api_key=api_key,
            model=model,
            base_url=base_url,
            http_client=http_client,
        )

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("rerank query 不能为空")
        if not isinstance(documents, Sequence) or isinstance(documents, (str, bytes)):
            raise TypeError("documents 必须是文本序列")
        batch = list(documents)
        if not batch or any(not isinstance(item, str) or not item.strip() for item in batch):
            raise ValueError("documents 至少需要一个非空文本")
        try:
            response = self._http_client.post(
                f"{self.base_url}/rerank",
                json={
                    "model": self.model,
                    "query": query.strip(),
                    "documents": batch,
                    "return_documents": False,
                    "top_n": len(batch),
                },
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
            )
        except Exception as error:
            raise RuntimeError("SiliconFlow rerank request failed.") from None
        if response.status_code != 200:
            raise RuntimeError("SiliconFlow rerank request failed.")
        try:
            payload = response.json()
            results = payload["results"]
            scores: list[float | None] = [None] * len(batch)
            for item in results:
                index = item["index"]
                score = float(item["relevance_score"])
                if isinstance(index, bool) or not isinstance(index, int):
                    raise ValueError
                if not 0 <= index < len(batch) or not math.isfinite(score):
                    raise ValueError
                if scores[index] is not None:
                    raise ValueError
                scores[index] = score
            if any(score is None for score in scores):
                raise ValueError
            return [float(score) for score in scores]
        except Exception:
            raise RuntimeError("SiliconFlow rerank response was invalid.") from None

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()


__all__ = ["DEFAULT_RERANK_MODEL", "SiliconFlowRerankClient"]
