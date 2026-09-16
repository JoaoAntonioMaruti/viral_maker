import tempfile
import unittest
import sqlite3
from pathlib import Path

from generation_assets import (
    fetch_all_by_video_id,
    fetch_by_screenshot_id,
    fetch_by_video_id,
    fetch_screenshot_history,
    init_db,
    save_video_screenshot,
)


class GenerationAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.database = Path(self.temp_directory.name) / "generations.db"

    def save(self, *, clothes: str = "default", language: str = "ja") -> None:
        save_video_screenshot(
            self.database,
            "video-1",
            "video-1_screenshot",
            npc_id="npc-1",
            clothes=clothes,
            client_url="http://127.0.0.1:3000/play",
            width=1080,
            height=1920,
            npc_name="ハナ",
            description="Description",
            message="Message",
            actions=["First", "Second"],
            language=language,
        )

    def test_init_db_is_idempotent(self):
        init_db(self.database)
        init_db(self.database)
        self.assertIsNone(fetch_by_video_id(self.database, "missing"))
        self.assertEqual(fetch_screenshot_history(self.database), [])

    def test_saves_and_fetches_association_by_both_ids(self):
        self.save()
        by_video = fetch_by_video_id(self.database, "video-1")
        by_screenshot = fetch_by_screenshot_id(
            self.database, "video-1_screenshot"
        )
        self.assertEqual(by_video, by_screenshot)
        self.assertEqual(by_video["npc_id"], "npc-1")
        self.assertEqual(by_video["actions"], ["First", "Second"])
        self.assertEqual(by_video["width"], 1080)
        self.assertEqual(by_video["language"], "ja")
        self.assertEqual(fetch_all_by_video_id(self.database), {"video-1": by_video})
        self.assertEqual(fetch_screenshot_history(self.database), [by_video])
        self.assertEqual(fetch_screenshot_history(self.database, "ja"), [by_video])
        self.assertEqual(fetch_screenshot_history(self.database, "pt"), [])

    def test_updates_existing_video_association(self):
        self.save()
        self.save(clothes="school_uniform")
        result = fetch_by_video_id(self.database, "video-1")
        self.assertEqual(result["clothes"], "school_uniform")

    def test_migrates_existing_database_with_nullable_language(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                CREATE TABLE video_screenshots (
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
            )
            connection.execute(
                "INSERT INTO video_screenshots VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "old-video",
                    "old-screenshot",
                    "npc",
                    "default",
                    "http://client/play",
                    540,
                    960,
                    "Name",
                    "Description",
                    "Message",
                    "[]",
                    "2026-09-15T00:00:00+00:00",
                ),
            )

        history = fetch_screenshot_history(self.database)
        self.assertEqual(history[0]["language"], None)


if __name__ == "__main__":
    unittest.main()
