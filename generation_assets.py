"""SQLite-backed associations between generated videos and screenshots."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DATABASE_PATH = Path("generation_assets.db")

_SCREENSHOT_SCHEMA = """
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
    language TEXT,
    created_at TEXT NOT NULL
)
"""

_GENERATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS video_generations (
    video_id TEXT PRIMARY KEY,
    initial_video INTEGER NOT NULL,
    initial_video_filename TEXT NOT NULL,
    final_video INTEGER NOT NULL,
    final_video_filename TEXT NOT NULL,
    language TEXT NOT NULL,
    caption_index INTEGER NOT NULL,
    caption TEXT NOT NULL,
    position TEXT NOT NULL,
    carousel INTEGER NOT NULL,
    carousel_position TEXT NOT NULL,
    music INTEGER NOT NULL,
    music_filename TEXT,
    music_volume REAL NOT NULL,
    created_at TEXT NOT NULL
)
"""


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(_SCREENSHOT_SCHEMA)
    connection.execute(_GENERATION_SCHEMA)
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(video_screenshots)")
    }
    if "language" not in columns:
        connection.execute("ALTER TABLE video_screenshots ADD COLUMN language TEXT")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_video_screenshots_language "
        "ON video_screenshots(language)"
    )
    connection.commit()


def save_video_generation(
    db_path: Path,
    video_id: str,
    *,
    initial_video: int,
    initial_video_filename: str,
    final_video: int,
    final_video_filename: str,
    language: str,
    caption_index: int,
    caption: str,
    position: str,
    carousel: bool,
    carousel_position: str,
    music: bool,
    music_filename: str | None,
    music_volume: float,
) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        _ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO video_generations (
                video_id, initial_video, initial_video_filename,
                final_video, final_video_filename, language, caption_index,
                caption, position, carousel, carousel_position, music,
                music_filename, music_volume, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                initial_video=excluded.initial_video,
                initial_video_filename=excluded.initial_video_filename,
                final_video=excluded.final_video,
                final_video_filename=excluded.final_video_filename,
                language=excluded.language,
                caption_index=excluded.caption_index,
                caption=excluded.caption,
                position=excluded.position,
                carousel=excluded.carousel,
                carousel_position=excluded.carousel_position,
                music=excluded.music,
                music_filename=excluded.music_filename,
                music_volume=excluded.music_volume,
                created_at=excluded.created_at
            """,
            (
                video_id,
                initial_video,
                initial_video_filename,
                final_video,
                final_video_filename,
                language,
                caption_index,
                caption,
                position,
                int(carousel),
                carousel_position,
                int(music),
                music_filename,
                music_volume,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()


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
    language: str,
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
                actions_json, language, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                language=excluded.language,
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
                language,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["actions"] = json.loads(result.pop("actions_json"))
    return result


def _generation_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["carousel"] = bool(result["carousel"])
    result["music"] = bool(result["music"])
    return result


def fetch_generation_by_video_id(
    db_path: Path, video_id: str
) -> dict[str, Any] | None:
    path = Path(db_path)
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as connection:
        _ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM video_generations WHERE video_id = ?", (video_id,)
        ).fetchone()
    return _generation_row_to_dict(row) if row else None


def fetch_all_generations_by_video_id(
    db_path: Path,
) -> dict[str, dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path)) as connection:
        _ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM video_generations").fetchall()
    return {
        row["video_id"]: _generation_row_to_dict(row)
        for row in rows
    }


def delete_video_records(db_path: Path, video_id: str) -> None:
    path = Path(db_path)
    if not path.exists():
        return
    with closing(sqlite3.connect(path)) as connection:
        _ensure_schema(connection)
        connection.execute(
            "DELETE FROM video_screenshots WHERE video_id = ?", (video_id,)
        )
        connection.execute(
            "DELETE FROM video_generations WHERE video_id = ?", (video_id,)
        )
        connection.commit()


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


def fetch_screenshot_history(
    db_path: Path, language: str | None = None
) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as connection:
        _ensure_schema(connection)
        connection.row_factory = sqlite3.Row
        query = "SELECT * FROM video_screenshots"
        parameters: tuple[str, ...] = ()
        if language is not None:
            query += " WHERE language = ?"
            parameters = (language,)
        query += " ORDER BY created_at DESC, video_id DESC"
        rows = connection.execute(query, parameters).fetchall()
    return [_row_to_dict(row) for row in rows]
