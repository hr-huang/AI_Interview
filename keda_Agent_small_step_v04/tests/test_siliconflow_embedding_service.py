from __future__ import annotations

import io
import logging
import os
import unittest
from unittest.mock import patch

import httpx

from profile_agent.services.siliconflow_embedding_service import (
    EmbeddingProviderError,
    SiliconFlowEmbeddingClient,
)


class SiliconFlowEmbeddingClientTests(unittest.TestCase):
    def _client(self, handler, **kwargs) -> tuple[SiliconFlowEmbeddingClient, list[httpx.Request]]:
        requests: list[httpx.Request] = []

        def recording_handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        transport = httpx.MockTransport(recording_handler)
        client = httpx.Client(transport=transport)
        return (
            SiliconFlowEmbeddingClient(
                api_key="test-siliconflow-key",
                http_client=client,
                **kwargs,
            ),
            requests,
        )

    def test_posts_one_batch_to_embeddings_with_default_model(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1/embeddings")
            self.assertEqual(request.headers["authorization"], "Bearer test-siliconflow-key")
            self.assertEqual(
                request.read(),
                b'{"model":"BAAI/bge-m3","input":["first","second"]}',
            )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 0, "embedding": [0.1, 0.2]},
                        {"index": 1, "embedding": [0.3, 0.4]},
                    ]
                },
            )

        client, requests = self._client(handler, base_url="https://api.siliconflow.cn/v1")
        try:
            self.assertEqual(client.embed(["first", "second"]), [[0.1, 0.2], [0.3, 0.4]])
        finally:
            client.close()

        self.assertEqual(len(requests), 1)

    def test_orders_vectors_by_response_index(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 2, "embedding": [2.0]},
                        {"index": 0, "embedding": [0.0]},
                        {"index": 1, "embedding": [1.0]},
                    ]
                },
            )

        client, _ = self._client(handler)
        try:
            self.assertEqual(client.embed(["zero", "one", "two"]), [[0.0], [1.0], [2.0]])
        finally:
            client.close()

    def test_rejects_embeddings_with_inconsistent_dimensions(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 0, "embedding": [0.1, 0.2]},
                        {"index": 1, "embedding": [0.3]},
                    ]
                },
            )

        client, _ = self._client(handler)
        try:
            with self.assertRaises(EmbeddingProviderError) as raised:
                client.embed(["first", "second"])
        finally:
            client.close()

        self.assertNotIn("0.1", str(raised.exception))

    def test_rejects_empty_embedding_vector(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": []}]},
            )

        client, _ = self._client(handler)
        try:
            with self.assertRaises(EmbeddingProviderError) as raised:
                client.embed(["text"])
        finally:
            client.close()

        self.assertEqual(
            str(raised.exception),
            "SiliconFlow embedding response was invalid.",
        )

    def test_rejects_non_numeric_embedding_values_without_echoing_value(self) -> None:
        secret_value = "candidate-secret-value"

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "embedding": [0.1, secret_value]}]},
            )

        client, _ = self._client(handler)
        try:
            with self.assertRaises(EmbeddingProviderError) as raised:
                client.embed(["text"])
        finally:
            client.close()

        self.assertNotIn(secret_value, str(raised.exception))

    def test_rejects_empty_batch_before_http_call(self) -> None:
        def forbidden_handler(_: httpx.Request) -> httpx.Response:
            self.fail("empty input must not make an HTTP request")

        client, _ = self._client(forbidden_handler)
        try:
            with self.assertRaisesRegex(ValueError, "至少需要一个"):
                client.embed([])
        finally:
            client.close()

    def test_from_env_requires_api_key_without_exposing_a_value(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SILICONFLOW_API_KEY": "",
                "SILICONFLOW_EMBEDDING_MODEL": "",
                "SILICONFLOW_EMBEDDING_BASE_URL": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "SILICONFLOW_API_KEY") as raised:
                SiliconFlowEmbeddingClient.from_env()

        self.assertNotIn("test-siliconflow-key", str(raised.exception))

    def test_from_env_uses_provider_defaults_and_overrides(self) -> None:
        fake_client = object()
        with patch.dict(
            os.environ,
            {
                "SILICONFLOW_API_KEY": "env-key",
                "SILICONFLOW_EMBEDDING_MODEL": "custom-model",
                "SILICONFLOW_EMBEDDING_BASE_URL": "https://embedding.example/v1",
            },
            clear=False,
        ):
            client = SiliconFlowEmbeddingClient.from_env(http_client=fake_client)  # type: ignore[arg-type]

        self.assertEqual(client.api_key, "env-key")
        self.assertEqual(client.model, "custom-model")
        self.assertEqual(client.base_url, "https://embedding.example/v1")

    def test_retries_429_once_with_bounded_attempts(self) -> None:
        statuses = iter([429, 200])

        def handler(_: httpx.Request) -> httpx.Response:
            status = next(statuses)
            return httpx.Response(status, json={"data": [{"index": 0, "embedding": [1.0]}]})

        client, requests = self._client(handler, max_attempts=2)
        try:
            with (
                patch("profile_agent.services.siliconflow_embedding_service.time.sleep") as sleep,
                self.assertLogs(
                    "profile_agent.services.siliconflow_embedding_service",
                    level="WARNING",
                ),
            ):
                self.assertEqual(client.embed(["retry-me"]), [[1.0]])
        finally:
            client.close()

        self.assertEqual(len(requests), 2)
        sleep.assert_called_once()

    def test_retries_5xx_and_stops_at_configured_attempt_limit(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(503, text="provider body contains test-siliconflow-key")

        transport = httpx.MockTransport(handler)
        raw_client = httpx.Client(transport=transport)
        client = SiliconFlowEmbeddingClient(
            api_key="test-siliconflow-key",
            http_client=raw_client,
            max_attempts=3,
        )
        try:
            with (
                patch("profile_agent.services.siliconflow_embedding_service.time.sleep"),
                self.assertLogs(
                    "profile_agent.services.siliconflow_embedding_service",
                    level="WARNING",
                ),
            ):
                with self.assertRaises(EmbeddingProviderError) as raised:
                    client.embed(["private candidate text"])
        finally:
            client.close()

        self.assertEqual(len(requests), 3)
        self.assertNotIn("test-siliconflow-key", str(raised.exception))
        self.assertNotIn("private candidate text", str(raised.exception))
        self.assertNotIn("provider body", str(raised.exception))

    def test_network_error_and_logs_never_include_key_or_input(self) -> None:
        secret = "test-siliconflow-key"
        candidate_text = "candidate private text"

        def handler(_: httpx.Request) -> httpx.Response:
            raise RuntimeError(f"transport failed with {secret} for {candidate_text}")

        transport = httpx.MockTransport(handler)
        raw_client = httpx.Client(transport=transport)
        client = SiliconFlowEmbeddingClient(api_key=secret, http_client=raw_client)
        log_stream = io.StringIO()
        log_handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger("profile_agent.services.siliconflow_embedding_service")
        logger.addHandler(log_handler)
        logger.setLevel(logging.WARNING)
        try:
            with self.assertRaises(EmbeddingProviderError) as raised:
                client.embed([candidate_text])
        finally:
            client.close()
            logger.removeHandler(log_handler)

        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(candidate_text, str(raised.exception))
        self.assertNotIn(secret, log_stream.getvalue())
        self.assertNotIn(candidate_text, log_stream.getvalue())

    def test_non_transport_exception_is_wrapped_without_retrying(self) -> None:
        secret = "programming-secret-value"
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            raise ValueError(f"adapter bug: {secret}")

        raw_client = httpx.Client(transport=httpx.MockTransport(handler))
        client = SiliconFlowEmbeddingClient(
            api_key="test-siliconflow-key",
            http_client=raw_client,
            max_attempts=3,
        )
        try:
            with (
                patch("profile_agent.services.siliconflow_embedding_service.time.sleep") as sleep,
                self.assertLogs(
                    "profile_agent.services.siliconflow_embedding_service",
                    level="WARNING",
                ),
            ):
                with self.assertRaises(EmbeddingProviderError) as raised:
                    client.embed(["text"])
        finally:
            client.close()

        self.assertEqual(len(requests), 1)
        sleep.assert_not_called()
        self.assertNotIn(secret, str(raised.exception))

    def test_rejects_malformed_response_without_echoing_response_body(self) -> None:
        secret_body = "response contains test-siliconflow-key"

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=secret_body)

        client, _ = self._client(handler)
        try:
            with self.assertRaises(EmbeddingProviderError) as raised:
                client.embed(["text"])
        finally:
            client.close()

        self.assertNotIn(secret_body, str(raised.exception))
        self.assertNotIn("test-siliconflow-key", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
