"""Run selected frozen assessment cases without any provider calls."""

from profile_agent.calibration.offline_services import (
    build_offline_semantic_services,
)
from profile_agent.calibration.report_runner import (
    run_report_calibration_case,
)
from profile_agent.calibration.schemas import (
    ReportCalibrationCase,
    ReportCalibrationRun,
)


OFFLINE_CASE_IDS = ("C01", "C03", "C06")


def run_offline_calibration_case(
    case: ReportCalibrationCase,
) -> ReportCalibrationRun:
    """Replay one supported case through current deterministic report stages."""

    if case.id not in OFFLINE_CASE_IDS:
        raise ValueError(f"不支持的离线回放案例: {case.id}")
    runs = run_report_calibration_case(
        case,
        runs=1,
        semantic_services=build_offline_semantic_services(case),
    )
    if len(runs) != 1:
        raise RuntimeError("离线回放必须产生且仅产生一次运行")
    return runs[0]


__all__ = ["OFFLINE_CASE_IDS", "run_offline_calibration_case"]
