"""Persistent SQLite queue for asynchronous chat video recordings."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JOB_STATUSES = {"queued", "processing", "completed", "failed"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def _open(path: Path):
    connection = _connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path: Path) -> None:
    with _open(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_video_jobs (
                id TEXT PRIMARY KEY,
                schema_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('queued', 'processing', 'completed', 'failed')
                ),
                output_filename TEXT,
                error TEXT,
                execution_id TEXT,
                conversation_id TEXT,
                duration_ms INTEGER,
                headed INTEGER NOT NULL DEFAULT 0,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                stopped_early INTEGER NOT NULL DEFAULT 0,
                progress INTEGER NOT NULL DEFAULT 0,
                progress_stage TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(chat_video_jobs)")
        }
        if "headed" not in columns:
            connection.execute(
                """
                ALTER TABLE chat_video_jobs
                ADD COLUMN headed INTEGER NOT NULL DEFAULT 0
                """
            )
        if "stop_requested" not in columns:
            connection.execute(
                """
                ALTER TABLE chat_video_jobs
                ADD COLUMN stop_requested INTEGER NOT NULL DEFAULT 0
                """
            )
        if "stopped_early" not in columns:
            connection.execute(
                """
                ALTER TABLE chat_video_jobs
                ADD COLUMN stopped_early INTEGER NOT NULL DEFAULT 0
                """
            )
        if "progress" not in columns:
            connection.execute(
                """
                ALTER TABLE chat_video_jobs
                ADD COLUMN progress INTEGER NOT NULL DEFAULT 0
                """
            )
        if "progress_stage" not in columns:
            connection.execute(
                """
                ALTER TABLE chat_video_jobs
                ADD COLUMN progress_stage TEXT NOT NULL DEFAULT 'queued'
                """
            )


def create(
    path: Path,
    job_id: str,
    schema_json: str,
    *,
    headed: bool = False,
) -> dict[str, Any]:
    initialize(path)
    created_at = _utc_now()
    with _open(path) as connection:
        connection.execute(
            """
            INSERT INTO chat_video_jobs (
                id, schema_json, status, headed, created_at
            )
            VALUES (?, ?, 'queued', ?, ?)
            """,
            (job_id, schema_json, int(headed), created_at),
        )
    result = fetch(path, job_id)
    assert result is not None
    return result


def fetch(path: Path, job_id: str) -> dict[str, Any] | None:
    initialize(path)
    with _open(path) as connection:
        row = connection.execute(
            "SELECT * FROM chat_video_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def recover_interrupted(path: Path) -> int:
    """Return interrupted jobs to the queue so they restart from the beginning."""
    initialize(path)
    with _open(path) as connection:
        cursor = connection.execute(
            """
            UPDATE chat_video_jobs
            SET status = 'queued', started_at = NULL, finished_at = NULL,
                output_filename = NULL, error = NULL, execution_id = NULL,
                conversation_id = NULL, duration_ms = NULL,
                stop_requested = 0, stopped_early = 0,
                progress = 0, progress_stage = 'queued'
            WHERE status = 'processing'
            """
        )
        return cursor.rowcount


def claim_next(path: Path) -> dict[str, Any] | None:
    """Atomically claim the oldest queued job, including across API processes."""
    initialize(path)
    connection = _connect(path)
    try:
        connection.isolation_level = None
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM chat_video_jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            connection.execute("COMMIT")
            return None
        started_at = _utc_now()
        connection.execute(
            """
            UPDATE chat_video_jobs
            SET status = 'processing', started_at = ?, finished_at = NULL,
                error = NULL, stop_requested = 0, stopped_early = 0,
                progress = 5, progress_stage = 'preparing',
                attempts = attempts + 1
            WHERE id = ? AND status = 'queued'
            """,
            (started_at, row["id"]),
        )
        claimed = connection.execute(
            "SELECT * FROM chat_video_jobs WHERE id = ?", (row["id"],)
        ).fetchone()
        connection.execute("COMMIT")
        return dict(claimed) if claimed is not None else None
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()


def complete(
    path: Path,
    job_id: str,
    *,
    output_filename: str,
    execution_id: str,
    conversation_id: str,
    duration_ms: int,
    stopped_early: bool = False,
) -> None:
    initialize(path)
    with _open(path) as connection:
        connection.execute(
            """
            UPDATE chat_video_jobs
            SET status = 'completed', output_filename = ?, error = NULL,
                execution_id = ?, conversation_id = ?, duration_ms = ?,
                stopped_early = ?, progress = 100,
                progress_stage = 'completed', finished_at = ?
            WHERE id = ?
            """,
            (
                output_filename,
                execution_id,
                conversation_id,
                duration_ms,
                int(stopped_early),
                _utc_now(),
                job_id,
            ),
        )


def fail(path: Path, job_id: str, error: str) -> None:
    initialize(path)
    with _open(path) as connection:
        connection.execute(
            """
            UPDATE chat_video_jobs
            SET status = 'failed', output_filename = NULL, error = ?,
                progress_stage = 'failed', finished_at = ?
            WHERE id = ?
            """,
            (error[:2000], _utc_now(), job_id),
        )


def request_stop(path: Path, job_id: str) -> dict[str, Any] | None:
    initialize(path)
    with _open(path) as connection:
        connection.execute(
            """
            UPDATE chat_video_jobs
            SET stop_requested = 1, progress_stage = 'stopping'
            WHERE id = ? AND status = 'processing'
            """,
            (job_id,),
        )
        row = connection.execute(
            "SELECT * FROM chat_video_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def is_stop_requested(path: Path, job_id: str) -> bool:
    with _open(path) as connection:
        row = connection.execute(
            "SELECT stop_requested FROM chat_video_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return bool(row["stop_requested"]) if row is not None else False


def update_progress(
    path: Path,
    job_id: str,
    progress: int,
    stage: str,
) -> None:
    bounded = max(0, min(99, progress))
    with _open(path) as connection:
        connection.execute(
            """
            UPDATE chat_video_jobs
            SET progress = ?, progress_stage = ?
            WHERE id = ? AND status = 'processing'
            """,
            (bounded, stage, job_id),
        )
