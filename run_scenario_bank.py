"""Validate or rebuild the reviewed Scenario Module index."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from profile_agent.services.scenario_bank_service import ScenarioCatalog
from profile_agent.schemas.scenario_calibration_schema import (
    ScenarioCalibrationRunMetadata,
)
from profile_agent.services.siliconflow_embedding_service import (
    DEFAULT_MODEL as DEFAULT_EMBEDDING_MODEL,
    DEFAULT_PROVIDER as DEFAULT_EMBEDDING_PROVIDER,
)
from profile_agent.services.siliconflow_rerank_service import (
    DEFAULT_RERANK_MODEL,
)
from profile_agent.services.scenario_calibration_service import (
    ScenarioCalibrationAcceptance,
    build_scenario_calibration_request,
    evaluate_scenario_retrieval,
    load_scenario_retrieval_cases,
)


SCENARIO_CALIBRATION_ARTIFACT_ROOT = Path("artifacts/scenario_rag")
SCENARIO_QDRANT_COLLECTION = "scenario_modules"
SCENARIO_QDRANT_INDEX_VERSION = "scenario-modules-v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scenario Bank maintenance")
    parser.add_argument("action", choices=("validate", "rebuild-index", "evaluate"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="explicitly call embedding and update Qdrant",
    )
    return parser


def _qdrant_client_from_env():
    from qdrant_client import QdrantClient

    index_path = os.getenv("SCENARIO_RAG_INDEX_PATH", "").strip()
    qdrant_url = (
        os.getenv("SCENARIO_RAG_QDRANT_URL", "").strip()
        or os.getenv("QDRANT_URL", "").strip()
    )
    if qdrant_url:
        api_key = (
            os.getenv("SCENARIO_RAG_QDRANT_API_KEY", "").strip()
            or os.getenv("QDRANT_API_KEY", "").strip()
        )
        return QdrantClient(url=qdrant_url, api_key=api_key or None)
    if index_path:
        return QdrantClient(path=index_path)
    raise ValueError(
        "请配置 SCENARIO_RAG_INDEX_PATH 或 SCENARIO_RAG_QDRANT_URL"
    )


class _ScenarioCalibrationRetriever:
    """Expose the raw Top-3 store result to the calibration evaluator."""

    def __init__(self, store: object, reranker: object | None = None) -> None:
        self.store = store
        self.reranker = reranker

    def retrieve(self, case, *, as_of: date, limit: int = 3):
        request = build_scenario_calibration_request(case)
        return self.store.search(
            request,
            as_of=as_of,
            limit=limit,
            reranker=self.reranker,
        )


def _write_calibration_report(report, *, as_of: date) -> Path:
    path = SCENARIO_CALIBRATION_ARTIFACT_ROOT / "scenario_retrieval_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _safe_component_identity(component: object, attribute: str, fallback: str) -> str:
    """Read only the public provider/model scalar; never serialize config objects."""

    value = getattr(component, attribute, None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _build_calibration_metadata(
    catalog: ScenarioCatalog,
    embedding: object,
    reranker: object,
    *,
    created_at: datetime | None = None,
) -> ScenarioCalibrationRunMetadata:
    """Build the apply-only report identity without touching provider secrets."""

    return ScenarioCalibrationRunMetadata(
        embedding_provider=_safe_component_identity(
            embedding, "provider", DEFAULT_EMBEDDING_PROVIDER
        ),
        embedding_model=_safe_component_identity(
            embedding, "model", DEFAULT_EMBEDDING_MODEL
        ),
        reranker_provider=_safe_component_identity(
            reranker, "provider", "siliconflow"
        ),
        reranker_model=_safe_component_identity(
            reranker, "model", DEFAULT_RERANK_MODEL
        ),
        qdrant_collection=SCENARIO_QDRANT_COLLECTION,
        qdrant_index_version=SCENARIO_QDRANT_INDEX_VERSION,
        bank_version=catalog.manifest.version,
        role_family=catalog.manifest.role_family,
        role_profile_version=catalog.manifest.role_profile_version,
        as_of=catalog.as_of,
        created_at=created_at or datetime.now(timezone.utc),
        bank_manifest_hash=catalog.manifest_hash,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        catalog = ScenarioCatalog.load()
    except (OSError, TypeError, ValueError) as exc:
        print(f"Scenario Bank 校验失败：{exc}")
        return 2

    if args.action == "validate":
        print(
            "Scenario Bank 校验通过："
            f"{len(catalog.scenarios)} 个场景，"
            f"{len(catalog.modules)} 个检索单元，"
            f"{len(catalog.constraints)} 个约束。"
        )
        return 0

    cases = ()
    if args.action == "evaluate":
        try:
            cases = load_scenario_retrieval_cases()
        except (OSError, TypeError, ValueError) as exc:
            print(f"Scenario Retrieval 校准案例加载失败：{exc}")
            return 2

        if not args.apply:
            print(
                "Scenario Retrieval 评估预览："
                f"{len(cases)} 个案例；embedding calls={len(cases)}；"
                f"正常命中预计 {len(cases)} 次 rerank calls，最多 {len(cases)} 次；"
                "显式传入 --apply 才会执行。"
            )
            return 0

    if not args.apply:
        print(
            "Scenario Module 索引重建预览："
            f"将为 {len(catalog.active_modules)} 个检索单元调用 embedding；"
            "显式传入 --apply 才会执行。"
        )
        return 0

    load_dotenv()
    client = embedding = store = reranker = None
    try:
        from profile_agent.knowledge.qdrant_scenario_store import QdrantScenarioStore
        from profile_agent.services.siliconflow_embedding_service import SiliconFlowEmbeddingClient

        client = _qdrant_client_from_env()
        embedding = SiliconFlowEmbeddingClient.from_env()
        store = QdrantScenarioStore(embedding_client=embedding, client=client)
        if args.action == "rebuild-index":
            store.rebuild(catalog)
            print(
                "Scenario Module 索引重建完成："
                f"collection=scenario_modules，vectors={len(catalog.active_modules)}。"
            )
        else:
            from profile_agent.services.siliconflow_rerank_service import SiliconFlowRerankClient

            store.load_catalog(catalog)
            reranker = SiliconFlowRerankClient.from_env()
            retriever = _ScenarioCalibrationRetriever(store, reranker)
            metadata = _build_calibration_metadata(catalog, embedding, reranker)
            report = evaluate_scenario_retrieval(
                cases,
                retriever,
                catalog.as_of,
                metadata=metadata,
            )
            acceptance = ScenarioCalibrationAcceptance(report)
            report_path = _write_calibration_report(report, as_of=catalog.as_of)
            print(
                "Scenario Retrieval 评估完成："
                f"cases={report.case_count}，top1={report.top1_acceptable_rate:.3f}，"
                f"top3={report.top3_recall:.3f}，"
                f"forbidden_top1={report.forbidden_top1_hit_count}，"
                f"forbidden_top1_hit_count={report.forbidden_top1_hit_count}，"
                f"top3_forbidden_diagnostic={report.forbidden_hit_count}，"
                f"forbidden_hit_count={report.forbidden_hit_count}，"
                f"fallback={report.fallback_count}，"
                f"gate={'PASS' if acceptance.passed else 'FAIL'}，"
                f"report={report_path}。"
            )
            return 0 if acceptance.passed else 1
        return 0
    except Exception as exc:
        # Provider exceptions are intentionally not expanded: they may carry
        # deployment details.  The operator only needs the safe class name.
        action_label = "索引重建" if args.action == "rebuild-index" else "Scenario Retrieval 评估"
        print(f"{action_label}失败：{type(exc).__name__}")
        return 2
    finally:
        for resource in (reranker, store, client, embedding):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
