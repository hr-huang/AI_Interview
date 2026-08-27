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
                    payload TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS answer_requests (
                    token_hash TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    PRIMARY KEY (token_hash, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS assessment_requests (
                    idempotency_key TEXT PRIMARY KEY,
                    request_fingerprint TEXT NOT NULL,
                    assessment_id TEXT NOT NULL UNIQUE
                );
                """
            )
            columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(assessments)"
                ).fetchall()
            }
            if "version" not in columns:
                self._conn.execute(
                    "ALTER TABLE assessments ADD COLUMN version INTEGER NOT NULL DEFAULT 1"
                )
                self._conn.execute(
                    """
                    UPDATE assessments
                    SET version = COALESCE(json_extract(payload, '$.version'), 1)
                    """
                )

    def create(self, record: AssessmentRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO assessments(
                    id, candidate_token_hash, payload, version
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.candidate_token_hash,
                    record.model_dump_json(),
                    record.version,
                ),
            )

    def create_with_request(
        self,
        record: AssessmentRecord,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[bool, tuple[str, str]]:
        """Create an assessment and bind its idempotency key atomically.

        The create row and its request binding must be committed together.
        Otherwise a concurrent duplicate can leave an unbound assessment row
        behind, even though only one request is allowed to start analysis.
        The returned tuple is ``(created, (fingerprint, assessment_id))``;
        when ``created`` is false, the second item identifies the existing
        binding so the caller can detect a payload conflict.
        """

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    """
                    SELECT request_fingerprint, assessment_id
                    FROM assessment_requests
                    WHERE idempotency_key = ?
                    """,
                    (idempotency_key,),
                ).fetchone()
                if row is not None:
                    self._conn.commit()
                    return False, (
                        row["request_fingerprint"],
                        row["assessment_id"],
                    )

                self._conn.execute(
                    """
                    INSERT INTO assessments(
                        id, candidate_token_hash, payload, version
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.candidate_token_hash,
                        record.model_dump_json(),
                        record.version,
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO assessment_requests
                    (idempotency_key, request_fingerprint, assessment_id)
                    VALUES (?, ?, ?)
                    """,
                    (idempotency_key, request_fingerprint, record.id),
                )
                self._conn.commit()
                return True, (request_fingerprint, record.id)
            except Exception:
                self._conn.rollback()
                raise

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
                SET candidate_token_hash = ?, payload = ?, version = ?
                WHERE id = ?
                """,
                (
                    record.candidate_token_hash,
                    record.model_dump_json(),
                    record.version,
                    record.id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(record.id)

    def save_if_version(
        self,
        record: AssessmentRecord,
        expected_version: int,
    ) -> bool:
        """Persist ``record`` only when the stored version is unchanged."""

        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE assessments
                SET candidate_token_hash = ?, payload = ?, version = ?
                WHERE id = ? AND version = ?
                """,
                (
                    record.candidate_token_hash,
                    record.model_dump_json(),
                    record.version,
                    record.id,
                    expected_version,
                ),
            )
            if cursor.rowcount == 1:
                return True
            exists = self._conn.execute(
                "SELECT 1 FROM assessments WHERE id = ?",
                (record.id,),
            ).fetchone()
            if exists is None:
                raise KeyError(record.id)
            return False

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
