from __future__ import annotations

import unittest

import httpx

from profile_agent.services.siliconflow_rerank_service import (
    SiliconFlowRerankClient,
)


class SiliconFlowRerankClientTests(unittest.TestCase):
    def test_posts_documents_and_restores_scores_to_input_order(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/rerank")
            self.assertEqual(request.headers["authorization"], "Bearer test-key")
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 1, "relevance_score": 0.93},
                        {"index": 0, "relevance_score": 0.07},
                    ]
                },
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        client = SiliconFlowRerankClient(api_key="test-key", http_client=http_client)
        try:
            scores = client.rerank("并发连接限制", ["RAG回归", "连接池限流"])
        finally:
            client.close()

        self.assertEqual(scores, [0.07, 0.93])

    def test_rejects_incomplete_provider_results(self) -> None:
        http_client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"results": [{"index": 0, "relevance_score": 0.5}]},
                )
            )
        )
        client = SiliconFlowRerankClient(api_key="test-key", http_client=http_client)
        try:
            with self.assertRaises(RuntimeError):
                client.rerank("query", ["one", "two"])
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
