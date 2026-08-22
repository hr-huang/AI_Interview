"""Explicit command for running real MiMo dynamic interview calibrations."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

from openai import OpenAIError

from profile_agent.calibration.artifacts import (
    write_interview_calibration_artifacts,
)
from profile_agent.calibration.interview_cases import (
    get_interview_calibration_case,
    load_interview_calibration_cases,
)
from profile_agent.calibration.interview_runner import (
    InterviewCalibrationRunnerError,
    run_interview_calibration_case,
)
from profile_agent.calibration.scripted_candidate import (
    ScriptedAnswerSelectionError,
)
from profile_agent.llm import LLMProviderError
from run_report_calibration import _safe_error_message, _validate_provider_config


CASE_IDS = ("C01", "C02", "C03", "C04", "C05", "C06")
CASE_CHOICES = ("ALL", *CASE_IDS)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行真实 MiMo 动态面试路径校准")
    parser.add_argument("--case", choices=CASE_CHOICES, default="ALL")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("artifacts/calibration"),
    )
    return parser


def _selected_cases(case_id: str):
    if case_id == "ALL":
        return load_interview_calibration_cases()
    return (get_interview_calibration_case(case_id),)


def _print_run_summary(run: Any) -> None:
    status = "PASS" if run.passed else "FAIL"
    print(f"{run.case_id} run-{run.run_number:02d}: {status}")
    for assertion in run.assertions:
        assertion_status = "PASS" if assertion.passed else "FAIL"
        print(f"  {assertion.code}: {assertion_status}")


def main(
    argv: list[str] | None = None,
    *,
    runner: Callable[..., Any] | None = None,
    artifact_writer: Callable[..., Path] | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> int:
    """Run selected full-path cases with stable process exit codes."""

    runner = run_interview_calibration_case if runner is None else runner
    artifact_writer = (
        write_interview_calibration_artifacts
        if artifact_writer is None
        else artifact_writer
    )
    now_provider = (
        (lambda: datetime.now(timezone.utc))
        if now_provider is None
        else now_provider
    )

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code) if isinstance(error.code, int) else 2

    if args.runs <= 0:
        print("参数错误：--runs 必须大于 0。", file=sys.stderr)
        return 2

    try:
        _validate_provider_config()
        cases = _selected_cases(args.case)
    except ValueError as error:
        print(f"配置错误：{_safe_error_message(error)}", file=sys.stderr)
        return 2

    timestamp = now_provider().astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    session_root = args.artifact_root / timestamp
    calibration_failed = False
    try:
        for case in cases:
            runs = [
                runner(case, run_number=run_number)
                for run_number in range(1, args.runs + 1)
            ]
            artifact_writer(session_root, case, runs)
            for run in runs:
                _print_run_summary(run)
            if not all(run.passed for run in runs):
                calibration_failed = True
    except (LLMProviderError, OpenAIError) as error:
        print(f"Provider 错误：{_safe_error_message(error)}", file=sys.stderr)
        return 1
    except (
        InterviewCalibrationRunnerError,
        ScriptedAnswerSelectionError,
        ValueError,
    ) as error:
        print(f"校准错误：{_safe_error_message(error)}", file=sys.stderr)
        return 1

    return 1 if calibration_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
