"""Export one completed assessment into a private competition artifact.

Usage:
    uv run python scripts/export_competition_session.py ast_xxx
    uv run python scripts/export_competition_session.py ast_xxx --include-inputs

The default output lives under artifacts/runs/, which is ignored by Git.  The
exporter performs no model, embedding, reranker or Qdrant calls.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from profile_agent.web.competition_artifact import (
    build_competition_session_artifact,
)
from profile_agent.web.container import WebContainer


def _checkpoint_values(container: WebContainer, assessment_id: str) -> dict[str, Any]:
    graph = container.interview_graph
    if graph is None:
        raise RuntimeError("interview graph 不可用")
    with container.interview_lock:
        snapshot = graph.get_state(
            {"configurable": {"thread_id": assessment_id}}
        )
    if isinstance(snapshot, Mapping):
        values = snapshot.get("values", snapshot)
    else:
        values = getattr(snapshot, "values", None)
    if isinstance(values, BaseModel):
        values = values.model_dump(mode="python")
    if not isinstance(values, Mapping):
        raise RuntimeError("找不到可读取的 interview checkpoint")
    return dict(values)


def _close_container(container: WebContainer) -> None:
    first_error: BaseException | None = None
    for resource in (
        container.scenario_retriever,
        container.question_retriever,
        container.dispatcher,
        container.repository,
        container.checkpoint_connection,
    ):
        close = getattr(resource, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except BaseException as error:  # pragma: no cover - cleanup best effort
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error


def export_session(
    assessment_id: str,
    *,
    output: Path | None = None,
    include_inputs: bool = False,
) -> Path:
    container = WebContainer.default()
    try:
        record = container.repository.get(assessment_id)
        values = _checkpoint_values(container, assessment_id)
        artifact = build_competition_session_artifact(
            record,
            values,
            include_inputs=include_inputs,
        )
        destination = output or (
            Path("artifacts")
            / "runs"
            / "competition"
            / assessment_id
            / "session.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return destination
    finally:
        _close_container(container)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="导出一场真实完成的比赛 Golden Demo 会话。"
    )
    parser.add_argument("assessment_id", help="例如 ast_xxx")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="自定义 JSON 输出路径；默认写入 artifacts/runs/competition/<assessment_id>/session.json",
    )
    parser.add_argument(
        "--include-inputs",
        action="store_true",
        help="同时导出 JD / Resume 原文；默认关闭，避免误带个人信息。",
    )
    args = parser.parse_args()

    destination = export_session(
        args.assessment_id,
        output=args.output,
        include_inputs=args.include_inputs,
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
