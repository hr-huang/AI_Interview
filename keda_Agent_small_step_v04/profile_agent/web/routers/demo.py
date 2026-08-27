from __future__ import annotations

from fastapi import APIRouter

from profile_agent.web.demo_service import build_demo_report_view
from profile_agent.web.report_view import ReportViewModel


router = APIRouter()


@router.get("/demo/assessment", response_model=ReportViewModel)
def get_demo_assessment() -> ReportViewModel:
    return build_demo_report_view()


@router.get("/demo/assessment/boundary", response_model=ReportViewModel)
def get_boundary_demo_assessment() -> ReportViewModel:
    return build_demo_report_view("C03")


__all__ = ["router"]
