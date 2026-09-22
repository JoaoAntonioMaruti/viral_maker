"""Unified metadata, tags, and feedback for every generated video type."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


VideoType = Literal["ugc-reaction", "chat-video"]
Feedback = Literal["thumbsup", "thumbsdown"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS video_metadata (
            video_id TEXT PRIMARY KEY,
            video_type TEXT NOT NULL CHECK (
                video_type IN ('ugc-reaction', 'chat-video')
            ),
            request_json TEXT,
            feedback TEXT CHECK (
                feedback IS NULL OR feedback IN ('thumbsup', 'thumbsdown')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS video_tags (
            video_id TEXT NOT NULL,
            tag_key TEXT NOT NULL,
            tag TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (video_id, tag_key),
            FOREIGN KEY (video_id) REFERENCES video_metadata(video_id)
                ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_video_tags_tag_key ON video_tags(tag_key)"
    )
    connection.commit()


def initialize(path: Path) -> None:
    with closing(_connect(path)) as connection:
        _ensure_schema(connection)


def infer_video_type(video_id: str) -> VideoType:
    return "chat-video" if video_id.startswith("chat-") else "ugc-reaction"


def upsert_video(
    path: Path,
    video_id: str,
    video_type: VideoType,
    *,
    request_body: dict[str, Any] | None = None,
) -> None:
    now = _utc_now()
    request_json = (
        json.dumps(request_body, ensure_ascii=False, separators=(",", ":"))
        if request_body is not None
        else None
    )
    with closing(_connect(path)) as connection:
        _ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO video_metadata (
                video_id, video_type, request_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                video_type=excluded.video_type,
                request_json=COALESCE(excluded.request_json, video_metadata.request_json),
                updated_at=excluded.updated_at
            """,
            (video_id, video_type, request_json, now, now),
        )
        connection.commit()


def ensure_video(path: Path, video_id: str) -> dict[str, Any]:
    existing = fetch(path, video_id)
    if existing is not None:
        return existing
    upsert_video(path, video_id, infer_video_type(video_id))
    result = fetch(path, video_id)
    assert result is not None
    return result


def fetch(path: Path, video_id: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with closing(_connect(path)) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            "SELECT * FROM video_metadata WHERE video_id = ?", (video_id,)
        ).fetchone()
        if row is None:
            return None
        tags = connection.execute(
            """
            SELECT tag FROM video_tags
            WHERE video_id = ?
            ORDER BY tag_key ASC
            """,
            (video_id,),
        ).fetchall()
    result = dict(row)
    result["type"] = result.pop("video_type")
    request_json = result.pop("request_json")
    result["request_body"] = json.loads(request_json) if request_json else None
    result["tags"] = [item["tag"] for item in tags]
    return result


def fetch_all(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with closing(_connect(path)) as connection:
        _ensure_schema(connection)
        ids = [
            row["video_id"]
            for row in connection.execute("SELECT video_id FROM video_metadata")
        ]
    return {video_id: fetch(path, video_id) for video_id in ids}


def add_tags(path: Path, video_id: str, tags: list[str]) -> dict[str, Any]:
    ensure_video(path, video_id)
    now = _utc_now()
    with closing(_connect(path)) as connection:
        _ensure_schema(connection)
        for tag in tags:
            cleaned = tag.strip()
            connection.execute(
                """
                INSERT OR IGNORE INTO video_tags (
                    video_id, tag_key, tag, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (video_id, cleaned.casefold(), cleaned, now),
            )
        connection.commit()
    result = fetch(path, video_id)
    assert result is not None
    return result


def remove_tag(path: Path, video_id: str, tag: str) -> dict[str, Any]:
    ensure_video(path, video_id)
    cleaned = tag.strip()
    with closing(_connect(path)) as connection:
        _ensure_schema(connection)
        cursor = connection.execute(
            "DELETE FROM video_tags WHERE video_id = ? AND tag_key = ?",
            (video_id, cleaned.casefold()),
        )
        if cursor.rowcount:
            connection.execute(
                "UPDATE video_metadata SET updated_at = ? WHERE video_id = ?",
                (_utc_now(), video_id),
            )
        connection.commit()
    result = fetch(path, video_id)
    assert result is not None
    return result


def set_feedback(
    path: Path,
    video_id: str,
    feedback: Feedback | None,
) -> dict[str, Any]:
    ensure_video(path, video_id)
    with closing(_connect(path)) as connection:
        _ensure_schema(connection)
        connection.execute(
            """
            UPDATE video_metadata
            SET feedback = ?, updated_at = ?
            WHERE video_id = ?
            """,
            (feedback, _utc_now(), video_id),
        )
        connection.commit()
    result = fetch(path, video_id)
    assert result is not None
    return result


def delete(path: Path, video_id: str) -> None:
    if not path.exists():
        return
    with closing(_connect(path)) as connection:
        _ensure_schema(connection)
        connection.execute("DELETE FROM video_metadata WHERE video_id = ?", (video_id,))
        connection.commit()
