import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    import api
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
        self.final_directory = Path(self.temp_directory.name) / "finals"
        self.final_directory.mkdir()
        (self.final_directory / "1.mp4").write_bytes(b"final one")
        (self.final_directory / "2.mp4").write_bytes(b"final two")
        self.final_patch = patch.object(
            api, "FINAL_VIDEO_DIRECTORY", self.final_directory
        )
        self.final_patch.start()
        self.addCleanup(self.final_patch.stop)
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
        self.assertNotIn(("/videos", "POST"), routes)

    def test_default_music_volume_is_full(self):
        self.assertEqual(api.VideoCreate().music_volume, 1.0)

    def test_lists_final_videos(self):
        self.assertEqual(
            [video.model_dump() for video in api.get_final_videos()],
            [
                {"number": 1, "filename": "1.mp4"},
                {"number": 2, "filename": "2.mp4"},
            ],
        )

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
                    final=2,
                    carousel=True,
                    carousel_position="bottom",
                    music=True,
                    music_volume=0.15,
                ),
                self.request,
            )

        self.assertEqual(response.id, "20260915_120000_1")
        self.assertEqual(response.caption, "caption")
        self.assertEqual(response.final, 2)
        self.assertTrue(response.carousel)
        self.assertEqual(response.carousel_position, "bottom")
        self.assertTrue(response.music)
        self.assertEqual(response.music_volume, 0.15)
        self.assertEqual(
            generate.call_args.kwargs["final_path"],
            (self.final_directory / "2.mp4").resolve(),
        )
        self.assertTrue(generate.call_args.kwargs["carousel"])
        self.assertEqual(
            generate.call_args.kwargs["carousel_position"], "bottom"
        )
        self.assertEqual(generate.call_args.kwargs["music_path"], api.MUSIC_FILE)
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


if __name__ == "__main__":
    unittest.main()
