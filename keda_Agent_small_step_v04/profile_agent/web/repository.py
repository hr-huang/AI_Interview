from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

from profile_agent.web.schemas import AssessmentRecord


class SqliteAssessmentRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS assessments (
                    id TEXT PRIMARY KEY,
                    candidate_token_hash TEXT UNIQUE,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS answer_requests (
                    token_hash TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    PRIMARY KEY (token_hash, idempotency_key)
                );
                """
            )

    def create(self, record: AssessmentRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO assessments(id, candidate_token_hash, payload)
                VALUES (?, ?, ?)
                """,
                (
                    record.id,
                    record.candidate_token_hash,
                    record.model_dump_json(),
                ),
            )

    def get(self, assessment_id: str) -> AssessmentRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM assessments WHERE id = ?",
                (assessment_id,),
            ).fetchone()
        if row is None:
            raise KeyError(assessment_id)
        return AssessmentRecord.model_validate_json(row["payload"])

    def get_by_candidate_token_hash(self, token_hash: str) -> AssessmentRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM assessments WHERE candidate_token_hash = ?",
                (token_hash,),
            ).fetchone()
        if row is None:
            raise KeyError(token_hash)
        return AssessmentRecord.model_validate_json(row["payload"])

    def save(self, record: AssessmentRecord) -> None:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE assessments
                SET candidate_token_hash = ?, payload = ?
                WHERE id = ?
                """,
                (
                    record.candidate_token_hash,
                    record.model_dump_json(),
                    record.id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(record.id)

    def save_answer_response(
        self,
        token_hash: str,
        key: str,
        response: dict[str, Any],
    ) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO answer_requests
                (token_hash, idempotency_key, response_json)
                VALUES (?, ?, ?)
                """,
                (token_hash, key, json.dumps(response, ensure_ascii=False)),
            )
            return cursor.rowcount == 1

    def get_answer_response(
        self,
        token_hash: str,
        key: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT response_json
                FROM answer_requests
                WHERE token_hash = ? AND idempotency_key = ?
                """,
                (token_hash, key),
            ).fetchone()
        return None if row is None else json.loads(row["response_json"])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
