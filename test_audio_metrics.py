import tempfile
import unittest
from pathlib import Path

from audio_metrics import fetch_all_metrics, init_db, upsert_metrics


class AudioMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.db_path = Path(self.temp_directory.name) / "metrics.db"

    def test_init_db_creates_table_idempotently(self):
        init_db(self.db_path)
        init_db(self.db_path)
        self.assertEqual(fetch_all_metrics(self.db_path), {})

    def test_upsert_then_fetch_all_roundtrip(self):
        upsert_metrics(
            self.db_path,
            "123",
            source_url="https://www.tiktok.com/@user/video/123",
            title="Clip",
            author="user",
            view_count=100,
            like_count=10,
            comment_count=2,
            share_count=1,
        )
        rows = fetch_all_metrics(self.db_path)
        self.assertEqual(rows["123"]["view_count"], 100)
        self.assertEqual(rows["123"]["like_count"], 10)
        self.assertEqual(rows["123"]["comment_count"], 2)
        self.assertEqual(rows["123"]["share_count"], 1)
        self.assertEqual(rows["123"]["source_url"], "https://www.tiktok.com/@user/video/123")

    def test_upsert_updates_existing_row_on_conflict(self):
        upsert_metrics(
            self.db_path,
            "123",
            source_url="https://www.tiktok.com/@user/video/123",
            title="Clip",
            author="user",
            view_count=100,
            like_count=10,
            comment_count=2,
            share_count=1,
        )
        upsert_metrics(
            self.db_path,
            "123",
            source_url="https://www.tiktok.com/@user/video/123",
            title="Clip",
            author="user",
            view_count=500,
            like_count=50,
            comment_count=9,
            share_count=3,
        )
        rows = fetch_all_metrics(self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows["123"]["view_count"], 500)

    def test_fetch_all_metrics_on_fresh_path_self_initializes(self):
        self.assertEqual(fetch_all_metrics(self.db_path), {})


if __name__ == "__main__":
    unittest.main()
