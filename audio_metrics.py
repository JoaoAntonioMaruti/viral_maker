"""SQLite-backed storage for TikTok engagement metrics of downloaded audio."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DATABASE_PATH = Path("audio_metrics.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audio_metrics (
    tiktok_id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    title TEXT,
    author TEXT,
    view_count INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    share_count INTEGER,
    fetched_at TEXT NOT NULL
)
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)


def init_db(db_path: Path = DEFAULT_DATABASE_PATH) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        _ensure_schema(conn)
        conn.commit()


def upsert_metrics(
    db_path: Path,
    tiktok_id: str,
    *,
    source_url: str,
    title: str | None,
    author: str | None,
    view_count: int | None,
    like_count: int | None,
    comment_count: int | None,
    share_count: int | None,
) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO audio_metrics (
                tiktok_id, source_url, title, author,
                view_count, like_count, comment_count, share_count, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tiktok_id) DO UPDATE SET
                source_url=excluded.source_url,
                title=excluded.title,
                author=excluded.author,
                view_count=excluded.view_count,
                like_count=excluded.like_count,
                comment_count=excluded.comment_count,
                share_count=excluded.share_count,
                fetched_at=excluded.fetched_at
            """,
            (
                tiktok_id,
                source_url,
                title,
                author,
                view_count,
                like_count,
                comment_count,
                share_count,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def fetch_all_metrics(db_path: Path) -> dict[str, dict[str, Any]]:
    db_path = Path(db_path)
    if not db_path.exists():
        return {}
    with closing(sqlite3.connect(db_path)) as conn:
        _ensure_schema(conn)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM audio_metrics").fetchall()
    return {row["tiktok_id"]: dict(row) for row in rows}
