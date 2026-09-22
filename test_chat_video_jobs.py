import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path

import chat_video_jobs


class ChatVideoJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.database = Path(self.temp_directory.name) / "jobs.db"

    def test_create_claim_and_complete(self):
        created = chat_video_jobs.create(
            self.database,
            "job-1",
            '{"id":"one"}',
            headed=True,
        )
        self.assertEqual(created["status"], "queued")
        self.assertEqual(created["headed"], 1)
        self.assertEqual(created["stop_requested"], 0)
        self.assertEqual(created["stopped_early"], 0)
        self.assertEqual(created["progress"], 0)
        self.assertEqual(created["progress_stage"], "queued")

        claimed = chat_video_jobs.claim_next(self.database)
        self.assertEqual(claimed["id"], "job-1")
        self.assertEqual(claimed["status"], "processing")
        self.assertEqual(claimed["attempts"], 1)
        self.assertEqual(claimed["progress"], 5)
        self.assertEqual(claimed["progress_stage"], "preparing")
        self.assertIsNone(chat_video_jobs.claim_next(self.database))

        chat_video_jobs.complete(
            self.database,
            "job-1",
            output_filename="job-1.mp4",
            execution_id="execution-1",
            conversation_id="conversation-1",
            duration_ms=1234,
        )
        completed = chat_video_jobs.fetch(self.database, "job-1")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["output_filename"], "job-1.mp4")
        self.assertEqual(completed["duration_ms"], 1234)
        self.assertEqual(completed["progress"], 100)
        self.assertEqual(completed["progress_stage"], "completed")

    def test_updates_processing_progress_without_reaching_one_hundred(self):
        chat_video_jobs.create(self.database, "job-1", "{}")
        chat_video_jobs.claim_next(self.database)

        chat_video_jobs.update_progress(self.database, "job-1", 150, "recording")

        job = chat_video_jobs.fetch(self.database, "job-1")
        self.assertEqual(job["progress"], 99)
        self.assertEqual(job["progress_stage"], "recording")

    def test_processing_job_can_request_stop(self):
        chat_video_jobs.create(self.database, "job-1", "{}")
        chat_video_jobs.claim_next(self.database)

        stopped = chat_video_jobs.request_stop(self.database, "job-1")

        self.assertEqual(stopped["stop_requested"], 1)
        self.assertTrue(chat_video_jobs.is_stop_requested(self.database, "job-1"))

    def test_recover_interrupted_requeues_processing_job(self):
        chat_video_jobs.create(self.database, "job-1", "{}")
        chat_video_jobs.claim_next(self.database)

        self.assertEqual(chat_video_jobs.recover_interrupted(self.database), 1)

        recovered = chat_video_jobs.fetch(self.database, "job-1")
        self.assertEqual(recovered["status"], "queued")
        self.assertIsNone(recovered["started_at"])
        self.assertEqual(recovered["attempts"], 1)

    def test_fail_sanitizes_error_length(self):
        chat_video_jobs.create(self.database, "job-1", "{}")
        chat_video_jobs.fail(self.database, "job-1", "x" * 3000)

        failed = chat_video_jobs.fetch(self.database, "job-1")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(len(failed["error"]), 2000)

    def test_initialize_migrates_jobs_created_before_headed_option(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE chat_video_jobs (
                    id TEXT PRIMARY KEY,
                    schema_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_filename TEXT,
                    error TEXT,
                    execution_id TEXT,
                    conversation_id TEXT,
                    duration_ms INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO chat_video_jobs (
                    id, schema_json, status, created_at
                ) VALUES ('old-job', '{}', 'queued', '2026-09-22T00:00:00Z')
                """
            )

        chat_video_jobs.initialize(self.database)

        migrated = chat_video_jobs.fetch(self.database, "old-job")
        self.assertEqual(migrated["headed"], 0)
        self.assertEqual(migrated["stop_requested"], 0)
        self.assertEqual(migrated["stopped_early"], 0)
        self.assertEqual(migrated["progress"], 0)
        self.assertEqual(migrated["progress_stage"], "queued")


if __name__ == "__main__":
    unittest.main()
