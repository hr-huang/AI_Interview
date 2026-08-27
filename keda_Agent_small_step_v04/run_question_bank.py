"""Safely validate, audit, and manage the disposable question-bank index.

The JSON bank is authoritative.  This command only constructs the paid
embedding client and the local Qdrant store for an explicit ``--apply`` on a
``rebuild`` or ``sync`` action.  Validation and audit remain read-only, and a
dry-run never needs an API key.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse

from profile_agent.knowledge.qdrant_question_store import (
    COLLECTION_NAME,
    IndexFingerprint,
    QdrantQuestionStore,
)
from profile_agent.schemas.question_rag_schema import (
    InterviewQuestionRecord,
    MODE_POLICY_VERSION,
)
from profile_agent.services.question_bank_service import (
    EMBEDDING_TEXT_VERSION,
    UNAVAILABLE_QUESTION_BANK_MANIFEST_HASH,
    SUPPORTED_SCHEMA_VERSIONS,
    SUPPORTED_ROLE,
    audit_question_bank,
    build_question_content_hash,
    build_question_embedding_text,
    load_question_bank_runtime_identity,
    load_question_bank,
)
from profile_agent.services.siliconflow_embedding_service import (
    DEFAULT_BASE_URL,
    DEFAULT_DIMENSION,
    DEFAULT_INDEX_VERSION,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    EmbeddingConfigurationError,
    SiliconFlowEmbeddingClient,
    parse_embedding_dimension,
    resolve_embedding_config,
)


DEFAULT_INDEX_PATH = Path("data/qdrant-question-index")
DEFAULT_EXPIRING_WITHIN_DAYS = 30

EXIT_OK = 0
EXIT_OPERATION_ERROR = 1
EXIT_USAGE_ERROR = 2


class QuestionBankValidationError(ValueError):
    """A bank failed before any external dependency can be constructed."""


class QuestionBankConfigurationError(ValueError):
    """The command cannot safely perform the requested apply operation."""


class _SafeArgumentParser(argparse.ArgumentParser):
    """Keep argparse from echoing untrusted values in its default errors."""

    def error(self, _message: str) -> None:
        raise QuestionBankConfigurationError("invalid command arguments")


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
    raw_audit_loader: Callable[..., Any] | None = None


@dataclass(frozen=True)
class QuestionIndexConfig:
    """Canonical identity/configuration shared by index writers and readers.

    Provider/model/dimension/index_version are resolved here; the embedding
    text, bank manifest and mode-policy components are added to the persisted
    ``IndexFingerprint`` after the validated bank is available.  CLI-specific
    overrides are resolved before any client is constructed, so the manifest
    identity cannot silently drift from defaults.
    """

    provider: str
    model: str
    dimension: int
    index_version: str
    index_path: Path
    base_url: str


@dataclass(frozen=True)
class _RawAuditPayload:
    records: list[Mapping[str, Any]]
    role_version: str | None = None


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
                "raw_audit_loader",
                "audit_bank_loader",
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
    parser = _SafeArgumentParser(
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
                "--source-registry",
                "--question-source-registry",
                dest="source_registry",
                type=_parse_path,
                default=None,
                help="可选的 QuestionSourceRegistry.json 路径",
            )
            subparser.add_argument(
                "--manifest",
                "--question-bank-manifest",
                dest="manifest_path",
                type=_parse_path,
                default=None,
                help="可选的 QuestionBankManifest.json 路径",
            )
            subparser.add_argument(
                "--apply",
                action="store_true",
                help="确认执行 embedding 与 Qdrant 写入；默认仅 dry-run",
            )
            subparser.add_argument(
                "--index-path",
                "--qdrant-path",
                dest="index_path",
                type=_parse_path,
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
            subparser.add_argument(
                "--dimension",
                type=_parse_positive_int,
                default=None,
                help="可选的 embedding 维度约束，必须为正整数",
            )
    return parser


def _parse_date(value: str) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise argparse.ArgumentTypeError("日期必须为 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError("日期必须为 YYYY-MM-DD") from exc
    return parsed


def _parse_non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError("天数必须为非负整数") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("天数必须为非负整数")
    return parsed


def _parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError("数值必须为正整数") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("数值必须为正整数")
    return parsed


def _parse_path(value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise argparse.ArgumentTypeError("路径不能为空")
    try:
        return Path(value)
    except (TypeError, ValueError, OSError) as exc:
        raise argparse.ArgumentTypeError("路径无效") from exc


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
        try:
            result = provider()
        except (OverflowError, TypeError, ValueError) as exc:
            raise QuestionBankConfigurationError("as-of date is invalid") from exc
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


def _require_non_blank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuestionBankConfigurationError(f"{label} must not be blank")
    return value.strip()


def _validate_index_path(value: Any) -> Path:
    """Check a local index path without creating files or directories."""

    if value is None or (isinstance(value, str) and not value.strip()):
        raise QuestionBankConfigurationError("index path must not be blank")
    try:
        path = value if isinstance(value, Path) else Path(value)
    except (TypeError, ValueError, OSError) as exc:
        raise QuestionBankConfigurationError("index path is invalid") from exc
    if not str(path).strip():
        raise QuestionBankConfigurationError("index path must not be blank")

    try:
        if path.exists():
            if not path.is_dir():
                raise QuestionBankConfigurationError("index path must be a directory")
            if not os.access(path, os.W_OK):
                raise QuestionBankConfigurationError("index path is not writable")
            return path

        # The store may create the final directory.  Verify that the nearest
        # existing ancestor is a writable directory, but never create it here.
        parent = path.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if not parent.exists() or not parent.is_dir():
            raise QuestionBankConfigurationError("index path parent is unavailable")
        if not os.access(parent, os.W_OK):
            raise QuestionBankConfigurationError("index path parent is not writable")
    except QuestionBankConfigurationError:
        raise
    except (OSError, ValueError) as exc:
        raise QuestionBankConfigurationError("index path is unavailable") from exc
    return path


def _parse_dimension_setting(value: Any) -> int:
    try:
        return parse_embedding_dimension(value)
    except EmbeddingConfigurationError as exc:
        raise QuestionBankConfigurationError(str(exc)) from exc


def _resolve_index_path_from_env(env: Mapping[str, Any]) -> str | Path:
    for name in ("QUESTION_RAG_INDEX_PATH", "QDRANT_QUESTION_INDEX_PATH"):
        value = env.get(name)
        if value is None:
            continue
        text = value if isinstance(value, str) else str(value)
        if text.strip():
            return text.strip()
    return DEFAULT_INDEX_PATH


def build_question_index_config(
    env: Mapping[str, str],
    *,
    model: str | None = None,
    index_version: str | None = None,
    index_path: Path | None = None,
    dimension: int | None = None,
) -> QuestionIndexConfig:
    """Resolve the one canonical index identity/configuration.

    Environment aliases intentionally follow the runtime reader: the
    ``QUESTION_RAG_*`` names win over legacy/provider-specific names, and blank
    environment entries behave like unset values.  Explicit CLI values are
    validated by the caller before this builder is reached.
    """

    try:
        embedding_config = resolve_embedding_config(
            env,
            model=model,
            index_version=index_version,
            dimension=dimension,
        )
    except EmbeddingConfigurationError as exc:
        raise QuestionBankConfigurationError(str(exc)) from exc
    resolved_path = _validate_index_path(
        index_path if index_path is not None else _resolve_index_path_from_env(env)
    )
    return QuestionIndexConfig(
        provider=embedding_config.provider,
        model=embedding_config.model,
        dimension=embedding_config.dimension,
        index_version=embedding_config.index_version,
        index_path=resolved_path,
        base_url=embedding_config.base_url,
    )


# Keep the private spelling available for callers from the first Task7 draft.
_build_index_config = build_question_index_config


def _normalise_records(value: Any, *, test_dependency: object | None) -> list[InterviewQuestionRecord]:
    if isinstance(value, (str, bytes, bytearray)):
        raise QuestionBankValidationError("question bank loader returned invalid records")
    try:
        raw_records = list(value)
    except TypeError as exc:
        raise QuestionBankValidationError("question bank loader returned invalid records") from exc
    if not raw_records:
        raise QuestionBankValidationError("question bank must contain at least one record")

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


def _read_raw_audit_bank(
    path: Path,
    *,
    test_dependency: object | None,
) -> _RawAuditPayload:
    """Read enough bank structure for audit without enforcing record strictness.

    Audit is intentionally the diagnostic path: malformed record/source fields,
    duplicate IDs, and stored hashes must reach ``audit_question_bank`` instead
    of being rejected by the strict indexing loader.  Root identity and the
    synthetic-fixture boundary remain strict so audit cannot inspect a bank
    for another role or accidentally bless test data in production.
    """

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise QuestionBankValidationError("question bank audit JSON could not be read") from exc
    if not isinstance(payload, Mapping):
        raise QuestionBankValidationError("question bank audit root must be an object")

    schema_version = payload.get("schema_version", payload.get("version"))
    if (
        isinstance(schema_version, bool)
        or type(schema_version) not in {int, str}
        or schema_version not in SUPPORTED_SCHEMA_VERSIONS
    ):
        raise QuestionBankValidationError("question bank schema_version is invalid")
    if payload.get("role") != SUPPORTED_ROLE:
        raise QuestionBankValidationError("unsupported question bank role")
    role_version = payload.get("role_version")
    if not isinstance(role_version, str) or not role_version.strip():
        raise QuestionBankValidationError("question bank role_version is invalid")

    test_only = payload.get("test_only", False)
    if not isinstance(test_only, bool):
        raise QuestionBankValidationError("question bank test_only is invalid")
    if test_only and test_dependency is None:
        raise QuestionBankValidationError(
            "test-only synthetic banks require an explicit test dependency"
        )

    questions = payload.get("questions")
    if questions is None:
        questions = payload.get("records")
    if not isinstance(questions, list):
        raise QuestionBankValidationError("question bank questions must be a list")
    if not questions:
        raise QuestionBankValidationError("question bank must contain at least one record")
    # Keep non-object items on the diagnostic path as synthetic placeholders;
    # the canonical auditor can then classify them as invalid records without
    # exposing or echoing arbitrary malformed input.
    normalised_questions: list[Mapping[str, Any]] = []
    for index, question in enumerate(questions):
        if isinstance(question, Mapping):
            normalised_questions.append(question)
        else:
            normalised_questions.append(
                {
                    "question_id": f"<record:{index}>",
                    "valid_until": date.max,
                    "status": None,
                }
            )

    if any(
        question.get("source_type") == "test_only_synthetic"
        for question in normalised_questions
    ) and (test_dependency is None or test_only is not True):
        raise QuestionBankValidationError(
            "test-only synthetic records require an explicit test dependency"
        )
    return _RawAuditPayload(normalised_questions, role_version=role_version)


def _call_raw_audit_loader(
    loader: Callable[..., Any],
    path: Path,
    *,
    test_dependency: object | None,
) -> _RawAuditPayload:
    try:
        loaded = _call_with_supported_kwargs(
            loader,
            (path,),
            {
                "allow_test_only": test_dependency is not None,
                "test_dependency": test_dependency,
            },
        )
    except Exception as exc:
        raise QuestionBankValidationError("question bank audit loader failed") from exc
    if isinstance(loaded, _RawAuditPayload):
        if loaded.role_version is not None and (
            not isinstance(loaded.role_version, str)
            or not loaded.role_version.strip()
        ):
            raise QuestionBankValidationError("question bank role_version is invalid")
        raw_records = loaded.records
        if isinstance(raw_records, (str, bytes, bytearray)):
            raise QuestionBankValidationError(
                "question bank audit loader returned invalid records"
            )
        try:
            raw_records = list(raw_records)
        except TypeError as exc:
            raise QuestionBankValidationError(
                "question bank audit loader returned invalid records"
            ) from exc
        if not raw_records:
            raise QuestionBankValidationError("question bank audit must contain records")
        normalised_records: list[Mapping[str, Any]] = []
        for index, record in enumerate(raw_records):
            if isinstance(record, Mapping):
                normalised_records.append(record)
            elif isinstance(record, InterviewQuestionRecord):
                normalised_records.append(record.model_dump(mode="python"))
            else:
                normalised_records.append(
                    {
                        "question_id": f"<record:{index}>",
                        "valid_until": date.max,
                        "status": None,
                    }
                )
        if any(
            record.get("source_type") == "test_only_synthetic"
            for record in normalised_records
        ) and test_dependency is None:
            raise QuestionBankValidationError(
                "test-only synthetic records require an explicit test dependency"
            )
        return _RawAuditPayload(normalised_records, loaded.role_version)
    if isinstance(loaded, (str, bytes, bytearray)):
        raise QuestionBankValidationError("question bank audit loader returned invalid records")
    try:
        records = list(loaded)
    except TypeError as exc:
        raise QuestionBankValidationError("question bank audit loader returned invalid records") from exc
    if not records:
        raise QuestionBankValidationError("question bank audit must contain records")
    normalised_records: list[Mapping[str, Any]] = []
    for index, record in enumerate(records):
        if isinstance(record, Mapping):
            normalised_records.append(record)
        elif isinstance(record, InterviewQuestionRecord):
            normalised_records.append(record.model_dump(mode="python"))
        else:
            normalised_records.append(
                {
                    "question_id": f"<record:{index}>",
                    "valid_until": date.max,
                    "status": None,
                }
            )
    if any(
        record.get("source_type") == "test_only_synthetic"
        for record in normalised_records
    ) and test_dependency is None:
        raise QuestionBankValidationError(
            "test-only synthetic records require an explicit test dependency"
        )
    return _RawAuditPayload(normalised_records)


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
    try:
        return _call_with_supported_kwargs(auditor, (records,), kwargs)
    except (OverflowError, ValueError) as exc:
        # Date/window validation is a command/configuration error, not a
        # backend failure, even when an injected auditor raises ValueError.
        raise QuestionBankConfigurationError("date range cannot be represented") from exc


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


def _raw_record_identifier(record: Mapping[str, Any], index: int) -> str:
    value = record.get("question_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"<record:{index}>"


def _prepare_audit_records(
    records: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Keep malformed URLs diagnostic instead of letting urlparse abort audit."""

    prepared: list[Mapping[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            prepared.append(
                {
                    "question_id": f"<record:{index}>",
                    "valid_until": date.max,
                    "status": None,
                }
            )
            continue
        copied = dict(record)
        source_url = copied.get("source_url")
        if isinstance(source_url, str):
            try:
                parsed = urlparse(source_url.strip())
                _ = parsed.port
            except (UnicodeError, ValueError):
                # The canonical auditor sees this as a syntactically invalid
                # URL and can safely report invalid_source.  The original URL
                # is never rendered or included in an exception message.
                copied["source_url"] = "invalid://source"
        elif source_url is not None:
            copied["source_url"] = "invalid://source"
        prepared.append(copied)
    return prepared


def _role_version_mismatch_ids(
    records: Sequence[Mapping[str, Any]],
    root_role_version: str | None,
) -> list[str]:
    if root_role_version is None:
        return []
    return [
        _raw_record_identifier(record, index)
        for index, record in enumerate(records)
        if not isinstance(record.get("role_version"), str)
        or record.get("role_version") != root_role_version
    ]


def _raw_audit_diagnostics(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Find duplicate/hash issues without echoing raw record content."""

    id_groups: defaultdict[str, list[str]] = defaultdict(list)
    hash_groups: defaultdict[str, list[str]] = defaultdict(list)
    hash_mismatch_ids: list[str] = []
    for index, raw_record in enumerate(records):
        safe_id = _raw_record_identifier(raw_record, index)
        id_groups[safe_id].append(safe_id)

        content_hash = raw_record.get("content_hash")
        if isinstance(content_hash, str) and content_hash.strip():
            hash_groups[content_hash.strip()].append(safe_id)
        try:
            record = InterviewQuestionRecord.model_validate(raw_record)
            expected_hash = build_question_content_hash(record)
        except Exception:
            continue
        # The strict loader de-duplicates by the canonical semantic hash, not
        # merely by the stored field.  Keep that same diagnostic visible when
        # a malformed bank stores two different (or stale) hash values.
        if not isinstance(content_hash, str) or content_hash.strip() != expected_hash:
            hash_groups[expected_hash].append(safe_id)
        if record.content_hash != expected_hash:
            hash_mismatch_ids.append(safe_id)

    duplicate_ids = sorted(
        question_id
        for question_id, grouped_ids in id_groups.items()
        if len(grouped_ids) > 1 and not question_id.startswith("<record:")
    )
    duplicate_hash_ids = sorted(
        question_id
        for grouped_ids in hash_groups.values()
        if len(grouped_ids) > 1
        for question_id in grouped_ids
    )
    return {
        "duplicate_question_id": duplicate_ids,
        "duplicate_content_hash": duplicate_hash_ids,
        "content_hash_mismatch": sorted(set(hash_mismatch_ids)),
    }


def _record_count_by_status(records: Sequence[Any]) -> dict[str, int]:
    allowed_statuses = {"active", "needs_review", "retired"}
    counts: dict[str, int] = {}
    for record in records:
        if isinstance(record, Mapping):
            status_value = record.get("status")
        else:
            status_value = getattr(record, "status", None)
        status = (
            status_value
            if isinstance(status_value, str) and status_value in allowed_statuses
            else "invalid"
        )
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _safe_audit_identifier(value: Any) -> str:
    """Project an untrusted question id to a stable, non-echoing token."""

    text = str(value)
    if re.fullmatch(r"<record:[0-9]+>", text):
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"id:{digest}"


def _safe_audit_identifiers(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        projected = _safe_audit_identifier(value)
        if projected not in seen:
            seen.add(projected)
            result.append(projected)
    return result


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
    # Every writer path must use the candidate-safe six-section projection;
    # the raw question_text is a canonical field, not an embedding contract.
    return [build_question_embedding_text(record) for record in records]


def _normalise_vectors(
    raw: Any,
    *,
    expected_count: int,
    expected_dimension: int | None = None,
) -> list[list[float]]:
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
    if expected_dimension is not None and dimension != expected_dimension:
        raise QuestionBankConfigurationError("embedding dimension does not match configured dimension")
    return vectors


def _embed(
    embedding: Any,
    texts: Sequence[str],
    *,
    expected_dimension: int | None = None,
) -> list[list[float]]:
    embed = getattr(embedding, "embed", None)
    if not callable(embed):
        if callable(embedding):
            raw = embedding(texts)
        else:
            raise TypeError("embedding dependency must provide embed")
    else:
        raw = embed(texts)
    return _normalise_vectors(
        raw,
        expected_count=len(texts),
        expected_dimension=expected_dimension,
    )


def _build_fingerprint(
    *,
    embedding: Any,
    vectors: Sequence[Sequence[float]],
    configured_provider: str,
    configured_model: str,
    configured_dimension: int,
    configured_index_version: str,
    fingerprint_factory: Callable[..., Any] | None,
    question_bank_manifest_hash: str = UNAVAILABLE_QUESTION_BANK_MANIFEST_HASH,
    embedding_text_version: str = EMBEDDING_TEXT_VERSION,
    mode_policy_version: str = MODE_POLICY_VERSION,
) -> IndexFingerprint:
    """Build and validate all seven persisted index identity fields."""

    _validate_embedding_identity(
        embedding,
        configured_provider=configured_provider,
        configured_model=configured_model,
        configured_dimension=configured_dimension,
    )
    provider_value = getattr(embedding, "provider", None)
    provider = _require_non_blank(
        provider_value if provider_value is not None else configured_provider,
        "embedding provider",
    )
    model_value = getattr(embedding, "model", None)
    model = _require_non_blank(
        model_value if model_value is not None else configured_model,
        "embedding model",
    )
    dimension = len(vectors[0]) if vectors else 0
    if dimension <= 0 or any(len(vector) != dimension for vector in vectors):
        raise QuestionBankConfigurationError("embedding vectors have inconsistent dimensions")
    if dimension != configured_dimension:
        raise QuestionBankConfigurationError(
            "embedding vectors do not match configured dimension"
        )
    configured_index_version = _require_non_blank(
        configured_index_version,
        "index version",
    )
    question_bank_manifest_hash = _require_non_blank(
        question_bank_manifest_hash,
        "question bank manifest hash",
    )
    embedding_text_version = _require_non_blank(
        embedding_text_version,
        "embedding text version",
    )
    mode_policy_version = _require_non_blank(
        mode_policy_version,
        "mode policy version",
    )
    fingerprint_kwargs = {
        "provider": provider,
        "model": model,
        "dimension": dimension,
        "index_version": configured_index_version,
        "embedding_text_version": embedding_text_version,
        "question_bank_manifest_hash": question_bank_manifest_hash,
        "mode_policy_version": mode_policy_version,
    }
    if fingerprint_factory is not None:
        fingerprint = _call_with_supported_kwargs(
            fingerprint_factory,
            (),
            fingerprint_kwargs,
        )
    else:
        fingerprint = IndexFingerprint(**fingerprint_kwargs)
    if not isinstance(fingerprint, IndexFingerprint):
        try:
            fingerprint = IndexFingerprint.model_validate(fingerprint)
        except Exception as exc:
            raise ValueError("fingerprint dependency returned an invalid value") from exc
    if (
        fingerprint.provider != provider
        or fingerprint.model != model
        or fingerprint.dimension != dimension
        or fingerprint.index_version != configured_index_version
        or fingerprint.embedding_text_version != embedding_text_version
        or fingerprint.question_bank_manifest_hash != question_bank_manifest_hash
        or fingerprint.mode_policy_version != mode_policy_version
    ):
        raise QuestionBankConfigurationError(
            "fingerprint identity does not match embedding/index configuration"
        )
    return fingerprint


def _validate_embedding_identity(
    embedding: Any,
    *,
    configured_provider: str,
    configured_model: str,
    configured_dimension: int,
) -> None:
    """Reject a client whose declared contract cannot write this index.

    This preflight runs immediately after construction and before ``embed`` so
    an injected or provider client with an explicit identity mismatch cannot
    trigger a paid request.  A client that does not expose optional metadata is
    checked against the returned vectors and fingerprint after embedding.
    """

    configured_provider = _require_non_blank(configured_provider, "embedding provider")
    configured_model = _require_non_blank(configured_model, "embedding model")
    configured_dimension = _parse_dimension_setting(configured_dimension)
    provider_value = getattr(embedding, "provider", None)
    if provider_value is not None and _require_non_blank(
        provider_value, "embedding provider"
    ) != configured_provider:
        raise QuestionBankConfigurationError(
            "embedding client provider does not match configured provider"
        )
    model_value = getattr(embedding, "model", None)
    if model_value is not None and _require_non_blank(
        model_value, "embedding model"
    ) != configured_model:
        raise QuestionBankConfigurationError(
            "embedding client model does not match configured model"
        )
    declared_dimension = getattr(embedding, "dimension", None)
    if declared_dimension is None:
        declared_dimension = getattr(embedding, "embedding_dimension", None)
    if declared_dimension is not None and _parse_dimension_setting(declared_dimension) != configured_dimension:
        raise QuestionBankConfigurationError(
            "embedding client dimension does not match configured dimension"
        )


def _default_embedding_factory(
    *,
    api_key: str,
    model: str,
    base_url: str,
    provider: str = DEFAULT_PROVIDER,
    dimension: int = DEFAULT_DIMENSION,
) -> SiliconFlowEmbeddingClient:
    return SiliconFlowEmbeddingClient(
        api_key=api_key,
        model=model,
        base_url=base_url,
        provider=provider,
        dimension=dimension,
    )


def _default_store_factory(
    *,
    index_path: Path,
    fingerprint: IndexFingerprint,
    authoritative_catalog: Mapping[str, InterviewQuestionRecord] | None = None,
) -> QdrantQuestionStore:
    return QdrantQuestionStore(
        path=index_path,
        fingerprint=fingerprint,
        authoritative_catalog=authoritative_catalog,
    )


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
    records: Sequence[Any],
    report: Any,
    *,
    as_of: date,
    diagnostics: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    diagnostics = diagnostics or {}
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
        if key == "invalid_record":
            ids.extend(diagnostics.get("role_version_mismatch", ()))
            ids = list(dict.fromkeys(ids))
        if key == "eligible":
            excluded = {
                item
                for diagnostic_ids in diagnostics.values()
                for item in diagnostic_ids
            }
            ids = list(dict.fromkeys(item for item in ids if item not in excluded))
        summary[f"{key}_count"] = len(ids)
        summary[f"{key}_ids"] = _safe_audit_identifiers(ids)
    for key in (
        "duplicate_question_id",
        "duplicate_content_hash",
        "content_hash_mismatch",
        "role_version_mismatch",
    ):
        ids = list(dict.fromkeys(str(item) for item in diagnostics.get(key, ())))
        summary[f"{key}_count"] = len(ids)
        summary[f"{key}_ids"] = _safe_audit_identifiers(ids)
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
        "embedding_text_version": _public_model(fingerprint.embedding_text_version),
        "question_bank_manifest_hash": _public_model(
            fingerprint.question_bank_manifest_hash
        ),
        "mode_policy_version": _public_model(fingerprint.mode_policy_version),
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
        "embedding_text_version",
        "question_bank_manifest_hash",
        "mode_policy_version",
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
            "duplicate_question_id",
            "duplicate_content_hash",
            "content_hash_mismatch",
            "role_version_mismatch",
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
    raw_audit_loader: Callable[..., Any] | None,
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
    resolved_raw_audit_loader = raw_audit_loader
    if resolved_raw_audit_loader is None:
        resolved_raw_audit_loader = view.get(
            "raw_audit_loader", "audit_bank_loader", default=None
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
            "raw_audit_loader": resolved_raw_audit_loader,
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
    raw_audit_loader: Callable[..., Any] | None = None,
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
            raw_audit_loader=raw_audit_loader,
            env=env,
            now_provider=now_provider,
            test_dependency=test_dependency,
        )
        loader = view.get("bank_loader", default=load_question_bank)
        if not callable(loader):
            raise QuestionBankValidationError("question bank loader is not callable")
        test_only_dependency = view.get("test_dependency", default=None)
        diagnostics: dict[str, list[str]] = {}
        root_role_version: str | None = None
        if args.action == "audit" and loader is load_question_bank:
            raw_audit_loader = view.get("raw_audit_loader", default=None)
            if raw_audit_loader is None:
                raw_payload = _read_raw_audit_bank(
                    args.bank,
                    test_dependency=test_only_dependency,
                )
            else:
                if not callable(raw_audit_loader):
                    raise QuestionBankValidationError(
                        "question bank audit loader is not callable"
                    )
                raw_payload = _call_raw_audit_loader(
                    raw_audit_loader,
                    args.bank,
                    test_dependency=test_only_dependency,
                )
            records = _prepare_audit_records(raw_payload.records)
            root_role_version = raw_payload.role_version
            diagnostics = _raw_audit_diagnostics(records)
            diagnostics["role_version_mismatch"] = _role_version_mismatch_ids(
                records,
                root_role_version,
            )
        else:
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
                    _summary_for_audit(
                        records,
                        report,
                        as_of=as_of,
                        diagnostics=diagnostics,
                    ),
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

        # Validate CLI-only settings and dependency shapes before touching
        # dotenv or reading any environment key.  This keeps early failures
        # deterministic and guarantees that no paid/store call can occur.
        explicit_model = getattr(args, "model", None)
        model = (
            _require_non_blank(explicit_model, "embedding model")
            if explicit_model is not None
            else None
        )
        explicit_index_version = getattr(args, "index_version", None)
        index_version = (
            _require_non_blank(explicit_index_version, "index version")
            if explicit_index_version is not None
            else None
        )
        explicit_index_path = getattr(args, "index_path", None)
        index_path = (
            _validate_index_path(explicit_index_path)
            if explicit_index_path is not None
            else None
        )
        source_registry_path = getattr(args, "source_registry", None)
        if source_registry_path is not None:
            try:
                source_registry_path = Path(source_registry_path)
            except (TypeError, ValueError, OSError) as exc:
                raise QuestionBankConfigurationError(
                    "source registry path is invalid"
                ) from exc
        manifest_path = getattr(args, "manifest_path", None)
        if manifest_path is not None:
            try:
                manifest_path = Path(manifest_path)
            except (TypeError, ValueError, OSError) as exc:
                raise QuestionBankConfigurationError(
                    "manifest path is invalid"
                ) from exc
        expected_dimension = getattr(args, "dimension", None)
        if expected_dimension is not None and (
            isinstance(expected_dimension, bool)
            or not isinstance(expected_dimension, int)
            or expected_dimension <= 0
        ):
            raise QuestionBankConfigurationError("embedding dimension must be positive")

        embedding_impl = view.get("embedding_factory", default=None)
        store_impl = view.get("store_factory", default=None)
        fingerprint_impl = view.get("fingerprint_factory", default=None)
        if not using_default_embedding and not callable(embedding_impl):
            raise QuestionBankConfigurationError(
                "embedding dependency must be callable for --apply"
            )
        if not using_default_store and not callable(store_impl):
            raise QuestionBankConfigurationError(
                "store dependency must be callable for --apply"
            )
        if fingerprint_impl is not None and not callable(fingerprint_impl):
            raise QuestionBankConfigurationError(
                "fingerprint dependency must be callable for --apply"
            )
        texts = _validate_records_for_embedding(records)

        if not args.apply:
            # A dry-run deliberately uses static defaults only.  In particular,
            # it must not inspect env or invoke load_dotenv merely to decorate
            # the preview with optional configuration.
            output(
                _render_summary(
                    _summary_for_dry_run(
                        args.action,
                        records,
                        report,
                        as_of=as_of,
                        model=model or DEFAULT_MODEL,
                        index_version=index_version or DEFAULT_INDEX_VERSION,
                    ),
                    output_format=output_format,
                )
            )
            return EXIT_OK

        # The records and all explicit CLI/dependency settings have passed
        # preflight.  Only an applying command may now discover .env values.
        env_value = view.get("env", default=None)
        if env_value is None:
            try:
                from dotenv import load_dotenv

                load_dotenv()
            except Exception:
                pass
            env_value = os.environ

        config = build_question_index_config(
            env_value,
            model=model,
            index_version=index_version,
            index_path=index_path,
            dimension=expected_dimension,
        )
        try:
            runtime_identity = load_question_bank_runtime_identity(
                records,
                bank_path=args.bank,
                source_registry_path=source_registry_path,
                manifest_path=manifest_path,
                environment=env_value,
            )
        except (TypeError, ValueError, OSError) as exc:
            raise QuestionBankConfigurationError(
                "question bank manifest inputs are invalid"
            ) from exc
        model = config.model
        index_version = config.index_version
        index_path = config.index_path
        base_url = config.base_url
        expected_embedding_dimension = config.dimension
        api_key = ""
        if using_default_embedding:
            api_key = _require_non_blank(
                _env_value(env_value, "SILICONFLOW_API_KEY"),
                "SILICONFLOW_API_KEY",
            )

        if using_default_embedding:
            embedding_impl = _default_embedding_factory
        if using_default_store:
            store_impl = _default_store_factory
        if not callable(embedding_impl) or not callable(store_impl):
            raise QuestionBankConfigurationError(
                "embedding and store dependencies are required for --apply"
            )

        embedding_kwargs: dict[str, Any] = {"model": model}
        if using_default_embedding:
            embedding_kwargs.update(
                {
                    "api_key": api_key,
                    "base_url": base_url,
                    "provider": config.provider,
                    "dimension": config.dimension,
                }
            )
        # Never pass the API key to a caller-provided fake/factory.  The
        # default factory is the only seam that needs it, and this keeps test
        # call arguments and exception paths secret-free by construction.
        embedding = _call_with_supported_kwargs(embedding_impl, (), embedding_kwargs)
        embedding_owned = using_default_embedding
        store: Any | None = None
        store_owned = False
        try:
            _validate_embedding_identity(
                embedding,
                configured_provider=config.provider,
                configured_model=config.model,
                configured_dimension=config.dimension,
            )
            vectors = _embed(
                embedding,
                texts,
                expected_dimension=expected_embedding_dimension,
            )
            try:
                fingerprint = _build_fingerprint(
                    embedding=embedding,
                    vectors=vectors,
                    configured_provider=config.provider,
                    configured_model=config.model,
                    configured_dimension=config.dimension,
                    configured_index_version=config.index_version,
                    fingerprint_factory=fingerprint_impl,
                    question_bank_manifest_hash=runtime_identity.manifest_hash,
                    embedding_text_version=EMBEDDING_TEXT_VERSION,
                    mode_policy_version=runtime_identity.policy.mode_policy_version,
                )
            except (TypeError, ValueError, KeyError) as exc:
                raise QuestionBankConfigurationError(
                    "fingerprint configuration is invalid"
                ) from exc
            store_kwargs = {
                "index_path": config.index_path,
                "path": config.index_path,
                "fingerprint": fingerprint,
                "collection_name": COLLECTION_NAME,
                "authoritative_catalog": runtime_identity.catalog,
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
    except OverflowError as error:
        _emit_error(
            error,
            category="configuration",
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
    "DEFAULT_DIMENSION",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "EXIT_OK",
    "EXIT_OPERATION_ERROR",
    "EXIT_USAGE_ERROR",
    "QuestionBankConfigurationError",
    "QuestionBankDependencies",
    "QuestionIndexConfig",
    "QuestionBankValidationError",
    "build_question_index_config",
    "main",
]
