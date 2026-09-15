import tempfile
import unittest
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import api
    import audio_metrics
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
        default_music = self.audio_directory / "default.mp3"
        default_music.write_bytes(b"default audio")
        (self.audio_directory / "second.m4a").write_bytes(b"other")
        (self.audio_directory / "ignore.txt").write_text("not audio")
        self.audio_directory_patch = patch.object(
            api, "AUDIO_DIRECTORY", self.audio_directory
        )
        self.music_file_patch = patch.object(api, "MUSIC_FILE", default_music)
        self.audio_directory_patch.start()
        self.music_file_patch.start()
        self.addCleanup(self.audio_directory_patch.stop)
        self.addCleanup(self.music_file_patch.stop)
        self.database_path = Path(self.temp_directory.name) / "audio_metrics.db"
        self.database_path_patch = patch.object(
            api, "DATABASE_PATH", self.database_path
        )
        self.database_path_patch.start()
        self.addCleanup(self.database_path_patch.stop)
        self.caption_data = Path(self.temp_directory.name) / "data.json"
        self.caption_data.write_text(
            json.dumps(
                {
                    "pt": {
                        "carousel": "Resposta dela",
                        "data": ["Primeira", "Segunda"],
                    }
                }
            ),
            encoding="utf-8",
        )
        self.caption_data_patch = patch.object(api, "CAPTION_DATA", self.caption_data)
        self.caption_data_patch.start()
        self.addCleanup(self.caption_data_patch.stop)
        self.request = Mock()
        self.request.url_for.side_effect = (
            lambda _name, video_id: f"http://test/videos/{video_id}/download"
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

    def test_lists_numbered_initial_videos(self):
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
                    "number": 3,
                    "filename": "3.mp4",
                    "size_bytes": 11,
                    "url": "/media/videos/3.mp4",
                },
            ],
        )

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
                    "is_default": True,
                    "url": "/media/audios/default.mp3",
                    "views": None,
                    "likes": None,
                    "comments": None,
                    "shares": None,
                },
                {
                    "filename": "second.m4a",
                    "size_bytes": 5,
                    "is_default": False,
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
                "carousel": "Resposta dela",
                "data": ["Primeira", "Segunda"],
            },
        )

    def test_lists_outputs_newest_first(self):
        older = self.output_directory / "20260915_120000_1.mp4"
        newer = self.output_directory / "20260915_130000_2.mp4"
        older.write_bytes(b"old")
        newer.write_bytes(b"new video")
        (self.output_directory / "ignore.txt").write_text("not video")
        os.utime(older, (1_700_000_000, 1_700_000_000))
        os.utime(newer, (1_800_000_000, 1_800_000_000))

        outputs = [item.model_dump() for item in api.get_output_files()]
        self.assertEqual(
            [item["filename"] for item in outputs],
            ["20260915_130000_2.mp4", "20260915_120000_1.mp4"],
        )
        self.assertEqual(outputs[0]["id"], "20260915_130000_2")
        self.assertEqual(outputs[0]["size_bytes"], 9)
        self.assertEqual(
            outputs[0]["url"],
            "/media/outputs/20260915_130000_2.mp4",
        )
        self.assertEqual(
            outputs[0]["download_url"],
            "/videos/20260915_130000_2/download",
        )

    def test_output_list_is_empty_when_directory_does_not_exist(self):
        with patch.object(api, "OUTPUT_DIRECTORY", self.output_directory / "missing"):
            self.assertEqual(api.get_output_files(), [])

    def test_create_video(self):
        generated = self.output_directory / "20260915_120000_1.mp4"
        generated.write_bytes(b"video")
        with (
            patch.object(api, "default_output_path", return_value=generated),
            patch.object(
                api,
                "generate_video",
                return_value=(2, "caption", generated),
            ) as generate,
        ):
            response = api.create_video(
                api.VideoCreate(
                    language="pt",
                    index=2,
                    position="center",
                    video=3,
                    final=2,
                    carousel=True,
                    carousel_position="bottom",
                    music=True,
                    music_filename="second.m4a",
                    music_volume=0.15,
                ),
                self.request,
            )

        self.assertEqual(response.id, "20260915_120000_1")
        self.assertEqual(response.caption, "caption")
        self.assertEqual(response.video, 3)
        self.assertEqual(response.final, 2)
        self.assertTrue(response.carousel)
        self.assertEqual(response.carousel_position, "bottom")
        self.assertTrue(response.music)
        self.assertEqual(response.music_filename, "second.m4a")
        self.assertEqual(response.music_volume, 0.15)
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

    def test_missing_video_returns_404(self):
        with self.assertRaises(HTTPException) as raised:
            api.video_path("missing")
        self.assertEqual(raised.exception.status_code, 404)

    def test_rejects_invalid_payload(self):
        with self.assertRaises(ValidationError):
            api.VideoCreate(language="invalid", position="top")

    def test_rejects_missing_final_video(self):
        with self.assertRaises(HTTPException) as raised:
            api.create_video(api.VideoCreate(final=3), self.request)
        self.assertEqual(raised.exception.status_code, 400)

    def test_rejects_missing_initial_video(self):
        with self.assertRaises(HTTPException) as raised:
            api.create_video(api.VideoCreate(video=2), self.request)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Available: 1, 3", raised.exception.detail)

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


if __name__ == "__main__":
    unittest.main()
