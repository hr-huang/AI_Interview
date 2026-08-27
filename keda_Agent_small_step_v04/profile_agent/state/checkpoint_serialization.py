"""Checkpoint-only serialization for runtime fields hidden from public dumps.

LangGraph's default msgpack serializer calls ``model_dump()`` for Pydantic
models.  ``InterviewTurn.retrieval_trace`` is intentionally excluded from that
public representation, so checkpoints use a small explicit envelope to retain
the private trace without making it part of generic model serialization.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from profile_agent.schemas.runtime_schema import InterviewTurn


_INTERVIEW_TURN_MARKER = "__profile_agent_checkpoint_type__"
_INTERVIEW_TURN_VERSION = "interview_turn_v1"


def _encode_checkpoint_value(value: Any) -> Any:
    if isinstance(value, InterviewTurn):
        data = value.model_dump(mode="python")
        # Field(exclude=True) is the public boundary.  Read this one private
        # field explicitly only for the checkpoint envelope.
        data["retrieval_trace"] = _encode_checkpoint_value(
            value.retrieval_trace
        )
        return {
            _INTERVIEW_TURN_MARKER: _INTERVIEW_TURN_VERSION,
            "data": data,
        }
    if isinstance(value, Mapping):
        return {
            key: _encode_checkpoint_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_encode_checkpoint_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_encode_checkpoint_value(item) for item in value)
    if isinstance(value, set):
        return {_encode_checkpoint_value(item) for item in value}
    if isinstance(value, frozenset):
        return frozenset(_encode_checkpoint_value(item) for item in value)
    return value


def _decode_checkpoint_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if (
            value.get(_INTERVIEW_TURN_MARKER) == _INTERVIEW_TURN_VERSION
            and isinstance(value.get("data"), Mapping)
        ):
            data = {
                key: _decode_checkpoint_value(item)
                for key, item in value["data"].items()
            }
            return InterviewTurn.model_validate(data)
        return {
            key: _decode_checkpoint_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_decode_checkpoint_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_decode_checkpoint_value(item) for item in value)
    if isinstance(value, set):
        return {_decode_checkpoint_value(item) for item in value}
    if isinstance(value, frozenset):
        return frozenset(_decode_checkpoint_value(item) for item in value)
    return value


class InterviewCheckpointSerializer:
    """Decorate a LangGraph serializer with private ``InterviewTurn`` support."""

    def __init__(self, delegate: Any | None = None) -> None:
        self._delegate = delegate or JsonPlusSerializer()

    def dumps_typed(self, value: Any) -> tuple[str, bytes]:
        return self._delegate.dumps_typed(_encode_checkpoint_value(value))

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        return _decode_checkpoint_value(self._delegate.loads_typed(data))


def install_interview_checkpoint_serializer(checkpointer: Any) -> Any:
    """Install the private-turn serializer on a LangGraph checkpointer."""

    serializer = getattr(checkpointer, "serde", None)
    if isinstance(serializer, InterviewCheckpointSerializer):
        return checkpointer
    if serializer is None:
        # Preserve LangGraph's ``False``/``True`` checkpointer options and let
        # StateGraph.compile apply its usual validation to other values.
        return checkpointer
    checkpointer.serde = InterviewCheckpointSerializer(serializer)
    return checkpointer


__all__ = [
    "InterviewCheckpointSerializer",
    "install_interview_checkpoint_serializer",
]
