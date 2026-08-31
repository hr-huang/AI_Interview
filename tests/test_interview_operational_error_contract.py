from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from httpx import Request
from openai import APIConnectionError

from profile_agent.llm import LLMProviderError
from profile_agent.web.interview_service import InterviewService
from profile_agent.web.routers.interviews import router, start_interview


_SAFE_DETAIL = "面试模型服务暂时不可用，请稍后重试；若持续失败，请联系评估发起方检查模型配置。"


def _client() -> TestClient:
    app = FastAPI()
    app.state.container = object()
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_direct_handler_classifies_provider_connection_failure() -> None:
    error = APIConnectionError(
        request=Request("POST", "https://provider.example/v1/chat/completions")
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(container=object()))
    )
    with patch.object(InterviewService, "start", side_effect=error):
        try:
            start_interview("candidate-token", request)  # type: ignore[arg-type]
        except HTTPException as exc:
            assert exc.status_code == 503
            assert exc.detail == _SAFE_DETAIL
        else:
            raise AssertionError("provider failure must map to HTTPException 503")


def test_start_maps_provider_connection_failure_to_safe_503() -> None:
    error = APIConnectionError(
        request=Request("POST", "https://provider.example/v1/chat/completions")
    )
    with _client() as client, patch.object(
        InterviewService,
        "start",
        side_effect=error,
    ):
        response = client.post("/api/interviews/candidate-token/start")

    assert response.status_code == 503
    assert response.json() == {"detail": _SAFE_DETAIL}
    assert "provider.example" not in response.text


def test_answer_maps_normalized_provider_failure_to_safe_503() -> None:
    with _client() as client, patch.object(
        InterviewService,
        "answer",
        side_effect=LLMProviderError("sensitive provider account detail"),
    ):
        response = client.post(
            "/api/interviews/candidate-token/answers",
            json={
                "turn_id": "turn_001",
                "answer": "我的回答",
                "idempotency_key": "answer-001",
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": _SAFE_DETAIL}
    assert "sensitive provider account detail" not in response.text


def test_unexpected_programming_error_is_not_disguised_as_provider_outage() -> None:
    with _client() as client, patch.object(
        InterviewService,
        "start",
        side_effect=RuntimeError("unexpected invariant failure"),
    ):
        response = client.post("/api/interviews/candidate-token/start")

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
