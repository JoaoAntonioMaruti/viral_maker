import json
import os
import tempfile
import time
import unittest
from base64 import urlsafe_b64decode
from pathlib import Path
from threading import Lock
from unittest.mock import Mock, patch

import chat_video_jobs
from chat_video import (
    CAPTURE_HEIGHT,
    CAPTURE_WIDTH,
    START_TRIM_MS,
    ChatVideoResult,
    ChatVideoWorker,
    _event_duration_ms,
    _estimate_recording_duration_ms,
    _chat_audio_path,
    _recording_duration_ms,
    _sanitize_job_error,
    build_director_url,
    encode_schema,
    prepare_chat_audio,
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

    def test_estimates_progress_duration_from_conversation_events(self):
        estimated = _estimate_recording_duration_ms(
            {
                "events": [
                    {
                        "delayMs": 300,
                        "message": "1234567890",
                        "reveal": {
                            "loadingMs": 500,
                            "charactersPerSecond": 10,
                        },
                        "holdMs": 1200,
                    }
                ]
            }
        )
        self.assertEqual(estimated, 3000)

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

    def test_audio_controls_are_removed_when_generation_is_disabled(self):
        prepared = prepare_chat_audio(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "generateAudio": False,
                    "voiceId": "voice-1",
                    "forceRegenerateAudio": True,
                }
            ),
            Path("unused"),
        )

        self.assertEqual(json.loads(prepared), {"schemaVersion": 1})

    def test_audio_is_generated_before_capture_with_voice_and_force(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = root / "audio-generation.js"
            generator.write_text("", encoding="utf-8")
            runner_result = Mock(returncode=0)
            runner_result.stdout = iter(
                [
                    "Selected: 3  Pending: 2  Existing: 1\n",
                    "[1/2] start line-2 -> output\n",
                    "[1/2] done line-2\n",
                    "[2/2] start line-3 -> output\n",
                    "[2/2] done line-3\n",
                ]
            )
            schema = {
                "schemaVersion": 1,
                "id": "conversation-1",
                "generateAudio": True,
                "voiceId": "voice-1",
                "forceRegenerateAudio": True,
                "events": [],
            }

            progress = []
            with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), patch(
                "chat_video._require_program", return_value="node"
            ), patch(
                "chat_video.subprocess.Popen", return_value=runner_result
            ) as popen:
                prepared = prepare_chat_audio(
                    json.dumps(schema),
                    root / "audio",
                    generator_script=generator,
                    on_progress=lambda completed, total: progress.append(
                        (completed, total)
                    ),
                )

            command = popen.call_args.args[0]
            self.assertIn("--voice-id", command)
            self.assertIn("voice-1", command)
            self.assertIn("--force", command)
            self.assertEqual(
                command[command.index("--output-dir") + 1],
                str(root / "audio" / "conversation-1"),
            )
            self.assertNotIn("generateAudio", json.loads(prepared))
            self.assertEqual(progress, [(1, 3), (2, 3), (3, 3)])
            self.assertIn("ELEVENLABS_API_KEY", popen.call_args.kwargs["env"])

    def test_resolves_public_and_legacy_chat_audio_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "conversation-1" / "line-1.mp3"
            audio.parent.mkdir()
            audio.write_bytes(b"audio")

            self.assertEqual(
                _chat_audio_path(
                    "http://api.test/media/chat-audios/conversation-1/line-1.mp3",
                    root,
                ),
                audio,
            )
            self.assertEqual(
                _chat_audio_path(
                    "http://client.test/src/features/gameplay-director/audio/"
                    "conversation-1/line-1.mp3",
                    root,
                ),
                audio,
            )

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
                kwargs["on_progress"](75, "recording")
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
