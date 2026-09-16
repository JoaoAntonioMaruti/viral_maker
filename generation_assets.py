"""SQLite-backed associations between generated videos and screenshots."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DATABASE_PATH = Path("generation_assets.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS video_screenshots (
    video_id TEXT PRIMARY KEY,
    screenshot_id TEXT NOT NULL UNIQUE,
    npc_id TEXT NOT NULL,
    clothes TEXT NOT NULL,
    client_url TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    npc_name TEXT NOT NULL,
    description TEXT NOT NULL,
    message TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(_SCHEMA)


def init_db(db_path: Path = DEFAULT_DATABASE_PATH) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        _ensure_schema(connection)
        connection.commit()


def save_video_screenshot(
    db_path: Path,
    video_id: str,
    screenshot_id: str,
    *,
    npc_id: str,
    clothes: str,
    client_url: str,
    width: int,
    height: int,
    npc_name: str,
    description: str,
    message: str,
    actions: list[str],
) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        _ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO video_screenshots (
                video_id, screenshot_id, npc_id, clothes, client_url,
                width, height, npc_name, description, message,
                actions_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                screenshot_id=excluded.screenshot_id,
                npc_id=excluded.npc_id,
                clothes=excluded.clothes,
                client_url=excluded.client_url,
                width=excluded.width,
                height=excluded.height,
                npc_name=excluded.npc_name,
                description=excluded.description,
                message=excluded.message,
                actions_json=excluded.actions_json,
                created_at=excluded.created_at
            """,
            (
                video_id,
                screenshot_id,
                npc_id,
                clothes,
                client_url,
                width,
                height,
                npc_name,
                description,
                message,
                json.dumps(actions, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["actions"] = json.loads(result.pop("actions_json"))
    return result


def fetch_by_video_id(db_path: Path, video_id: str) -> dict[str, Any] | None:
    path = Path(db_path)
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as connection:
        _ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM video_screenshots WHERE video_id = ?", (video_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def fetch_by_screenshot_id(
    db_path: Path, screenshot_id: str
) -> dict[str, Any] | None:
    path = Path(db_path)
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as connection:
        _ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM video_screenshots WHERE screenshot_id = ?",
            (screenshot_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def fetch_all_by_video_id(db_path: Path) -> dict[str, dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path)) as connection:
        _ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM video_screenshots").fetchall()
    return {row["video_id"]: _row_to_dict(row) for row in rows}


def fetch_screenshot_history(db_path: Path) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as connection:
        _ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT * FROM video_screenshots
            ORDER BY created_at DESC, video_id DESC
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]
