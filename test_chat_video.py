import json
import tempfile
import time
import unittest
from base64 import urlsafe_b64decode
from pathlib import Path
from threading import Lock

import chat_video_jobs
from chat_video import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    START_TRIM_MS,
    ChatVideoResult,
    ChatVideoWorker,
    _event_duration_ms,
    _recording_duration_ms,
    _sanitize_job_error,
    build_director_url,
    encode_schema,
)


class ChatVideoTests(unittest.TestCase):
    def test_browser_uses_smaller_vertical_capture_viewport(self):
        self.assertEqual((CAPTURE_WIDTH, CAPTURE_HEIGHT), (540, 960))

    def test_final_video_trims_first_one_hundred_milliseconds(self):
        self.assertEqual(START_TRIM_MS, 100)

    def test_injection_dispatches_play_event_after_auth_preparation(self):
        script = (
            Path(__file__).parent / "assets" / "chat_video_inject.js"
        ).read_text(encoding="utf-8")
        auth_position = script.index('storage.setItem("authToken"')
        event_position = script.index(
            'new CustomEvent("gameplayDirectorPlayChat")'
        )
        open_dialog_position = script.index(
            "await Promise.resolve(window.openDialogAreainJavaScriptLayer())"
        )
        self.assertGreater(event_position, auth_position)
        self.assertGreater(event_position, open_dialog_position)

    def test_url_safe_schema_round_trip_preserves_unicode(self):
        schema = json.dumps(
            {"schemaVersion": 1, "message": "あっ…… olá"},
            ensure_ascii=False,
            separators=(",", ":"),
        )

        encoded = encode_schema(schema)

        self.assertNotIn("+", encoded)
        self.assertNotIn("/", encoded)
        self.assertEqual(urlsafe_b64decode(encoded).decode("utf-8"), schema)
        self.assertEqual(
            build_director_url("http://localhost:3000/play", schema),
            f"http://localhost:3000/play?director=chat&schema64={encoded}",
        )

    def test_event_duration_prefers_director_duration(self):
        self.assertEqual(
            _event_duration_ms(
                {"observedAt": 1000},
                {"observedAt": 2500, "durationMs": 1234.4},
            ),
            1234,
        )

    def test_recording_starts_when_play_event_is_emitted(self):
        self.assertEqual(
            _recording_duration_ms(
                900,
                {"observedAt": 1000},
                {"observedAt": 2500, "durationMs": 1400},
            ),
            1600,
        )

    def test_job_error_does_not_expose_encoded_schema(self):
        error = RuntimeError(
            "navigation failed at http://localhost/play/"
            "?director=chat&schema64=eyJzZWNyZXQiOiJ2YWx1ZSJ9=="
        )
        sanitized = _sanitize_job_error(error)
        self.assertIn("schema64=[redacted]", sanitized)
        self.assertNotIn("eyJzZWNyZXQi", sanitized)

    def test_worker_completes_persisted_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "jobs.db"
            output_directory = root / "outputs"
            script = root / "inject.js"
            script.write_text("", encoding="utf-8")
            chat_video_jobs.create(database, "job-1", '{"schemaVersion":1}')

            def capture(schema_json, output, **kwargs):
                self.assertEqual(schema_json, '{"schemaVersion":1}')
                self.assertEqual(kwargs["injection_script"], script)
                self.assertFalse(kwargs["headed"])
                self.assertFalse(kwargs["should_stop"]())
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"video")
                return ChatVideoResult(
                    output,
                    "execution-1",
                    "conversation-1",
                    1500,
                    stopped_early=True,
                )

            worker = ChatVideoWorker(
                database,
                output_directory,
                script,
                Lock(),
                poll_interval=0.01,
                capture=capture,
            )
            worker.start()
            self.addCleanup(worker.stop)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                job = chat_video_jobs.fetch(database, "job-1")
                if job["status"] == "completed":
                    break
                time.sleep(0.01)
            worker.stop()

            job = chat_video_jobs.fetch(database, "job-1")
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["execution_id"], "execution-1")
            self.assertEqual(job["stopped_early"], 1)
            self.assertTrue((output_directory / "job-1.mp4").is_file())

    def test_worker_marks_capture_failure_and_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "jobs.db"
            output_directory = root / "outputs"
            script = root / "inject.js"
            script.write_text("", encoding="utf-8")
            chat_video_jobs.create(database, "job-1", "{}")

            def capture(schema_json, output, **kwargs):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"partial")
                raise RuntimeError("capture failed")

            worker = ChatVideoWorker(
                database,
                output_directory,
                script,
                Lock(),
                poll_interval=0.01,
                capture=capture,
            )
            worker.start()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                job = chat_video_jobs.fetch(database, "job-1")
                if job["status"] == "failed":
                    break
                time.sleep(0.01)
            worker.stop()

            job = chat_video_jobs.fetch(database, "job-1")
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["error"], "capture failed")
            self.assertFalse((output_directory / "job-1.mp4").exists())


if __name__ == "__main__":
    unittest.main()
