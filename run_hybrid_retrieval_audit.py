"""Summarize a persisted hybrid-retrieval trace without calling providers.

This command is deliberately a post-processing tool.  It reads an existing
JSON artifact, emits only aggregate audit fields, and never loads ``.env`` or
constructs an embedding/reranker/LLM client.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from typing import Any


def summarize_rank_trace(rank_trace: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return a deterministic, secret-safe summary of candidate outcomes.

    ``None`` is a valid value for a candidate with no rejection or truncation
    reason.  Normalize only non-blank strings before sorting so mixed null and
    string values cannot raise the replay TypeError seen in the one-off audit.
    """

    if isinstance(rank_trace, (str, bytes, bytearray)):
        raise TypeError("rank_trace must be a sequence of mappings")
    try:
        entries = list(rank_trace)
    except TypeError as error:
        raise TypeError("rank_trace must be a sequence of mappings") from error
    rejection_reasons: set[str] = set()
    truncation_reasons: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        rejection = entry.get("rejection_reason")
        if isinstance(rejection, str) and rejection.strip():
            rejection_reasons.add(rejection.strip())
        truncation = entry.get("truncation_reason")
        if isinstance(truncation, str) and truncation.strip():
            truncation_reasons.add(truncation.strip())
    return {
        "candidate_count": len(entries),
        "rejection_reasons": sorted(rejection_reasons, key=str.casefold),
        "truncation_reasons": sorted(truncation_reasons, key=str.casefold),
    }


def _load_trace(payload: Any) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        raise ValueError("artifact root must be an object")
    raw_trace = payload.get("top20_audit", payload.get("rank_trace"))
    if raw_trace is None:
        offline_audit = payload.get("offline_top20_candidate_audit")
        raw_trace = (
            offline_audit.get("candidates", [])
            if isinstance(offline_audit, Mapping)
            else []
        )
    if not isinstance(raw_trace, Sequence) or isinstance(raw_trace, (str, bytes, bytearray)):
        raise ValueError("artifact trace must be an array")
    return [entry for entry in raw_trace if isinstance(entry, Mapping)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="汇总已有混合检索审计，不调用模型")
    parser.add_argument("input", type=Path, help="已有 JSON artifact")
    args = parser.parse_args(argv)
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    print(json.dumps(summarize_rank_trace(_load_trace(payload)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "summarize_rank_trace"]
