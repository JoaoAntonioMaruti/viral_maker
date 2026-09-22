import tempfile
import unittest
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import api
    import audio_metrics
    import generation_assets
    from fastapi import HTTPException
    from pydantic import ValidationError
except ImportError:
    api = None


@unittest.skipIf(api is None, "FastAPI dependencies are not installed")
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.output_directory = Path(self.temp_directory.name)
        self.output_patch = patch.object(api, "OUTPUT_DIRECTORY", self.output_directory)
        self.output_patch.start()
        self.addCleanup(self.output_patch.stop)
        self.video_directory = Path(self.temp_directory.name) / "videos"
        self.video_directory.mkdir()
        (self.video_directory / "1.mp4").write_bytes(b"first")
        (self.video_directory / "3.mp4").write_bytes(b"third video")
        (self.video_directory / "notes.txt").write_text("ignore")
        self.video_directory_patch = patch.object(
            api, "VIDEO_DIRECTORY", self.video_directory
        )
        self.video_directory_patch.start()
        self.addCleanup(self.video_directory_patch.stop)
        self.final_directory = Path(self.temp_directory.name) / "finals"
        self.final_directory.mkdir()
        (self.final_directory / "1.mp4").write_bytes(b"final one")
        (self.final_directory / "2.mp4").write_bytes(b"final two")
        self.final_patch = patch.object(
            api, "FINAL_VIDEO_DIRECTORY", self.final_directory
        )
        self.final_patch.start()
        self.addCleanup(self.final_patch.stop)
        self.audio_directory = Path(self.temp_directory.name) / "audio"
        self.audio_directory.mkdir()
        (self.audio_directory / "default.mp3").write_bytes(b"default audio")
        (self.audio_directory / "second.m4a").write_bytes(b"other")
        (self.audio_directory / "ignore.txt").write_text("not audio")
        self.audio_directory_patch = patch.object(
            api, "AUDIO_DIRECTORY", self.audio_directory
        )
        self.audio_directory_patch.start()
        self.addCleanup(self.audio_directory_patch.stop)
        self.database_path = Path(self.temp_directory.name) / "audio_metrics.db"
        self.database_path_patch = patch.object(
            api, "DATABASE_PATH", self.database_path
        )
        self.database_path_patch.start()
        self.addCleanup(self.database_path_patch.stop)
        self.generation_database_path = (
            Path(self.temp_directory.name) / "generation_assets.db"
        )
        self.generation_database_path_patch = patch.object(
            api, "GENERATION_DATABASE_PATH", self.generation_database_path
        )
        self.generation_database_path_patch.start()
        self.addCleanup(self.generation_database_path_patch.stop)
        self.chat_video_database_path = (
            Path(self.temp_directory.name) / "chat_video_jobs.db"
        )
        self.chat_video_database_path_patch = patch.object(
            api, "CHAT_VIDEO_DATABASE_PATH", self.chat_video_database_path
        )
        self.chat_video_database_path_patch.start()
        self.addCleanup(self.chat_video_database_path_patch.stop)
        self.caption_data = Path(self.temp_directory.name) / "data.json"
        self.caption_data.write_text(
            json.dumps(
                {
                    "pt": {
                        "carousel": "Her reply",
                        "data": ["First", "Second"],
                    }
                }
            ),
            encoding="utf-8",
        )
        self.caption_data_patch = patch.object(api, "CAPTION_DATA", self.caption_data)
        self.caption_data_patch.start()
        self.addCleanup(self.caption_data_patch.stop)
        self.request = Mock()
        self.request.url_for.side_effect = lambda name, **params: (
            f"http://test/videos/{params['video_id']}/download"
            if name == "download_video"
            else f"http://test/screenshots/{params['screenshot_id']}/download"
        )

    def test_health(self):
        self.assertEqual(api.health(), {"status": "ok"})

    def test_reaction_route_is_registered(self):
        routes = {
            (route.path, method)
            for route in api.app.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(("/videos/reaction", "POST"), routes)
        self.assertIn(("/videos", "GET"), routes)
        self.assertNotIn(("/videos", "POST"), routes)
        self.assertIn(("/audios", "GET"), routes)
        self.assertIn(("/audios", "POST"), routes)
        self.assertIn(("/data", "GET"), routes)
        self.assertIn(("/outputs", "GET"), routes)
        self.assertIn(("/screenshots/{screenshot_id}/download", "GET"), routes)
        self.assertIn(("/screenshots/history", "GET"), routes)
        self.assertIn(("/videos/chat-video", "POST"), routes)
        self.assertIn(("/videos/chat-video/{job_id}", "GET"), routes)
        self.assertIn(("/videos/chat-video/{job_id}/download", "GET"), routes)
        self.assertIn(("/videos/chat-video/{job_id}/stop", "POST"), routes)
        self.assertIn(("/videos/tags", "GET"), routes)
        self.assertIn(("/videos/{video_id}/tags", "GET"), routes)
        self.assertIn(("/videos/{video_id}/tags", "POST"), routes)
        self.assertIn(("/videos/{video_id}/tags/{tag}", "DELETE"), routes)
        self.assertIn(("/videos/{video_id}/feedback", "PUT"), routes)
        self.assertIn(("/videos/{video_id}/metadata", "GET"), routes)
        mount_paths = {
            route.path for route in api.app.routes if route.__class__.__name__ == "Mount"
        }
        self.assertTrue(
            {"/media/audios", "/media/videos", "/media/outputs"}.issubset(
                mount_paths
            )
        )

    def test_default_music_volume_is_full(self):
        self.assertEqual(api.VideoCreate().music_volume, 1.0)

    def _chat_schema(self):
        return {
            "schemaVersion": 1,
            "id": "conversation-1",
            "npc": {
                "id": "npc-1",
                "name": "ハナ",
                "renderer": {"type": "npc-renderer-v2", "strict": True},
            },
            "options": {"suppressSideEffects": True},
            "events": [{"type": "chat.open", "delayMs": 300}],
        }

    def _chat_request(self):
        request = Mock()
        request.url_for.side_effect = lambda name, **params: {
            "get_chat_video_job": (
                f"http://test/videos/chat-video/{params['job_id']}"
            ),
            "download_chat_video": (
                f"http://test/videos/chat-video/{params['job_id']}/download"
            ),
        }[name]
        return request

    def test_creates_and_reads_chat_video_job(self):
        payload = api.ChatVideoCreate.model_validate(self._chat_schema())

        created = api.create_chat_video(payload, self._chat_request())
        fetched = api.get_chat_video_job(created.id, self._chat_request())

        self.assertEqual(created.status, "queued")
        self.assertEqual(created.type, "chat-video")
        self.assertFalse(created.headed)
        self.assertFalse(created.stop_requested)
        self.assertFalse(created.stopped_early)
        self.assertEqual(created.progress, 0)
        self.assertEqual(created.progress_stage, "queued")
        self.assertEqual(fetched.id, created.id)
        self.assertIsNone(created.download_url)
        stored = api.chat_video_jobs.fetch(
            self.chat_video_database_path, created.id
        )
        self.assertEqual(json.loads(stored["schema_json"])["npc"]["name"], "ハナ")
        metadata = api.video_metadata.fetch(
            self.generation_database_path, created.id
        )
        self.assertEqual(metadata["type"], "chat-video")
        self.assertEqual(metadata["request_body"]["npc"]["name"], "ハナ")

    def test_adds_unique_tags_and_lists_them_for_all_video_types(self):
        reaction = self.output_directory / "reaction-1.mp4"
        chat = self.output_directory / "chat-example.mp4"
        reaction.write_bytes(b"reaction")
        chat.write_bytes(b"chat")

        first = api.add_video_tags(
            reaction.stem,
            api.VideoTagsCreate(tags=["Anime", "anime", " Viral "]),
        )
        second = api.add_video_tags(
            reaction.stem,
            api.VideoTagsCreate(tags=["ANIME", "Comedy"]),
        )
        chat_tags = api.add_video_tags(
            chat.stem,
            api.VideoTagsCreate(tags=["Dialogue"]),
        )
        listed = {item.video_id: item for item in api.list_video_tags()}

        self.assertEqual(first.tags, ["Anime", "Viral"])
        self.assertEqual(second.tags, ["Anime", "Comedy", "Viral"])
        self.assertEqual(second.type, "ugc-reaction")
        self.assertEqual(chat_tags.type, "chat-video")
        self.assertEqual(listed[reaction.stem].tags, second.tags)
        self.assertEqual(listed[chat.stem].tags, ["Dialogue"])

    def test_removes_tag_case_insensitively_and_is_idempotent(self):
        video = self.output_directory / "reaction-1.mp4"
        video.write_bytes(b"reaction")
        api.add_video_tags(
            video.stem,
            api.VideoTagsCreate(tags=["Anime", "Dialogue"]),
        )

        removed = api.remove_video_tag(video.stem, "anime")
        repeated = api.remove_video_tag(video.stem, "ANIME")

        self.assertEqual(removed.tags, ["Dialogue"])
        self.assertEqual(repeated.tags, ["Dialogue"])

    def test_feedback_is_replaceable_and_independent_of_video_type(self):
        for video_id in ("reaction-1", "chat-example"):
            (self.output_directory / f"{video_id}.mp4").write_bytes(b"video")

        reaction = api.update_video_feedback(
            "reaction-1",
            api.VideoFeedbackUpdate(value="thumbsup"),
        )
        chat = api.update_video_feedback(
            "chat-example",
            api.VideoFeedbackUpdate(value="thumbsdown"),
        )
        cleared = api.update_video_feedback(
            "reaction-1",
            api.VideoFeedbackUpdate(value=None),
        )

        self.assertEqual(reaction.type, "ugc-reaction")
        self.assertEqual(reaction.value, "thumbsup")
        self.assertEqual(chat.type, "chat-video")
        self.assertEqual(chat.value, "thumbsdown")
        self.assertIsNone(cleared.value)

    def test_returns_reproducible_video_metadata(self):
        path = self.output_directory / "chat-example.mp4"
        path.write_bytes(b"video")
        body = self._chat_schema()
        api.video_metadata.upsert_video(
            self.generation_database_path,
            path.stem,
            "chat-video",
            request_body=body,
        )

        metadata = api.get_video_metadata(path.stem)

        self.assertEqual(metadata.type, "chat-video")
        self.assertEqual(metadata.request_body, body)

    def test_creates_headed_chat_video_job_for_debugging(self):
        payload = api.ChatVideoCreate.model_validate(self._chat_schema())

        created = api.create_chat_video(
            payload,
            self._chat_request(),
            headed=True,
        )

        self.assertTrue(created.headed)
        stored = api.chat_video_jobs.fetch(
            self.chat_video_database_path, created.id
        )
        self.assertEqual(stored["headed"], 1)

    def test_chat_video_rejects_gameplay_wrapper(self):
        with self.assertRaises(ValidationError):
            api.ChatVideoCreate.model_validate(
                {
                    "schemaVersion": 1,
                    "id": "gameplay-script",
                    "environment": {},
                    "steps": [],
                }
            )

    def test_chat_video_rejects_event_without_type(self):
        schema = self._chat_schema()
        schema["events"] = [{"delayMs": 10}]
        with self.assertRaisesRegex(ValidationError, "non-empty type"):
            api.ChatVideoCreate.model_validate(schema)

    def test_completed_chat_video_can_be_downloaded(self):
        created = api.create_chat_video(
            api.ChatVideoCreate.model_validate(self._chat_schema()),
            self._chat_request(),
        )
        output = self.output_directory / f"{created.id}.mp4"
        output.write_bytes(b"chat video")
        api.chat_video_jobs.complete(
            self.chat_video_database_path,
            created.id,
            output_filename=output.name,
            execution_id="execution-1",
            conversation_id="conversation-1",
            duration_ms=2000,
        )

        status_response = api.get_chat_video_job(created.id, self._chat_request())
        download = api.download_chat_video(created.id)

        self.assertEqual(status_response.status, "completed")
        self.assertEqual(status_response.duration_ms, 2000)
        self.assertEqual(
            status_response.download_url,
            f"http://test/videos/chat-video/{created.id}/download",
        )
        self.assertEqual(Path(download.path), output)
        self.assertEqual(download.media_type, "video/mp4")

    def test_pending_chat_video_download_returns_409(self):
        created = api.create_chat_video(
            api.ChatVideoCreate.model_validate(self._chat_schema()),
            self._chat_request(),
        )
        with self.assertRaises(HTTPException) as raised:
            api.download_chat_video(created.id)
        self.assertEqual(raised.exception.status_code, 409)

    def test_requests_early_stop_for_processing_chat_video(self):
        created = api.create_chat_video(
            api.ChatVideoCreate.model_validate(self._chat_schema()),
            self._chat_request(),
        )
        api.chat_video_jobs.claim_next(self.chat_video_database_path)

        response = api.stop_chat_video(created.id, self._chat_request())

        self.assertEqual(response.status, "processing")
        self.assertTrue(response.stop_requested)
        self.assertEqual(response.progress_stage, "stopping")

    def test_stop_rejects_queued_chat_video(self):
        created = api.create_chat_video(
            api.ChatVideoCreate.model_validate(self._chat_schema()),
            self._chat_request(),
        )
        with self.assertRaises(HTTPException) as raised:
            api.stop_chat_video(created.id, self._chat_request())
        self.assertEqual(raised.exception.status_code, 409)

    def test_lists_final_videos(self):
        self.assertEqual(
            [video.model_dump() for video in api.get_final_videos()],
            [
                {
                    "number": 1,
                    "filename": "1.mp4",
                    "url": "/media/videos/final_videos/1.mp4",
                },
                {
                    "number": 2,
                    "filename": "2.mp4",
                    "url": "/media/videos/final_videos/2.mp4",
                },
            ],
        )

    def test_lists_initial_videos_and_assigns_numbers_by_creation_order(self):
        self.assertEqual(
            [video.model_dump() for video in api.get_initial_videos()],
            [
                {
                    "number": 1,
                    "filename": "1.mp4",
                    "size_bytes": 5,
                    "url": "/media/videos/1.mp4",
                },
                {
                    "number": 2,
                    "filename": "3.mp4",
                    "size_bytes": 11,
                    "url": "/media/videos/3.mp4",
                },
            ],
        )

    def test_lists_initial_video_with_non_numeric_filename(self):
        path = self.video_directory / "custom name.mp4"
        path.write_bytes(b"custom")

        videos = api.get_initial_videos()

        self.assertEqual(videos[-1].number, 3)
        self.assertEqual(videos[-1].filename, "custom name.mp4")
        self.assertEqual(api.resolve_initial_video(3), path.resolve())

    def test_initial_video_list_is_empty_when_directory_does_not_exist(self):
        with patch.object(api, "VIDEO_DIRECTORY", self.video_directory / "missing"):
            self.assertEqual(api.get_initial_videos(), [])

    def test_lists_audio_files_without_exposing_server_paths(self):
        self.assertEqual(
            [audio.model_dump() for audio in api.get_audio_files()],
            [
                {
                    "filename": "default.mp3",
                    "size_bytes": 13,
                    "url": "/media/audios/default.mp3",
                    "views": None,
                    "likes": None,
                    "comments": None,
                    "shares": None,
                },
                {
                    "filename": "second.m4a",
                    "size_bytes": 5,
                    "url": "/media/audios/second.m4a",
                    "views": None,
                    "likes": None,
                    "comments": None,
                    "shares": None,
                },
            ],
        )

    def test_lists_audio_files_with_known_metrics(self):
        audio_metrics.upsert_metrics(
            self.database_path,
            "second",
            source_url="https://www.tiktok.com/@user/video/second",
            title="Some clip",
            author="user",
            view_count=1000,
            like_count=200,
            comment_count=30,
            share_count=4,
        )
        files = {audio.filename: audio for audio in api.get_audio_files()}
        self.assertEqual(files["second.m4a"].views, 1000)
        self.assertEqual(files["second.m4a"].likes, 200)
        self.assertEqual(files["second.m4a"].comments, 30)
        self.assertEqual(files["second.m4a"].shares, 4)
        self.assertIsNone(files["default.mp3"].views)

    def test_audio_list_is_empty_when_directory_does_not_exist(self):
        with patch.object(api, "AUDIO_DIRECTORY", self.audio_directory / "missing"):
            self.assertEqual(api.get_audio_files(), [])

    def test_create_audio_success(self):
        downloaded = self.audio_directory / "123456.mp3"
        downloaded.write_bytes(b"tiktok audio")
        metadata = {
            "tiktok_id": "123456",
            "source_url": "https://www.tiktok.com/@user/video/123456",
            "title": "A viral clip",
            "author": "user",
            "view_count": 5000,
            "like_count": 900,
            "comment_count": 40,
            "share_count": 12,
        }
        with patch.object(
            api,
            "download_audio_with_metadata",
            return_value=(downloaded, metadata),
        ) as download:
            response = api.create_audio(
                api.AudioCreate(url="https://www.tiktok.com/@user/video/123456")
            )

        download.assert_called_once_with(
            "https://www.tiktok.com/@user/video/123456",
            self.audio_directory,
            overwrite=False,
            cookies_from_browser=None,
        )
        self.assertEqual(response.filename, "123456.mp3")
        self.assertEqual(response.views, 5000)
        self.assertEqual(response.likes, 900)
        self.assertEqual(response.comments, 40)
        self.assertEqual(response.shares, 12)
        stored = audio_metrics.fetch_all_metrics(self.database_path)
        self.assertEqual(stored["123456"]["view_count"], 5000)

    def test_create_audio_tolerates_missing_metadata(self):
        downloaded = self.audio_directory / "789.mp3"
        downloaded.write_bytes(b"tiktok audio")
        with patch.object(
            api, "download_audio_with_metadata", return_value=(downloaded, {})
        ):
            response = api.create_audio(
                api.AudioCreate(url="https://www.tiktok.com/@user/video/789")
            )

        self.assertIsNone(response.views)
        self.assertEqual(
            audio_metrics.fetch_all_metrics(self.database_path), {}
        )

    def test_create_audio_maps_download_error_to_400(self):
        with patch.object(
            api,
            "download_audio_with_metadata",
            side_effect=api.AudioDownloadError("boom"),
        ):
            with self.assertRaises(HTTPException) as raised:
                api.create_audio(
                    api.AudioCreate(url="https://www.tiktok.com/@user/video/1")
                )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("boom", raised.exception.detail)

    def test_filters_caption_data_by_language(self):
        response = api.get_caption_data("pt")
        self.assertEqual(
            response.model_dump(),
            {
                "language": "pt",
                "carousel": "Her reply",
                "data": ["First", "Second"],
            },
        )

    def test_lists_outputs_newest_first(self):
        older = self.output_directory / "20260915_120000_1.mp4"
        newer = self.output_directory / "20260915_130000_2.mp4"
        older.write_bytes(b"old")
        newer.write_bytes(b"new video")
        (self.output_directory / f"{newer.stem}_screenshot.png").write_bytes(b"png")
        (self.output_directory / "ignore.txt").write_text("not video")
        os.utime(older, (1_700_000_000, 1_700_000_000))
        os.utime(newer, (1_800_000_000, 1_800_000_000))
        generation_assets.save_video_screenshot(
            self.generation_database_path,
            newer.stem,
            f"{newer.stem}_screenshot",
            npc_id="npc-1",
            clothes="default",
            client_url="http://client/play",
            width=540,
            height=960,
            npc_name="Name",
            description="Description",
            message="Message",
            actions=["Action"],
            language="en",
        )
        generation_assets.save_video_generation(
            self.generation_database_path,
            newer.stem,
            initial_video=2,
            initial_video_filename="2.mp4",
            final_video=1,
            final_video_filename="1.mp4",
            language="en",
            caption_index=3,
            caption="Caption",
            position="top",
            carousel=True,
            carousel_position="center",
            music=False,
            music_filename=None,
            music_volume=1.0,
        )

        outputs = [item.model_dump() for item in api.get_output_files()]
        self.assertEqual(
            [item["filename"] for item in outputs],
            ["20260915_130000_2.mp4", "20260915_120000_1.mp4"],
        )
        self.assertEqual(outputs[0]["id"], "20260915_130000_2")
        self.assertEqual(outputs[0]["type"], "ugc-reaction")
        self.assertEqual(outputs[0]["tags"], [])
        self.assertIsNone(outputs[0]["feedback"])
        self.assertEqual(outputs[0]["size_bytes"], 9)
        self.assertEqual(
            outputs[0]["url"],
            "/media/outputs/20260915_130000_2.mp4",
        )
        self.assertEqual(
            outputs[0]["download_url"],
            "/videos/20260915_130000_2/download",
        )
        self.assertEqual(
            outputs[0]["screenshot_id"],
            "20260915_130000_2_screenshot",
        )
        self.assertEqual(
            outputs[0]["screenshot_url"],
            "/screenshots/20260915_130000_2_screenshot/download",
        )
        self.assertEqual(outputs[0]["generation"]["initial_video"], 2)
        self.assertEqual(outputs[0]["generation"]["final_video"], 1)
        self.assertEqual(outputs[0]["generation"]["caption_index"], 3)
        self.assertIsNone(outputs[1]["screenshot_id"])
        self.assertIsNone(outputs[1]["screenshot_url"])
        self.assertIsNone(outputs[1]["generation"])

    def test_output_list_is_empty_when_directory_does_not_exist(self):
        with patch.object(api, "OUTPUT_DIRECTORY", self.output_directory / "missing"):
            self.assertEqual(api.get_output_files(), [])

    def test_lists_screenshot_mock_history(self):
        (self.output_directory / "video-1.mp4").write_bytes(b"video")
        (self.output_directory / "video-1_screenshot.png").write_bytes(b"png")
        generation_assets.save_video_screenshot(
            self.generation_database_path,
            "video-1",
            "video-1_screenshot",
            npc_id="npc-1",
            clothes="school_uniform",
            client_url="http://client/play",
            width=540,
            height=960,
            npc_name="ハナ",
            description="Scene",
            message="Message",
            actions=["First", "Second"],
            language="ja",
        )

        history = [item.model_dump() for item in api.get_screenshot_history()]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["video_id"], "video-1")
        self.assertEqual(history[0]["screenshot_id"], "video-1_screenshot")
        self.assertEqual(history[0]["language"], "ja")
        self.assertEqual(history[0]["actions"], ["First", "Second"])
        self.assertEqual(history[0]["video_url"], "/videos/video-1/download")
        self.assertEqual(
            history[0]["screenshot_url"],
            "/screenshots/video-1_screenshot/download",
        )
        self.assertEqual(len(api.get_screenshot_history("ja")), 1)
        self.assertEqual(api.get_screenshot_history("pt"), [])

    def test_screenshot_mock_history_is_empty_without_database(self):
        self.assertEqual(api.get_screenshot_history(), [])

    def test_create_video(self):
        generated = self.output_directory / "20260915_120000_1.mp4"
        generated.write_bytes(b"video")
        generated_screenshot = (
            self.output_directory / "20260915_120000_1_screenshot.png"
        )
        generated_screenshot.write_bytes(b"png")
        with (
            patch.object(api, "default_output_path", return_value=generated),
            patch.object(
                api,
                "generate_video",
                return_value=(2, "caption", generated),
            ) as generate,
            patch.object(
                api, "capture_screenshot", return_value=generated_screenshot
            ) as capture,
        ):
            response = api.create_video(
                api.VideoCreate(
                    language="pt",
                    index=2,
                    position="center",
                    video=2,
                    final=2,
                    carousel=True,
                    carousel_position="bottom",
                    music=True,
                    music_filename="second.m4a",
                    music_volume=0.15,
                    client_url="http://client/play",
                    screenshot_width=540,
                    screenshot_height=960,
                    npc_id="npc-123",
                    clothes="school_uniform",
                    npc_name="ハナ",
                    description="Scene",
                    message="Message",
                    actions=["First", "Second"],
                ),
                self.request,
            )

        self.assertEqual(response.id, "20260915_120000_1")
        self.assertEqual(response.type, "ugc-reaction")
        self.assertEqual(response.caption, "caption")
        self.assertEqual(response.video, 2)
        self.assertEqual(response.final, 2)
        self.assertTrue(response.carousel)
        self.assertEqual(response.carousel_position, "bottom")
        self.assertTrue(response.music)
        self.assertEqual(response.music_filename, "second.m4a")
        self.assertEqual(response.music_volume, 0.15)
        self.assertEqual(response.screenshot_id, "20260915_120000_1_screenshot")
        self.assertEqual(
            response.screenshot_url,
            "http://test/screenshots/20260915_120000_1_screenshot/download",
        )
        self.assertEqual(
            generate.call_args.kwargs["first_path"],
            (self.video_directory / "3.mp4").resolve(),
        )
        self.assertEqual(
            generate.call_args.kwargs["final_path"],
            (self.final_directory / "2.mp4").resolve(),
        )
        self.assertTrue(generate.call_args.kwargs["carousel"])
        self.assertEqual(
            generate.call_args.kwargs["carousel_position"], "bottom"
        )
        self.assertEqual(
            generate.call_args.kwargs["music_path"],
            (self.audio_directory / "second.m4a").resolve(),
        )
        self.assertEqual(generate.call_args.kwargs["music_volume"], 0.15)
        generate.assert_called_once()
        capture.assert_called_once_with(
            "http://client/play",
            540,
            960,
            generated_screenshot,
            npc_id="npc-123",
            clothes="school_uniform",
            mock_data={
                "npcName": "ハナ",
                "description": "Scene",
                "message": "Message",
                "actions": ["First", "Second"],
            },
            javascript=api.read_javascript(api.SCREENSHOT_SCRIPT),
        )
        association = generation_assets.fetch_by_video_id(
            self.generation_database_path, "20260915_120000_1"
        )
        self.assertEqual(
            association["screenshot_id"], "20260915_120000_1_screenshot"
        )
        self.assertEqual(association["clothes"], "school_uniform")
        generation_metadata = generation_assets.fetch_generation_by_video_id(
            self.generation_database_path, "20260915_120000_1"
        )
        self.assertEqual(generation_metadata["initial_video"], 2)
        self.assertEqual(generation_metadata["initial_video_filename"], "3.mp4")
        self.assertEqual(generation_metadata["final_video"], 2)
        self.assertEqual(generation_metadata["final_video_filename"], "2.mp4")
        self.assertEqual(generation_metadata["language"], "pt")
        self.assertEqual(generation_metadata["caption_index"], 2)
        self.assertEqual(generation_metadata["caption"], "caption")
        self.assertEqual(generation_metadata["position"], "center")
        self.assertTrue(generation_metadata["carousel"])
        self.assertTrue(generation_metadata["music"])
        self.assertEqual(generation_metadata["music_filename"], "second.m4a")
        metadata = api.video_metadata.fetch(
            self.generation_database_path, response.id
        )
        self.assertEqual(metadata["type"], "ugc-reaction")
        self.assertEqual(metadata["request_body"]["language"], "pt")

    def test_status_and_download(self):
        generated = self.output_directory / "20260915_120000_1.mp4"
        generated.write_bytes(b"video")

        status_response = api.get_video_status(
            "20260915_120000_1", self.request
        )
        self.assertEqual(status_response.status, "completed")

        download_response = api.download_video("20260915_120000_1")
        self.assertEqual(Path(download_response.path), generated)
        self.assertEqual(download_response.media_type, "video/mp4")

    def test_status_and_download_include_associated_screenshot(self):
        generated = self.output_directory / "20260915_120000_1.mp4"
        screenshot = self.output_directory / "20260915_120000_1_screenshot.png"
        generated.write_bytes(b"video")
        screenshot.write_bytes(b"png")
        generation_assets.save_video_screenshot(
            self.generation_database_path,
            generated.stem,
            screenshot.stem,
            npc_id="npc-1",
            clothes="default",
            client_url="http://client/play",
            width=1080,
            height=1920,
            npc_name="Name",
            description="Description",
            message="Message",
            actions=["Action"],
            language="pt",
        )
        generation_assets.save_video_generation(
            self.generation_database_path,
            generated.stem,
            initial_video=1,
            initial_video_filename="1.mp4",
            final_video=2,
            final_video_filename="2.mp4",
            language="pt",
            caption_index=1,
            caption="Caption",
            position="top",
            carousel=True,
            carousel_position="top",
            music=False,
            music_filename=None,
            music_volume=1.0,
        )

        response = api.get_video_status(generated.stem, self.request)
        self.assertEqual(response.screenshot_id, screenshot.stem)
        self.assertEqual(
            response.screenshot_url,
            f"http://test/screenshots/{screenshot.stem}/download",
        )
        self.assertEqual(response.generation.initial_video, 1)
        self.assertEqual(response.generation.final_video, 2)
        download = api.download_screenshot(screenshot.stem)
        self.assertEqual(Path(download.path), screenshot)
        self.assertEqual(download.media_type, "image/png")

    def test_screenshot_failure_removes_carousel_outputs(self):
        generated = self.output_directory / "20260915_120000_1.mp4"
        generated.write_bytes(b"video")
        partial_screenshot = (
            self.output_directory / "20260915_120000_1_screenshot.png"
        )
        partial_screenshot.write_bytes(b"partial")
        with (
            patch.object(api, "default_output_path", return_value=generated),
            patch.object(
                api, "generate_video", return_value=(1, "caption", generated)
            ),
            patch.object(
                api,
                "capture_screenshot",
                side_effect=api.ScreenshotError("capture failed"),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                api.create_video(
                    api.VideoCreate(
                        carousel=True,
                        npc_id="npc-1",
                        clothes="default",
                        npc_name="Name",
                        description="Description",
                        message="Message",
                        actions=["Action"],
                    ),
                    self.request,
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertFalse(generated.exists())
        self.assertFalse(partial_screenshot.exists())
        self.assertIsNone(
            generation_assets.fetch_by_video_id(
                self.generation_database_path, generated.stem
            )
        )

    def test_missing_video_returns_404(self):
        with self.assertRaises(HTTPException) as raised:
            api.video_path("missing")
        self.assertEqual(raised.exception.status_code, 404)

    def test_rejects_invalid_payload(self):
        with self.assertRaises(ValidationError):
            api.VideoCreate(language="invalid", position="top")

    def test_carousel_requires_screenshot_fields(self):
        with self.assertRaisesRegex(ValidationError, "npc_id"):
            api.VideoCreate(carousel=True)

    def test_screenshot_defaults(self):
        payload = api.VideoCreate()
        self.assertEqual(payload.client_url, "http://127.0.0.1:3000/play")
        self.assertEqual(payload.screenshot_width, 540)
        self.assertEqual(payload.screenshot_height, 960)

    def test_rejects_missing_final_video(self):
        with self.assertRaises(HTTPException) as raised:
            api.create_video(api.VideoCreate(final=3), self.request)
        self.assertEqual(raised.exception.status_code, 400)

    def test_rejects_missing_initial_video(self):
        with self.assertRaises(HTTPException) as raised:
            api.create_video(api.VideoCreate(video=3), self.request)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Available: 1, 2", raised.exception.detail)

    def test_rejects_missing_audio_filename(self):
        with self.assertRaises(HTTPException) as raised:
            api.create_video(
                api.VideoCreate(music=True, music_filename="missing.mp3"),
                self.request,
            )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Audio file not found", raised.exception.detail)

    def test_rejects_audio_path_traversal(self):
        with self.assertRaisesRegex(api.VideoMakerError, "Invalid music filename"):
            api.resolve_audio_file("../secret.mp3")

    def test_music_disabled_ignores_filename(self):
        generated = self.output_directory / "20260915_120001_1.mp4"
        generated.write_bytes(b"video")
        with (
            patch.object(api, "default_output_path", return_value=generated),
            patch.object(
                api, "generate_video", return_value=(1, "caption", generated)
            ) as generate,
        ):
            response = api.create_video(
                api.VideoCreate(music=False, music_filename="missing.mp3"),
                self.request,
            )
        self.assertIsNone(response.music_filename)
        self.assertIsNone(generate.call_args.kwargs["music_path"])
        generation = generation_assets.fetch_generation_by_video_id(
            self.generation_database_path, generated.stem
        )
        self.assertIsNotNone(generation)
        self.assertFalse(generation["carousel"])
        self.assertFalse(generation["music"])

    def test_music_enabled_without_filename_adds_no_music(self):
        generated = self.output_directory / "20260915_120002_1.mp4"
        generated.write_bytes(b"video")
        with (
            patch.object(api, "default_output_path", return_value=generated),
            patch.object(
                api, "generate_video", return_value=(1, "caption", generated)
            ) as generate,
        ):
            response = api.create_video(
                api.VideoCreate(music=True, music_filename=None),
                self.request,
            )
        self.assertFalse(response.music)
        self.assertIsNone(response.music_filename)
        self.assertIsNone(generate.call_args.kwargs["music_path"])


if __name__ == "__main__":
    unittest.main()
