"""Persist report calibration inputs and outputs as safe local artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from profile_agent.calibration.schemas import (
    ReportCalibrationCase,
    ReportCalibrationRun,
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_summary(run: ReportCalibrationRun) -> dict[str, object]:
    return {
        "run_number": run.run_number,
        "passed": run.passed,
        "assertions": [
            {"code": assertion.code, "passed": assertion.passed}
            for assertion in run.assertions
        ],
    }


def _summary_markdown(
    case: ReportCalibrationCase,
    runs: list[ReportCalibrationRun],
) -> str:
    lines = [
        f"# Report calibration: {case.id}",
        "",
        f"Title: {case.title}",
        "",
    ]
    for run in runs:
        status = "PASS" if run.passed else "FAIL"
        lines.extend(
            [
                f"## run-{run.run_number:02d}: {status}",
                "",
            ]
        )
        for assertion in run.assertions:
            assertion_status = "PASS" if assertion.passed else "FAIL"
            lines.append(f"- {assertion.code}: {assertion_status}")
        if not run.assertions:
            lines.append("- (no assertions)")
        lines.append("")
    return "\n".join(lines)


def write_report_calibration_artifacts(
    root: Path,
    case: ReportCalibrationCase,
    runs: list[ReportCalibrationRun],
) -> Path:
    """Write one case's deterministic inputs, outputs, and assertion summary."""

    case_dir = root / case.id
    case_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "case_id": case.id,
        "title": case.title,
        "runs": [_run_summary(run) for run in runs],
    }
    _write_json(case_dir / "summary.json", summary)
    (case_dir / "summary.md").write_text(
        _summary_markdown(case, runs),
        encoding="utf-8",
    )

    input_payload = case.model_dump(mode="json")
    for run in runs:
        run_dir = case_dir / f"run-{run.run_number:02d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(run_dir / "input.json", input_payload)
        _write_json(run_dir / "blueprint.json", run.blueprint.model_dump(mode="json"))
        _write_json(
            run_dir / "rubric_matches.json",
            run.rubric_matches.model_dump(mode="json"),
        )
        _write_json(run_dir / "report.json", run.report.model_dump(mode="json"))
        _write_json(
            run_dir / "assertions.json",
            [assertion.model_dump(mode="json") for assertion in run.assertions],
        )

    return case_dir


__all__ = ["write_report_calibration_artifacts"]
