from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from profile_agent.model_runtime import ModelRuntimeRegistry
from profile_agent.schemas.report_schema import AssessmentReport
from profile_agent.services.plan_review_service import PlanOverrideSet
from profile_agent.web.assessment_service import (
    AssessmentService,
    AssessmentConflictError,
    request_fingerprint,
)
from profile_agent.web.document_ingestion import MAX_FILE_BYTES
from profile_agent.web.report_view import ReportViewModel, build_report_view
from profile_agent.web.schemas import AssessmentRecord, AssessmentStatus

router = APIRouter()


def _container(request: Request):
    return request.app.state.container


def _not_found(error: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail="评估不存在")


def _status_payload(record: AssessmentRecord) -> dict[str, Any]:
    return {
        "assessment_id": record.id,
        "status": record.status.value,
        "target_role": record.target_role,
        "retryable": record.retryable,
        "failed_stage": record.failed_stage,
        "error_message": record.error_message,
        "has_plan": record.original_plan is not None,
        "has_final_plan": record.final_plan is not None,
    }


def _checkpoint_values(container: Any, assessment_id: str) -> dict[str, Any]:
    graph = getattr(container, "interview_graph", None)
    if graph is None:
        return {}
    lock = getattr(container, "interview_lock", None)
    if lock is None:
        return {}
    try:
        with lock:
            snapshot = graph.get_state(
                {"configurable": {"thread_id": assessment_id}}
            )
    except (AttributeError, KeyError, RuntimeError):
        return {}
    if isinstance(snapshot, Mapping):
        values = snapshot.get("values", snapshot)
    else:
        values = getattr(snapshot, "values", {})
    if isinstance(values, BaseModel):
        values = values.model_dump(mode="python")
    return dict(values) if isinstance(values, Mapping) else {}


@router.post("/assessments", status_code=202)
async def create_assessment(
    request: Request,
    target_role: str = Form(...),
    jd_text: str = Form(...),
    resume_text: str | None = Form(default=None),
    resume_file: UploadFile | None = File(default=None),
    idempotency_key: str = Form(...),
    interview_duration_minutes: int = Form(default=45),
    model_session_id: str | None = Form(default=None),
) -> dict[str, Any]:
    if not target_role.strip() or not jd_text.strip():
        raise HTTPException(status_code=422, detail="岗位和 JD 不能为空")
    if not idempotency_key.strip():
        raise HTTPException(status_code=422, detail="缺少幂等键")
    if interview_duration_minutes not in {30, 45, 60}:
        raise HTTPException(status_code=422, detail="面试时长只能是 30、45 或 60 分钟")

    has_resume_text = bool(resume_text and resume_text.strip())
    has_resume_file = resume_file is not None
    if has_resume_text == has_resume_file:
        raise HTTPException(
            status_code=422,
            detail="resume_text 和 resume_file 必须二选一",
        )

    container = _container(request)
    registry = getattr(container, "model_runtime_registry", None)
    clean_model_session_id = (model_session_id or "").strip() or None
    if clean_model_session_id:
        if not isinstance(registry, ModelRuntimeRegistry):
            raise HTTPException(status_code=503, detail="模型配置服务不可用")
        try:
            registry.public_session(clean_model_session_id)
        except KeyError as error:
            raise HTTPException(status_code=422, detail="模型配置会话不存在或已失效") from error

    if has_resume_file:
        assert resume_file is not None
        try:
            extracted = container.document_extractor.extract(
                resume_file.filename or "resume",
                await resume_file.read(MAX_FILE_BYTES + 1),
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        cleaned_resume = extracted.text
    else:
        cleaned_resume = resume_text.strip()  # type: ignore[union-attr]

    fingerprint = request_fingerprint(
        target_role=target_role,
        jd_text=jd_text,
        resume_text=cleaned_resume,
        interview_duration_minutes=interview_duration_minutes,
        model_session_id=clean_model_session_id,
    )
    repository = container.repository
    key = idempotency_key.strip()
    record = AssessmentRecord.new(
        assessment_id=f"ast_{uuid.uuid4().hex}",
        target_role=target_role,
        jd_text=jd_text,
        resume_text=cleaned_resume,
        interview_duration_minutes=interview_duration_minutes,
        model_session_id=clean_model_session_id,
    )
    created, binding = repository.create_with_request(
        record,
        key,
        fingerprint,
    )
    if not created:
        old_fingerprint, existing_id = binding
        if old_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="幂等键已用于另一份评估输入",
            )
        return {
            "assessment_id": existing_id,
            "status": repository.get(existing_id).status.value,
        }

    if clean_model_session_id and isinstance(registry, ModelRuntimeRegistry):
        registry.bind_assessment(record.id, clean_model_session_id)

    service = AssessmentService(container)
    container.dispatcher.submit(service.analyze, record.id)
    return {
        "assessment_id": record.id,
        "status": repository.get(record.id).status.value,
    }


@router.get("/assessments/{assessment_id}")
def get_assessment(assessment_id: str, request: Request) -> dict[str, Any]:
    try:
        record = _container(request).repository.get(assessment_id)
    except KeyError as error:
        raise _not_found(error) from error
    return _status_payload(record)


@router.get("/assessments/{assessment_id}/plan")
def get_plan(assessment_id: str, request: Request) -> dict[str, Any]:
    service = AssessmentService(_container(request))
    try:
        _record, payload = service.get_plan(assessment_id)
    except KeyError as error:
        raise _not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return payload


@router.get(
    "/assessments/{assessment_id}/report",
    response_model=ReportViewModel,
)
def get_report(assessment_id: str, request: Request) -> ReportViewModel:
    container = _container(request)
    try:
        record = container.repository.get(assessment_id)
    except KeyError as error:
        raise _not_found(error) from error

    if record.status is not AssessmentStatus.COMPLETE:
        raise HTTPException(status_code=409, detail="评估报告尚未完成")
    if record.report is None:
        raise HTTPException(status_code=409, detail="评估报告尚未生成")
    plan_payload = record.final_plan or record.preview_plan or record.original_plan
    if plan_payload is None:
        raise HTTPException(status_code=409, detail="评估计划尚未冻结")

    values = _checkpoint_values(container, assessment_id)
    turns = values.get("interview_turns")
    evidences = values.get("evidences")
    if not isinstance(turns, list) or not turns:
        raise HTTPException(status_code=409, detail="报告证据链尚未可读")
    if not isinstance(evidences, list):
        raise HTTPException(status_code=409, detail="报告证据链尚未可读")

    try:
        return build_report_view(
            AssessmentReport.model_validate(record.report),
            plan_payload,
            turns,
            evidences,
            container.role_profile,
            demo=False,
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.put("/assessments/{assessment_id}/plan-overrides")
def override_plan(
    assessment_id: str,
    overrides: PlanOverrideSet,
    request: Request,
) -> dict[str, Any]:
    service = AssessmentService(_container(request))
    try:
        _record, payload = service.override_plan(assessment_id, overrides)
    except KeyError as error:
        raise _not_found(error) from error
    except AssessmentConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return payload


@router.post("/assessments/{assessment_id}/freeze")
def freeze_plan(assessment_id: str, request: Request) -> dict[str, Any]:
    service = AssessmentService(_container(request))
    try:
        _record, raw_token = service.freeze_plan(assessment_id)
    except KeyError as error:
        raise _not_found(error) from error
    except AssessmentConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "assessment_id": assessment_id,
        "status": AssessmentStatus.READY.value,
        "candidate_url": f"/interviews/{raw_token}",
    }


@router.post("/assessments/{assessment_id}/retry", status_code=202)
def retry_analysis(assessment_id: str, request: Request) -> dict[str, Any]:
    container = _container(request)
    try:
        record = container.repository.get(assessment_id)
    except KeyError as error:
        raise _not_found(error) from error
    if record.status is not AssessmentStatus.FAILED or not record.retryable:
        raise HTTPException(status_code=409, detail="当前评估不可重试")
    service = AssessmentService(container)
    container.dispatcher.submit(service.retry, assessment_id)
    return {
        "assessment_id": assessment_id,
        "status": container.repository.get(assessment_id).status.value,
    }
