"""Zero-API replay command for the three priority assessment cases."""

from __future__ import annotations

import argparse

from profile_agent.calibration.offline_runner import (
    OFFLINE_CASE_IDS,
    run_offline_calibration_case,
)
from profile_agent.calibration.report_cases import (
    get_report_calibration_case,
)


CASE_CHOICES = ("ALL", *OFFLINE_CASE_IDS)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="零 API 重放 C01/C03/C06 题目、Evidence、评分与雷达图链路"
    )
    parser.add_argument("--case", choices=CASE_CHOICES, default="ALL")
    return parser


def _selected_case_ids(case_id: str) -> tuple[str, ...]:
    return OFFLINE_CASE_IDS if case_id == "ALL" else (case_id,)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code) if isinstance(error.code, int) else 2

    failed = False
    for case_id in _selected_case_ids(args.case):
        run = run_offline_calibration_case(
            get_report_calibration_case(case_id)
        )
        print(f"{case_id}: {'PASS' if run.passed else 'FAIL'}")
        for assertion in run.assertions:
            status = "PASS" if assertion.passed else "FAIL"
            print(f"  {assertion.code}: {status}")
        if not run.passed:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
