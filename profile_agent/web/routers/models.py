from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from profile_agent.llm import LLM, LLMProviderError
from profile_agent.model_runtime import ModelRuntimeConfig, ModelRuntimeRegistry, use_model_runtime_config

router = APIRouter()


class ModelProbe(BaseModel):
    ok: bool
    message: str


class ModelSessionRequest(ModelRuntimeConfig):
    pass


def _registry(request: Request) -> ModelRuntimeRegistry:
    registry = getattr(request.app.state.container, "model_runtime_registry", None)
    if not isinstance(registry, ModelRuntimeRegistry):
        raise RuntimeError("model runtime registry 未初始化")
    return registry


@router.post("/model-sessions")
def create_model_session(
    payload: ModelSessionRequest,
    request: Request,
) -> dict[str, str]:
    config = ModelRuntimeConfig.model_validate(payload)
    try:
        with use_model_runtime_config(config):
            result = LLM().structured(
                [
                    (
                        "system",
                        "你正在执行模型兼容性测试。只返回 JSON。",
                    ),
                    (
                        "human",
                        '请返回 {"ok": true, "message": "compatible"}。',
                    ),
                ],
                ModelProbe,
            )
    except (LLMProviderError, ValueError, TypeError, OSError) as error:
        raise HTTPException(
            status_code=422,
            detail=f"模型连接或结构化输出测试失败：{error}",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=422,
            detail="模型连接或结构化输出测试失败，请检查 Base URL、Key 与模型名。",
        ) from error
    if not result.ok:
        raise HTTPException(status_code=422, detail="模型未通过结构化输出兼容性测试")

    session_id = _registry(request).create_session(config)
    return _registry(request).public_session(session_id)


__all__ = ["router"]
