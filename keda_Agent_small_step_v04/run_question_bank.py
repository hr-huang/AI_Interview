"""Safely validate, audit, and manage the disposable question-bank index.

The JSON bank is authoritative.  This command only constructs the paid
embedding client and the local Qdrant store for an explicit ``--apply`` on a
``rebuild`` or ``sync`` action.  Validation and audit remain read-only, and a
dry-run never needs an API key.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import inspect
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any

from profile_agent.knowledge.qdrant_question_store import (
    COLLECTION_NAME,
    IndexFingerprint,
    QdrantQuestionStore,
)
from profile_agent.schemas.question_rag_schema import InterviewQuestionRecord
from profile_agent.services.question_bank_service import (
    SUPPORTED_ROLE,
    audit_question_bank,
    load_question_bank,
)
from profile_agent.services.siliconflow_embedding_service import (
    SiliconFlowEmbeddingClient,
)


DEFAULT_MODEL = "BAAI/bge-m3"
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_PROVIDER = "siliconflow"
DEFAULT_INDEX_VERSION = "question-bank.v1"
DEFAULT_INDEX_PATH = Path("data/qdrant-question-index")
DEFAULT_EXPIRING_WITHIN_DAYS = 30

EXIT_OK = 0
EXIT_OPERATION_ERROR = 1
EXIT_USAGE_ERROR = 2


class QuestionBankValidationError(ValueError):
    """A bank failed before any external dependency can be constructed."""


class QuestionBankConfigurationError(ValueError):
    """The command cannot safely perform the requested apply operation."""


@dataclass(frozen=True)
class QuestionBankDependencies:
    """Optional seams used by tests and offline rehearsals.

    ``bank_loader`` and ``auditor`` are pure functions.  Factories are only
    consulted by an applying rebuild/sync, never by validate, audit, or a
    dry-run.  A caller supplying a synthetic fixture must also provide an
    explicit ``test_dependency``; production defaults reject such records.
    """

    bank_loader: Callable[..., Sequence[InterviewQuestionRecord]] = load_question_bank
    auditor: Callable[..., Any] = audit_question_bank
    embedding_factory: Callable[..., Any] | None = None
    store_factory: Callable[..., Any] | None = None
    fingerprint_factory: Callable[..., Any] | None = None
    env: Mapping[str, str] | None = None
    now_provider: Callable[[], date] | None = None
    test_dependency: object | None = None


class _DependencyView:
    """Resolve mapping/dataclass/object dependency injection without logging it."""

    def __init__(self, *sources: object | None) -> None:
        self._values: dict[str, Any] = {}
        for source in sources:
            if source is None:
                continue
            if isinstance(source, Mapping):
                self._values.update(source)
                continue
            for name in (
                "bank_loader",
                "load_question_bank",
                "loader",
                "load_bank",
                "auditor",
                "audit_fn",
                "audit_question_bank",
                "embedding_factory",
                "create_embedding_client",
                "embedding_client_factory",
                "embedder_factory",
                "embedding_client",
                "embedding",
                "store_factory",
                "create_store",
                "qdrant_factory",
                "qdrant_store_factory",
                "question_store",
                "store",
                "fingerprint_factory",
                "build_fingerprint",
                "env",
                "environment",
                "now_provider",
                "today_provider",
                "clock",
                "test_dependency",
            ):
                if hasattr(source, name):
                    self._values[name] = getattr(source, name)

    def get(self, *names: str, default: Any = None) -> Any:
        for name in names:
            if name in self._values:
                return self._values[name]
        return default


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="管理版本化面试题库与可重建的 Qdrant 索引"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    for action in ("validate", "audit", "rebuild", "sync"):
        subparser = subparsers.add_parser(action, help=f"执行 {action} 操作")
        subparser.add_argument(
            "--bank",
            type=Path,
            required=True,
            help="版本化 JSON 题库路径（JSON 是唯一事实源）",
        )
        subparser.add_argument(
            "--as-of",
            "--today",
            dest="as_of",
            type=_parse_date,
            default=None,
            help="生命周期判断日期，格式 YYYY-MM-DD；默认使用今天",
        )
        subparser.add_argument(
            "--format",
            choices=("human", "json"),
            default="human",
            help="摘要格式，默认 human；json 适合脚本消费",
        )
        subparser.add_argument(
            "--json",
            action="store_true",
            help="--format json 的简写",
        )
        if action == "audit":
            subparser.add_argument(
                "--expiring-within-days",
                type=_parse_non_negative_int,
                default=DEFAULT_EXPIRING_WITHIN_DAYS,
                help="提前多少天标记即将过期，默认 30",
            )
        if action in {"rebuild", "sync"}:
            subparser.add_argument(
                "--apply",
                action="store_true",
                help="确认执行 embedding 与 Qdrant 写入；默认仅 dry-run",
            )
            subparser.add_argument(
                "--index-path",
                "--qdrant-path",
                dest="index_path",
                type=Path,
                default=None,
                help="本地 Qdrant 路径；默认读取 QUESTION_RAG_INDEX_PATH",
            )
            subparser.add_argument(
                "--model",
                default=None,
                help="覆盖 SILICONFLOW_EMBEDDING_MODEL（仅 apply 使用）",
            )
            subparser.add_argument(
                "--index-version",
                default=None,
                help="覆盖 QUESTION_RAG_INDEX_VERSION",
            )
    return parser


def _parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("日期必须为 YYYY-MM-DD") from exc
    return parsed


def _parse_non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("天数必须为非负整数") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("天数必须为非负整数")
    return parsed


def _now_provider(value: Callable[[], date] | None) -> Callable[[], date]:
    if value is None:
        return date.today
    if not callable(value):
        raise TypeError("now_provider must be callable")
    return value


def _resolve_today(
    requested: date | None,
    provider: Callable[[], date],
) -> date:
    if requested is not None:
        result = requested
    else:
        result = provider()
    if isinstance(result, datetime) or not isinstance(result, date):
        raise QuestionBankConfigurationError("as-of date must be a date")
    return result


def _env_value(env: Mapping[str, str], name: str, default: str = "") -> str:
    value = env.get(name, default)
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value).strip()
    return value.strip()


def _normalise_records(value: Any, *, test_dependency: object | None) -> list[InterviewQuestionRecord]:
    if isinstance(value, (str, bytes, bytearray)):
        raise QuestionBankValidationError("question bank loader returned invalid records")
    try:
        raw_records = list(value)
    except TypeError as exc:
        raise QuestionBankValidationError("question bank loader returned invalid records") from exc

    records: list[InterviewQuestionRecord] = []
    for _index, raw_record in enumerate(raw_records):
        if isinstance(raw_record, InterviewQuestionRecord):
            record = raw_record
        elif isinstance(raw_record, Mapping):
            try:
                record = InterviewQuestionRecord.model_validate(raw_record)
            except Exception as exc:
                raise QuestionBankValidationError(
                    "question bank loader returned an invalid record"
                ) from exc
        else:
            raise QuestionBankValidationError("question bank loader returned invalid records")
        if (
            record.source_type == "test_only_synthetic"
            and test_dependency is None
        ):
            raise QuestionBankValidationError(
                "test-only synthetic records require an explicit test dependency"
            )
        records.append(record)
    return records


def _call_loader(
    loader: Callable[..., Any],
    path: Path,
    *,
    test_dependency: object | None,
) -> list[InterviewQuestionRecord]:
    # Keep a caller-provided loader's ordinary ``loader(path)`` contract
    # intact.  The canonical loader receives its explicit synthetic-fixture
    # guard; an injected loader may opt into the same keywords when a test
    # dependency is deliberately supplied.
    kwargs: dict[str, Any] = {}
    if loader is load_question_bank or test_dependency is not None:
        kwargs = {
            "allow_test_only": test_dependency is not None,
            "test_dependency": test_dependency,
        }
    try:
        loaded = _call_with_supported_kwargs(loader, (path,), kwargs)
    except QuestionBankValidationError:
        raise
    except Exception as exc:
        # The canonical loader may include Pydantic input representations in
        # its exception text.  Keep that detail out of the CLI boundary.
        raise QuestionBankValidationError("question bank validation failed") from exc
    return _normalise_records(loaded, test_dependency=test_dependency)


def _call_with_supported_kwargs(
    function: Callable[..., Any],
    positional: tuple[Any, ...],
    kwargs: Mapping[str, Any],
) -> Any:
    """Call an injected seam without forcing a particular test double shape."""

    if not callable(function):
        raise TypeError("injected dependency must be callable")
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(*positional)

    parameters = signature.parameters
    accepts_var_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_var_kwargs:
        selected_kwargs = dict(kwargs)
    else:
        selected_kwargs = {
            name: value for name, value in kwargs.items() if name in parameters
        }
    return function(*positional, **selected_kwargs)


def _call_auditor(
    auditor: Callable[..., Any],
    records: Sequence[InterviewQuestionRecord],
    *,
    as_of: date,
    expiring_within_days: int,
) -> Any:
    # The canonical auditor rejects receiving both aliases at once.  Select
    # one spelling based on the injected callable's signature so lightweight
    # test doubles can use either API without changing the production call.
    kwargs: dict[str, Any] = {}
    try:
        parameters: Mapping[str, inspect.Parameter] = inspect.signature(auditor).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "as_of" in parameters:
        kwargs["as_of"] = as_of
    elif "today" in parameters:
        kwargs["today"] = as_of
    if "expiring_within_days" in parameters:
        kwargs["expiring_within_days"] = expiring_within_days
    elif "expiry_warning_days" in parameters:
        kwargs["expiry_warning_days"] = expiring_within_days
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        kwargs.setdefault("as_of", as_of)
        kwargs.setdefault("expiring_within_days", expiring_within_days)
    return _call_with_supported_kwargs(auditor, (records,), kwargs)


def _audit_value(report: Any, *names: str, default: Any = None) -> Any:
    if isinstance(report, Mapping):
        for name in names:
            if name in report:
                return report[name]
    for name in names:
        if hasattr(report, name):
            return getattr(report, name)
    return default


def _audit_ids(report: Any, *names: str) -> list[str]:
    value = _audit_value(report, *names, default=[])
    if isinstance(value, (str, bytes, bytearray)):
        return []
    try:
        return [str(item) for item in value]
    except TypeError:
        return []


def _record_count_by_status(records: Sequence[InterviewQuestionRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.status)
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _public_model(value: Any) -> str:
    """Keep provider metadata useful without echoing accidental credentials."""

    text = str(value).strip()
    if not text:
        return "unknown"
    text = re.sub(
        r"(?i)(?<![A-Za-z0-9])(?:sk[-_]|pk[-_]|akia|asia)[A-Za-z0-9_-]{10,}",
        "[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+", "[REDACTED]", text)
    return text[:120]


def _validate_records_for_embedding(
    records: Sequence[InterviewQuestionRecord],
) -> list[str]:
    if not records:
        raise QuestionBankConfigurationError(
            "question bank has no records to embed"
        )
    return [record.question_text for record in records]


def _normalise_vectors(raw: Any, *, expected_count: int) -> list[list[float]]:
    if isinstance(raw, (str, bytes, bytearray)):
        raise ValueError("embedding result must be a sequence")
    try:
        values = list(raw)
    except TypeError as exc:
        raise ValueError("embedding result must be a sequence") from exc

    # Accept the direct-vector shape used by a few small offline fakes when
    # there is one record, while keeping the provider contract list-of-vectors.
    if expected_count == 1 and values and all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in values
    ):
        values = [values]
    if len(values) != expected_count:
        raise ValueError("embedding result count does not match question count")

    vectors: list[list[float]] = []
    dimension: int | None = None
    for raw_vector in values:
        if isinstance(raw_vector, (str, bytes, bytearray)):
            raise ValueError("embedding vectors must be numeric sequences")
        try:
            vector_values = list(raw_vector)
        except TypeError as exc:
            raise ValueError("embedding vectors must be numeric sequences") from exc
        if not vector_values:
            raise ValueError("embedding vectors must not be empty")
        if dimension is None:
            dimension = len(vector_values)
        elif len(vector_values) != dimension:
            raise ValueError("embedding vectors must have one dimension")
        vector: list[float] = []
        for value in vector_values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("embedding vectors must contain finite numbers")
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError("embedding vectors must contain finite numbers")
            vector.append(numeric)
        vectors.append(vector)
    return vectors


def _embed(embedding: Any, texts: Sequence[str]) -> list[list[float]]:
    embed = getattr(embedding, "embed", None)
    if not callable(embed):
        if callable(embedding):
            raw = embedding(texts)
        else:
            raise TypeError("embedding dependency must provide embed")
    else:
        raw = embed(texts)
    return _normalise_vectors(raw, expected_count=len(texts))


def _build_fingerprint(
    *,
    embedding: Any,
    vectors: Sequence[Sequence[float]],
    configured_model: str,
    configured_index_version: str,
    fingerprint_factory: Callable[..., Any] | None,
) -> IndexFingerprint:
    provider = _public_model(
        getattr(embedding, "provider", DEFAULT_PROVIDER) or DEFAULT_PROVIDER
    )
    model = _public_model(
        getattr(embedding, "model", configured_model) or configured_model
    )
    dimension = len(vectors[0]) if vectors else 0
    if fingerprint_factory is not None:
        fingerprint = _call_with_supported_kwargs(
            fingerprint_factory,
            (),
            {
                "provider": provider,
                "model": model,
                "dimension": dimension,
                "index_version": configured_index_version,
            },
        )
    else:
        fingerprint = IndexFingerprint(
            provider=provider,
            model=model,
            dimension=dimension,
            index_version=configured_index_version,
        )
    if isinstance(fingerprint, IndexFingerprint):
        return fingerprint
    try:
        return IndexFingerprint.model_validate(fingerprint)
    except Exception as exc:
        raise ValueError("fingerprint dependency returned an invalid value") from exc


def _default_embedding_factory(
    *,
    api_key: str,
    model: str,
    base_url: str,
) -> SiliconFlowEmbeddingClient:
    return SiliconFlowEmbeddingClient(
        api_key=api_key,
        model=model,
        base_url=base_url,
    )


def _default_store_factory(
    *,
    index_path: Path,
    fingerprint: IndexFingerprint,
) -> QdrantQuestionStore:
    return QdrantQuestionStore(path=index_path, fingerprint=fingerprint)


def _close_owned(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        # Cleanup is best effort and must never replace the safe operation
        # outcome with an exception containing provider/backend details.
        return


def _summary_for_validation(
    action: str,
    records: Sequence[InterviewQuestionRecord],
) -> dict[str, Any]:
    return {
        "action": action,
        "status": "valid",
        "records": len(records),
        "role": SUPPORTED_ROLE,
        "role_version": _public_model(records[0].role_version) if records else None,
        "status_counts": _record_count_by_status(records),
    }


def _summary_for_audit(
    records: Sequence[InterviewQuestionRecord],
    report: Any,
    *,
    as_of: date,
) -> dict[str, Any]:
    categories = {
        "expired": ("expired_question_ids", "expired_ids", "expired"),
        "expiring_soon": (
            "expiring_soon_question_ids",
            "expiring_question_ids",
            "expiring_ids",
            "expiring",
        ),
        "needs_review": (
            "needs_review_question_ids",
            "needs_review_ids",
            "needs_review",
        ),
        "retired": ("retired_question_ids", "retired_ids", "retired"),
        "missing_source": (
            "missing_source_question_ids",
            "missing_source_ids",
            "missing_source",
        ),
        "invalid_source": (
            "invalid_source_question_ids",
            "invalid_source_ids",
            "invalid_source",
        ),
        "invalid_record": (
            "invalid_record_question_ids",
            "invalid_record_ids",
            "invalid_record",
        ),
        "inactive": ("inactive_question_ids",),
        "eligible": ("eligible_question_ids", "active_question_ids"),
    }
    summary: dict[str, Any] = {
        "action": "audit",
        "status": "ok",
        "as_of": as_of.isoformat(),
        "records": len(records),
        "role": SUPPORTED_ROLE,
        "status_counts": _record_count_by_status(records),
    }
    for key, names in categories.items():
        ids = _audit_ids(report, *names)
        summary[f"{key}_count"] = len(ids)
        summary[f"{key}_ids"] = ids
    warning_days = _audit_value(
        report,
        "expiring_within_days",
        "expiry_warning_days",
        default=DEFAULT_EXPIRING_WITHIN_DAYS,
    )
    if isinstance(warning_days, int) and not isinstance(warning_days, bool):
        summary["expiring_within_days"] = warning_days
    return summary


def _summary_for_dry_run(
    action: str,
    records: Sequence[InterviewQuestionRecord],
    report: Any,
    *,
    as_of: date,
    model: str,
    index_version: str,
) -> dict[str, Any]:
    eligible_ids = _audit_ids(report, "eligible_question_ids", "active_question_ids")
    question_writes = len(records) if action == "rebuild" else len(eligible_ids)
    return {
        "action": action,
        "status": "dry-run",
        "as_of": as_of.isoformat(),
        "records": len(records),
        "eligible_records": len(eligible_ids),
        "role": SUPPORTED_ROLE,
        "model": _public_model(model),
        "index_version": _public_model(index_version),
        "collection": COLLECTION_NAME,
        "expected_writes": {
            "manifest": 1,
            "question_points": question_writes,
        },
        "apply_required": True,
    }


def _summary_for_applied(
    action: str,
    records: Sequence[InterviewQuestionRecord],
    report: Any,
    fingerprint: IndexFingerprint,
    vectors: Sequence[Sequence[float]],
    *,
    as_of: date,
) -> dict[str, Any]:
    eligible_ids = _audit_ids(report, "eligible_question_ids", "active_question_ids")
    question_writes = len(records) if action == "rebuild" else len(eligible_ids)
    return {
        "action": action,
        "status": "applied",
        "as_of": as_of.isoformat(),
        "records": len(records),
        "eligible_records": len(eligible_ids),
        "vectors": len(vectors),
        "dimension": fingerprint.dimension,
        "role": SUPPORTED_ROLE,
        "provider": _public_model(fingerprint.provider),
        "model": _public_model(fingerprint.model),
        "index_version": _public_model(fingerprint.index_version),
        "collection": COLLECTION_NAME,
        "writes": {
            "manifest": 1,
            "question_points": question_writes,
        },
    }


def _render_summary(summary: Mapping[str, Any], *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(summary, ensure_ascii=False, sort_keys=True)
    action = str(summary.get("action", "operation")).upper()
    status = str(summary.get("status", "ok")).upper()
    prefix = action if action == "AUDIT" else status
    fields: list[str] = [f"{prefix} action={summary.get('action', 'operation')}"]
    for key in (
        "as_of",
        "records",
        "eligible_records",
        "vectors",
        "dimension",
        "role",
        "role_version",
        "model",
        "collection",
        "index_version",
    ):
        if key in summary and summary[key] is not None:
            fields.append(f"{key}={summary[key]}")
    if "status_counts" in summary:
        counts = summary["status_counts"]
        fields.append(
            "status_counts="
            + ",".join(f"{key}:{value}" for key, value in counts.items())
        )
    if summary.get("expected_writes") is not None:
        writes = summary["expected_writes"]
        fields.append(
            "expected_writes="
            + ",".join(f"{key}:{value}" for key, value in writes.items())
        )
    if summary.get("writes") is not None:
        writes = summary["writes"]
        fields.append(
            "writes=" + ",".join(f"{key}:{value}" for key, value in writes.items())
        )
    if summary.get("apply_required"):
        fields.append("apply_required=true")
    if action == "AUDIT":
        for key in (
            "expired",
            "expiring_soon",
            "needs_review",
            "retired",
            "missing_source",
            "invalid_source",
            "invalid_record",
            "inactive",
            "eligible",
        ):
            if f"{key}_count" in summary:
                ids = summary.get(f"{key}_ids", [])
                fields.append(
                    f"{key}={summary[f'{key}_count']}"
                    + (f"[{','.join(ids)}]" if ids else "")
                )
    return " ".join(fields)


def _safe_error_message(error: BaseException, *, category: str) -> str:
    """Return a useful but deliberately non-echoing error message."""

    # Inspect exception causes only for a small set of safe classification
    # words.  Never return the original text: Pydantic may echo bank input.
    details: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        details.append(str(current).lower())
        current = current.__cause__ or current.__context__
    detail = " ".join(details)
    if category == "bank":
        if "test_only" in detail or "test-only" in detail:
            return "question bank validation failed: production rejects test-only synthetic banks."
        if "duplicate question_id" in detail:
            return "question bank validation failed: duplicate question_id."
        if "duplicate content_hash" in detail:
            return "question bank validation failed: duplicate content_hash."
        if "content_hash mismatch" in detail:
            return "question bank validation failed: content_hash mismatch."
        if "invalid question bank json" in detail or "json" in detail:
            return "question bank validation failed: invalid JSON."
        if "root" in detail:
            return "question bank validation failed: invalid JSON root."
        if "source" in detail:
            return "question bank validation failed: invalid source metadata."
        if "dimension" in detail:
            return "question bank validation failed: unsupported dimension_id."
        if "role_version" in detail:
            return "question bank validation failed: role_version mismatch."
        return "question bank validation failed: check schema, lifecycle, source, and content hash."
    if category == "configuration":
        if "siliconflow_api_key" in detail or "api key" in detail:
            return "配置错误：未配置 SILICONFLOW_API_KEY；--apply 才会读取该变量。"
        return "配置错误：无法安全执行 apply。"
    if category == "argument":
        return "参数错误：请检查命令参数。"
    return f"操作失败：{type(error).__name__}。"


def _emit_error(
    error: BaseException,
    *,
    category: str,
    output_format: str,
    error_fn: Callable[[str], None],
) -> None:
    message = _safe_error_message(error, category=category)
    if output_format == "json":
        error_fn(
            json.dumps(
                {
                    "status": "error",
                    "error_category": category,
                    "message": message,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        error_fn(message)


def _resolve_injected_dependencies(
    dependencies: object | None,
    factories: object | None,
    *,
    bank_loader: Callable[..., Any] | None,
    auditor: Callable[..., Any] | None,
    embedding_factory: Callable[..., Any] | None,
    store_factory: Callable[..., Any] | None,
    fingerprint_factory: Callable[..., Any] | None,
    env: Mapping[str, str] | None,
    now_provider: Callable[[], date] | None,
    test_dependency: object | None,
) -> tuple[_DependencyView, bool, bool]:
    view = _DependencyView(dependencies, factories)
    resolved_bank_loader = bank_loader or view.get(
        "bank_loader",
        "load_question_bank",
        "loader",
        "load_bank",
        default=load_question_bank,
    )
    resolved_auditor = auditor or view.get(
        "auditor", "audit_fn", "audit_question_bank", default=audit_question_bank
    )
    resolved_embedding_factory = embedding_factory
    if resolved_embedding_factory is None:
        resolved_embedding_factory = view.get(
            "embedding_factory",
            "create_embedding_client",
            "embedding_client_factory",
            "embedder_factory",
            default=None,
        )
    resolved_store_factory = store_factory
    if resolved_store_factory is None:
        resolved_store_factory = view.get(
            "store_factory",
            "create_store",
            "qdrant_factory",
            "qdrant_store_factory",
            default=None,
        )
    resolved_fingerprint_factory = fingerprint_factory
    if resolved_fingerprint_factory is None:
        resolved_fingerprint_factory = view.get(
            "fingerprint_factory", "build_fingerprint", default=None
        )
    resolved_env = env if env is not None else view.get("env", "environment", default=None)
    resolved_now_provider = now_provider or view.get(
        "now_provider", "today_provider", "clock", default=None
    )
    resolved_test_dependency = (
        test_dependency
        if test_dependency is not None
        else view.get("test_dependency", default=None)
    )

    # An already-created fake is a dependency injection seam, not a production
    # factory.  Keep it unowned so the caller controls its lifecycle.
    if resolved_embedding_factory is None:
        injected_embedding = view.get("embedding_client", "embedding", default=None)
        if injected_embedding is not None:
            resolved_embedding_factory = lambda **_kwargs: injected_embedding
    if resolved_store_factory is None:
        injected_store = view.get("question_store", "store", default=None)
        if injected_store is not None:
            resolved_store_factory = lambda **_kwargs: injected_store

    view._values.update(
        {
            "bank_loader": resolved_bank_loader,
            "auditor": resolved_auditor,
            "embedding_factory": resolved_embedding_factory,
            "store_factory": resolved_store_factory,
            "fingerprint_factory": resolved_fingerprint_factory,
            "env": resolved_env,
            "now_provider": resolved_now_provider,
            "test_dependency": resolved_test_dependency,
        }
    )
    return (
        view,
        embedding_factory is None
        and view.get("embedding_factory", default=None) is None,
        store_factory is None and view.get("store_factory", default=None) is None,
    )


def main(
    argv: list[str] | None = None,
    *,
    dependencies: object | None = None,
    factories: object | None = None,
    bank_loader: Callable[..., Any] | None = None,
    loader: Callable[..., Any] | None = None,
    auditor: Callable[..., Any] | None = None,
    audit_fn: Callable[..., Any] | None = None,
    embedding_factory: Callable[..., Any] | None = None,
    embedder_factory: Callable[..., Any] | None = None,
    embedding_client_factory: Callable[..., Any] | None = None,
    store_factory: Callable[..., Any] | None = None,
    qdrant_factory: Callable[..., Any] | None = None,
    qdrant_store_factory: Callable[..., Any] | None = None,
    fingerprint_factory: Callable[..., Any] | None = None,
    env: Mapping[str, str] | None = None,
    now_provider: Callable[[], date] | None = None,
    today_provider: Callable[[], date] | None = None,
    test_dependency: object | None = None,
    output_fn: Callable[[str], None] | None = None,
    print_fn: Callable[[str], None] | None = None,
    error_fn: Callable[[str], None] | None = None,
) -> int:
    """Run one question-bank action and return a stable process exit code.

    Dependency seams are intentionally keyword-only.  Production defaults are
    resolved lazily and are never constructed for read-only or dry-run paths.
    """

    output = output_fn or print_fn or print
    errors = error_fn or (lambda message: print(message, file=sys.stderr))
    if auditor is None:
        auditor = audit_fn
    bank_loader = bank_loader or loader
    embedding_factory = embedding_factory or embedder_factory or embedding_client_factory
    store_factory = store_factory or qdrant_factory or qdrant_store_factory
    now_provider = now_provider or today_provider

    try:
        parser = _build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE_ERROR
        output_format = "json" if getattr(args, "json", False) else args.format

        view, using_default_embedding, using_default_store = _resolve_injected_dependencies(
            dependencies,
            factories,
            bank_loader=bank_loader,
            auditor=auditor,
            embedding_factory=embedding_factory,
            store_factory=store_factory,
            fingerprint_factory=fingerprint_factory,
            env=env,
            now_provider=now_provider,
            test_dependency=test_dependency,
        )
        loader = view.get("bank_loader", default=load_question_bank)
        if not callable(loader):
            raise QuestionBankValidationError("question bank loader is not callable")
        test_only_dependency = view.get("test_dependency", default=None)
        records = _call_loader(
            loader,
            args.bank,
            test_dependency=test_only_dependency,
        )

        today_provider = _now_provider(view.get("now_provider", default=None))
        as_of = _resolve_today(getattr(args, "as_of", None), today_provider)

        if args.action == "validate":
            output(
                _render_summary(
                    _summary_for_validation(args.action, records),
                    output_format=output_format,
                )
            )
            return EXIT_OK

        auditor_impl = view.get("auditor", default=audit_question_bank)
        if args.action == "audit":
            report = _call_auditor(
                auditor_impl,
                records,
                as_of=as_of,
                expiring_within_days=args.expiring_within_days,
            )
            output(
                _render_summary(
                    _summary_for_audit(records, report, as_of=as_of),
                    output_format=output_format,
                )
            )
            return EXIT_OK

        # The audit is pure and is also used to make dry-run write counts
        # explicit.  It happens before any embedding/store factory call.
        report = _call_auditor(
            auditor_impl,
            records,
            as_of=as_of,
            expiring_within_days=DEFAULT_EXPIRING_WITHIN_DAYS,
        )
        env_value = view.get("env", default=None)
        if env_value is None:
            # Loading .env is configuration discovery only; it does not build
            # a client or perform a network operation.
            try:
                from dotenv import load_dotenv

                load_dotenv()
            except Exception:
                pass
            env_value = os.environ
        model = getattr(args, "model", None) or _env_value(
            env_value, "SILICONFLOW_EMBEDDING_MODEL", DEFAULT_MODEL
        ) or DEFAULT_MODEL
        index_version = getattr(args, "index_version", None) or _env_value(
            env_value, "QUESTION_RAG_INDEX_VERSION", DEFAULT_INDEX_VERSION
        ) or DEFAULT_INDEX_VERSION

        if not args.apply:
            output(
                _render_summary(
                    _summary_for_dry_run(
                        args.action,
                        records,
                        report,
                        as_of=as_of,
                        model=model,
                        index_version=index_version,
                    ),
                    output_format=output_format,
                )
            )
            return EXIT_OK

        api_key = _env_value(env_value, "SILICONFLOW_API_KEY")
        embedding_impl = view.get("embedding_factory", default=None)
        store_impl = view.get("store_factory", default=None)
        fingerprint_impl = view.get("fingerprint_factory", default=None)

        if using_default_embedding and not api_key:
            raise QuestionBankConfigurationError(
                "SILICONFLOW_API_KEY is required for --apply"
            )
        if using_default_embedding:
            embedding_impl = _default_embedding_factory
        if using_default_store:
            store_impl = _default_store_factory
        if embedding_impl is None or store_impl is None:
            raise QuestionBankConfigurationError(
                "embedding and store dependencies are required for --apply"
            )

        texts = _validate_records_for_embedding(records)
        embedding_kwargs = {
            "api_key": api_key,
            "model": model,
            "base_url": _env_value(
                env_value, "SILICONFLOW_EMBEDDING_BASE_URL", DEFAULT_BASE_URL
            )
            or DEFAULT_BASE_URL,
        }
        # Never pass the API key to a caller-provided fake/factory.  The
        # default factory is the only seam that needs it, and this keeps test
        # call arguments and exception paths secret-free by construction.
        if not using_default_embedding:
            embedding_kwargs.pop("api_key", None)
        embedding = _call_with_supported_kwargs(embedding_impl, (), embedding_kwargs)
        embedding_owned = using_default_embedding
        store: Any | None = None
        store_owned = False
        try:
            vectors = _embed(embedding, texts)
            fingerprint = _build_fingerprint(
                embedding=embedding,
                vectors=vectors,
                configured_model=model,
                configured_index_version=index_version,
                fingerprint_factory=fingerprint_impl,
            )
            index_path = getattr(args, "index_path", None) or Path(
                _env_value(env_value, "QUESTION_RAG_INDEX_PATH", str(DEFAULT_INDEX_PATH))
                or str(DEFAULT_INDEX_PATH)
            )
            store_kwargs = {
                "index_path": index_path,
                "path": index_path,
                "fingerprint": fingerprint,
                "collection_name": COLLECTION_NAME,
            }
            store = _call_with_supported_kwargs(store_impl, (), store_kwargs)
            store_owned = using_default_store
            if args.action == "rebuild":
                rebuild = getattr(store, "rebuild", None)
                if not callable(rebuild):
                    raise TypeError("store dependency must provide rebuild")
                rebuild(records, vectors, fingerprint)
            else:
                sync = getattr(store, "sync", None)
                if not callable(sync):
                    raise TypeError("store dependency must provide sync")
                _call_with_supported_kwargs(
                    sync,
                    (records, vectors, fingerprint),
                    {"today": as_of},
                )
            output(
                _render_summary(
                    _summary_for_applied(
                        args.action,
                        records,
                        report,
                        fingerprint,
                        vectors,
                        as_of=as_of,
                    ),
                    output_format=output_format,
                )
            )
            return EXIT_OK
        finally:
            if store_owned and store is not None:
                _close_owned(store)
            if embedding_owned:
                _close_owned(embedding)
    except QuestionBankValidationError as error:
        _emit_error(
            error,
            category="bank",
            output_format=locals().get("output_format", "human"),
            error_fn=errors,
        )
        return EXIT_USAGE_ERROR
    except (ValueError, OSError, TypeError) as error:
        category = "configuration" if isinstance(error, QuestionBankConfigurationError) else "operation"
        _emit_error(
            error,
            category=category,
            output_format=locals().get("output_format", "human"),
            error_fn=errors,
        )
        return EXIT_USAGE_ERROR if category == "configuration" else EXIT_OPERATION_ERROR
    except Exception as error:
        _emit_error(
            error,
            category="operation",
            output_format=locals().get("output_format", "human"),
            error_fn=errors,
        )
        return EXIT_OPERATION_ERROR


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_INDEX_PATH",
    "DEFAULT_INDEX_VERSION",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "EXIT_OK",
    "EXIT_OPERATION_ERROR",
    "EXIT_USAGE_ERROR",
    "QuestionBankConfigurationError",
    "QuestionBankDependencies",
    "QuestionBankValidationError",
    "main",
]
