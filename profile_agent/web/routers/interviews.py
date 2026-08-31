from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from profile_agent.model_runtime import ModelRuntimeUnavailableError
from profile_agent.web.interview_service import (
    AnswerRequest,
    InterviewService,
    InterviewStateError,
    StaleTurnError,
)

router = APIRouter()


def _service(request: Request) -> InterviewService:
    return InterviewService(request.app.state.container)


def _not_found(error: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail="候选人面试链接不存在")


def _model_unavailable(error: ModelRuntimeUnavailableError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(error))


@router.get("/interviews/{token}")
def get_interview(token: str, request: Request) -> dict[str, Any]:
    try:
        return _service(request).get_session(token)
    except KeyError as error:
        raise _not_found(error) from error
    except ModelRuntimeUnavailableError as error:
        raise _model_unavailable(error) from error
    except InterviewStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/interviews/{token}/start")
def start_interview(token: str, request: Request) -> dict[str, Any]:
    try:
        return _service(request).start(token)
    except KeyError as error:
        raise _not_found(error) from error
    except ModelRuntimeUnavailableError as error:
        raise _model_unavailable(error) from error
    except InterviewStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/interviews/{token}/answers", response_model=None)
def submit_answer(
    token: str,
    answer: AnswerRequest,
    request: Request,
) -> Any:
    service = _service(request)
    try:
        return service.answer(token, answer)
    except KeyError as error:
        raise _not_found(error) from error
    except StaleTurnError as error:
        content: dict[str, Any] = {
            "detail": str(error),
        }
        if error.public_state is not None:
            content["session"] = error.public_state
        return JSONResponse(status_code=409, content=content)
    except ModelRuntimeUnavailableError as error:
        raise _model_unavailable(error) from error
    except InterviewStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


__all__ = ["router"]
