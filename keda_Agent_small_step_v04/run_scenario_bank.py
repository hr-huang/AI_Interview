"""Validate or rebuild the reviewed Scenario Module index."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from dotenv import load_dotenv

from profile_agent.services.scenario_bank_service import ScenarioCatalog


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scenario Bank maintenance")
    parser.add_argument("action", choices=("validate", "rebuild-index"))
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

    if not args.apply:
        print(
            "Scenario Module 索引重建预览："
            f"将为 {len(catalog.active_modules)} 个检索单元调用 embedding；"
            "显式传入 --apply 才会执行。"
        )
        return 0

    load_dotenv()
    client = embedding = store = None
    try:
        from profile_agent.knowledge.qdrant_scenario_store import QdrantScenarioStore
        from profile_agent.services.siliconflow_embedding_service import SiliconFlowEmbeddingClient

        client = _qdrant_client_from_env()
        embedding = SiliconFlowEmbeddingClient.from_env()
        store = QdrantScenarioStore(embedding_client=embedding, client=client)
        store.rebuild(catalog)
        print(
            "Scenario Module 索引重建完成："
            f"collection=scenario_modules，vectors={len(catalog.active_modules)}。"
        )
        return 0
    except Exception as exc:
        # Provider exceptions are intentionally not expanded: they may carry
        # deployment details.  The operator only needs the safe class name.
        print(f"Scenario Module 索引重建失败：{type(exc).__name__}")
        return 2
    finally:
        for resource in (store, client, embedding):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
