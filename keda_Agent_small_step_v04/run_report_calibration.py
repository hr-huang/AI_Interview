"""Explicit command for running real MiMo report calibration cases."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv
from openai import OpenAIError

from profile_agent.calibration.artifacts import (
    write_report_calibration_artifacts,
)
from profile_agent.calibration.report_cases import (
    get_report_calibration_case,
    load_report_calibration_cases,
)
from profile_agent.calibration.report_runner import run_report_calibration_case
from profile_agent.llm import LLMProviderError


CASE_IDS = ("C01", "C02", "C03", "C04", "C05", "C06")
CASE_CHOICES = ("ALL", *CASE_IDS)


def _validate_provider_config() -> None:
    """Read the existing provider configuration without exposing secrets."""

    load_dotenv()

    api_key = os.getenv("QWEN_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "未配置 QWEN_API_KEY；请在现有环境或项目根目录 .env 中配置。"
        )

    model = os.getenv("QWEN_MODEL", "").strip() or "qwen3.8-max"
    base_url = os.getenv("QWEN_BASE_URL", "").strip() or (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    if not model:
        raise ValueError("QWEN_MODEL 不能为空。")
    if not base_url:
        raise ValueError("QWEN_BASE_URL 不能为空。")

    numeric_defaults = {
        "LLM_TEMPERATURE": "0.2",
        "LLM_MAX_TOKENS": "8192",
        "LLM_TOP_P": "0.95",
        "LLM_TIMEOUT": "120",
    }
    for name, default in numeric_defaults.items():
        raw_value = os.getenv(name, default).strip()
        try:
            if name == "LLM_MAX_TOKENS":
                int(raw_value)
            else:
                float(raw_value)
        except ValueError as exc:
            raise ValueError(f"{name} 配置不是有效数字。") from exc


def _safe_error_message(error: BaseException) -> str:
    """Return actionable error text with the configured key redacted."""

    detail = str(error).strip()
    for variable in ("QWEN_API_KEY", "MIMO_API_KEY", "DEEPSEEK_API_KEY"):
        configured_key = os.getenv(variable, "").strip()
        if configured_key:
            detail = detail.replace(configured_key, "[REDACTED]")
    return detail or type(error).__name__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行真实 Qwen3.8 Max 报告校准")
    parser.add_argument(
        "--case",
        choices=CASE_CHOICES,
        default="ALL",
        help="要运行的冻结案例，默认为 ALL",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="每个案例的重复次数，必须大于 0",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("artifacts/calibration"),
        help="校准 artifact 根目录",
    )
    return parser


def _selected_cases(case_id: str):
    if case_id == "ALL":
        return load_report_calibration_cases()
    return (get_report_calibration_case(case_id),)


def _run_passed(run: Any) -> bool:
    passed = getattr(run, "passed", None)
    if passed is not None:
        return bool(passed)
    return all(bool(getattr(assertion, "passed", False)) for assertion in run.assertions)


def _print_run_summary(case_id: str, run: Any) -> None:
    status = "PASS" if _run_passed(run) else "FAIL"
    print(f"{case_id} run-{run.run_number:02d}: {status}")
    for assertion in run.assertions:
        assertion_status = "PASS" if assertion.passed else "FAIL"
        print(f"  {assertion.code}: {assertion_status}")


def main(
    argv: list[str] | None = None,
    *,
    runner: Callable[..., Sequence[Any]] | None = None,
    artifact_writer: Callable[..., Path] | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> int:
    """Run selected report cases and return a stable process exit code."""

    if runner is None:
        runner = run_report_calibration_case
    if artifact_writer is None:
        artifact_writer = write_report_calibration_artifacts
    if now_provider is None:
        now_provider = lambda: datetime.now(timezone.utc)

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    if args.runs <= 0:
        print("参数错误：--runs 必须大于 0。", file=sys.stderr)
        return 2

    try:
        _validate_provider_config()
        cases = _selected_cases(args.case)
    except ValueError as error:
        print(f"配置错误：{_safe_error_message(error)}", file=sys.stderr)
        return 2

    calibration_failed = False
    session_timestamp = now_provider().astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    session_root = args.artifact_root / session_timestamp
    try:
        for case in cases:
            runs = list(runner(case, runs=args.runs))
            artifact_writer(session_root, case, runs)
            for run in runs:
                _print_run_summary(case.id, run)
            if not all(_run_passed(run) for run in runs):
                calibration_failed = True
    except (LLMProviderError, OpenAIError) as error:
        print(f"Provider 错误：{_safe_error_message(error)}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"配置错误：{_safe_error_message(error)}", file=sys.stderr)
        return 2

    return 1 if calibration_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
