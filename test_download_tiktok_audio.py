import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from download_tiktok_audio import (
    AudioDownloadError,
    build_download_command,
    download_audio,
    validate_tiktok_url,
)


class TikTokUrlTests(unittest.TestCase):
    def test_accepts_regular_and_short_tiktok_urls(self):
        urls = [
            "https://www.tiktok.com/@creator/video/123",
            "https://vm.tiktok.com/abc123/",
            "https://vt.tiktok.com/abc123/",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(validate_tiktok_url(url), url)

    def test_rejects_non_tiktok_url(self):
        with self.assertRaisesRegex(AudioDownloadError, "tiktok.com"):
            validate_tiktok_url("https://example.com/video/123")

    def test_rejects_url_without_http_scheme(self):
        with self.assertRaisesRegex(AudioDownloadError, "http"):
            validate_tiktok_url("www.tiktok.com/@creator/video/123")


class DownloadCommandTests(unittest.TestCase):
    def test_builds_mp3_extraction_command(self):
        command = build_download_command(
            "/usr/bin/yt-dlp",
            "https://vm.tiktok.com/example/",
            Path("/tmp/audio"),
            Path("/tmp/report.txt"),
            cookies_from_browser="firefox",
        )
        self.assertIn("--extract-audio", command)
        self.assertEqual(command[command.index("--audio-format") + 1], "mp3")
        self.assertEqual(command[command.index("--paths") + 1], "/tmp/audio")
        self.assertIn("--no-overwrites", command)
        self.assertIn("--progress", command)
        self.assertIn("--progress-template", command)
        self.assertEqual(
            command[command.index("--print-to-file") + 2], "/tmp/report.txt"
        )
        self.assertEqual(
            command[command.index("--cookies-from-browser") + 1], "firefox"
        )

    @patch("download_tiktok_audio.require_program")
    @patch("download_tiktok_audio.subprocess.run")
    def test_returns_downloaded_file(self, run_process, require_program):
        require_program.side_effect = lambda name: f"/usr/bin/{name}"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            downloaded = destination / "123.mp3"
            downloaded.write_bytes(b"audio")

            def report_download(command, check):
                report_file = Path(command[command.index("--print-to-file") + 2])
                report_file.write_text(f"{downloaded}\n", encoding="utf-8")

            run_process.side_effect = report_download

            result = download_audio(
                "https://www.tiktok.com/@creator/video/123", destination
            )

        self.assertEqual(result, downloaded)
        run_process.assert_called_once()


if __name__ == "__main__":
    unittest.main()
