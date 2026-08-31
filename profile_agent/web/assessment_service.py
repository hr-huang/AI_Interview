from __future__ import annotations

import hashlib
import secrets
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ValidationError

from profile_agent.llm import LLMProviderError
from profile_agent.model_runtime import ModelRuntimeRegistry
from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.services.plan_review_service import (
    PlanOverrideSet,
    freeze_reviewed_plan,
)
from profile_agent.web.container import WebContainer
from profile_agent.web.repository import SqliteAssessmentRepository
from profile_agent.web.schemas import (
    AssessmentRecord,
    AssessmentStatus,
    transition_assessment,
)


_PLAN_GUARDRAILS = {
    "allowed_duration_minutes": [30, 45, 60],
    "editable_target_fields": [
        "priority",
        "objective",
        "time_budget_minutes",
    ],
    "immutable_fields": [
        "id",
        "target_type",
        "competency_ids",
        "evidence_requirements",
        "related_claim_ids",
        "must_cover",
        "preferred_modes",
        "role_profile",
        "rubric",
        "scoring_weights",
    ],
}


class AssessmentConflictError(ValueError):
    """Raised when another request has already advanced an assessment."""


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def request_fingerprint(
    *,
    target_role: str,
    jd_text: str,
    resume_text: str,
    interview_duration_minutes: int = 45,
    model_session_id: str | None = None,
) -> str:
    payload = "\x1f".join(
        (
            target_role.strip(),
            jd_text.strip(),
            resume_text.strip(),
            str(interview_duration_minutes),
            (model_session_id or "").strip(),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AssessmentService:
    def __init__(self, container: WebContainer) -> None:
        self.container = container
        self.repository: SqliteAssessmentRepository = container.repository

    def _model_context(self, assessment_id: str):
        registry = getattr(self.container, "model_runtime_registry", None)
        if isinstance(registry, ModelRuntimeRegistry):
            return registry.use_for_assessment(assessment_id)
        return nullcontext()

    def analyze(self, assessment_id: str) -> AssessmentRecord:
        record = self.repository.get(assessment_id)
        if record.status not in {
            AssessmentStatus.DRAFT,
            AssessmentStatus.FAILED,
        }:
            return record
        if record.status is AssessmentStatus.FAILED and not record.retryable:
            raise ValueError("该评估不可重试")

        analyzing = transition_assessment(record, AssessmentStatus.ANALYZING)
        if not self.repository.save_if_version(analyzing, record.version):
            return self.repository.get(assessment_id)
        state = {
            "resume_text": analyzing.resume_text,
            "jd_text": analyzing.jd_text,
            "target_role": analyzing.target_role,
            "interview_duration_minutes": analyzing.interview_duration_minutes,
        }
        try:
            with self._model_context(assessment_id):
                output = self.container.pre_interview_graph.invoke(state)
            if not isinstance(output, dict):
                raise ValueError("Pre-Interview Graph 未返回有效 State")
            plan = InterviewPlan.model_validate(output.get("interview_plan"))
            serialized_state = jsonable(output)
            completed = analyzing.model_copy(
                update={
                    "status": AssessmentStatus.PLAN_REVIEW,
                    "updated_at": datetime.now(timezone.utc),
                    "version": analyzing.version + 1,
                    "pre_interview_state": serialized_state,
                    "original_plan": plan.model_dump(mode="json"),
                    "plan_overrides": None,
                    "preview_plan": None,
                    "final_plan": None,
                    "scoring_blueprint": None,
                    "failed_stage": None,
                    "error_message": None,
                    "retryable": False,
                }
            )
            if self.repository.save_if_version(completed, analyzing.version):
                return completed
            return self.repository.get(assessment_id)
        except (
            LLMProviderError,
            ValueError,
            TypeError,
            KeyError,
            ValidationError,
        ):
            failed = transition_assessment(
                analyzing,
                AssessmentStatus.FAILED,
            ).model_copy(
                update={
                    "failed_stage": "ANALYZING",
                    "error_message": "面试计划分析失败，请稍后重试。",
                    "retryable": True,
                }
            )
            if self.repository.save_if_version(failed, analyzing.version):
                return failed
            return self.repository.get(assessment_id)

    def get_plan(
        self,
        assessment_id: str,
    ) -> tuple[AssessmentRecord, dict[str, Any]]:
        record = self.repository.get(assessment_id)
        if record.original_plan is None:
            raise ValueError("评估计划尚未生成")
        preview = record.preview_plan or record.original_plan
        return record, {
            "assessment_id": record.id,
            "status": record.status.value,
            "original_plan": record.original_plan,
            "preview_plan": preview,
            "overrides": record.plan_overrides,
            "role_profile": self.container.role_profile.model_dump(
                mode="json"
            ),
            "guardrails": {
                key: list(value) for key, value in _PLAN_GUARDRAILS.items()
            },
        }

    def override_plan(
        self,
        assessment_id: str,
        overrides: PlanOverrideSet,
    ) -> tuple[AssessmentRecord, dict[str, Any]]:
        record = self.repository.get(assessment_id)
        self._require_status(record, AssessmentStatus.PLAN_REVIEW)
        original = self._original_plan(record)
        preview, _blueprint = freeze_reviewed_plan(
            original,
            overrides,
            self.container.role_profile,
        )
        updated = record.model_copy(
            update={
                "plan_overrides": overrides.model_dump(mode="json"),
                "preview_plan": preview.model_dump(mode="json"),
                "updated_at": datetime.now(timezone.utc),
                "version": record.version + 1,
            }
        )
        if not self.repository.save_if_version(updated, record.version):
            raise AssessmentConflictError(
                "评估已被其他请求更新，请刷新后重试"
            )
        return updated, self.get_plan(assessment_id)[1]

    def freeze_plan(self, assessment_id: str) -> tuple[AssessmentRecord, str]:
        record = self.repository.get(assessment_id)
        self._require_status(record, AssessmentStatus.PLAN_REVIEW)
        original = self._original_plan(record)
        overrides = PlanOverrideSet.model_validate(record.plan_overrides or {})
        final_plan, blueprint = freeze_reviewed_plan(
            original,
            overrides,
            self.container.role_profile,
        )

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        final_dump = final_plan.model_dump(mode="json")
        ready = transition_assessment(
            record.model_copy(
                update={
                    "final_plan": final_dump,
                    "preview_plan": final_dump,
                    "scoring_blueprint": blueprint.model_dump(mode="json"),
                    "candidate_token_hash": token_hash,
                }
            ),
            AssessmentStatus.READY,
        )
        if not self.repository.save_if_version(ready, record.version):
            raise AssessmentConflictError(
                "评估已被其他请求更新，请刷新后重试"
            )
        return ready, raw_token

    def retry(self, assessment_id: str) -> AssessmentRecord:
        record = self.repository.get(assessment_id)
        if record.status is not AssessmentStatus.FAILED or not record.retryable:
            raise ValueError("当前评估不可重试")
        return self.analyze(assessment_id)

    @staticmethod
    def _require_status(
        record: AssessmentRecord,
        expected: AssessmentStatus,
    ) -> None:
        if record.status is not expected:
            raise ValueError(
                f"当前评估状态不允许此操作: {record.status.value}"
            )

    @staticmethod
    def _original_plan(record: AssessmentRecord) -> InterviewPlan:
        if record.original_plan is None:
            raise ValueError("评估计划尚未生成")
        return InterviewPlan.model_validate(record.original_plan)
