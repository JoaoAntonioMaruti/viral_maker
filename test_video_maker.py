import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from video_maker import (
    VideoMakerError,
    ass_timestamp,
    create_ass,
    create_carousel_overlay,
    default_output_path,
    escape_ass_text,
    load_captions,
    list_final_videos,
    load_carousel_caption,
    make_video,
    prompt_for_music,
    prompt_for_music_url,
    resolve_final_video,
    resolve_cli_music,
    select_caption,
    wrap_cjk_text,
)


class CaptionTests(unittest.TestCase):
    def test_loads_requested_language(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text(json.dumps({"pt": {"data": ["uma", "duas"]}}))
            self.assertEqual(load_captions(path, "pt"), ["uma", "duas"])

    def test_rejects_missing_language(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text(json.dumps({"en": {"data": ["one"]}}))
            with self.assertRaisesRegex(VideoMakerError, "Missing data"):
                load_captions(path, "ja")

    def test_selects_one_based_index(self):
        self.assertEqual(select_caption(["one", "two"], 2), (2, "two"))

    def test_rejects_invalid_index(self):
        with self.assertRaisesRegex(VideoMakerError, "between 1 and 2"):
            select_caption(["one", "two"], 0)

    @patch("video_maker.random.randrange", return_value=1)
    def test_random_selection(self, random_range):
        self.assertEqual(select_caption(["one", "two"], None), (2, "two"))
        random_range.assert_called_once_with(2)

    def test_default_output_uses_timestamp_and_video_number(self):
        moment = datetime(2026, 9, 15, 14, 30, 52)
        self.assertEqual(
            default_output_path(Path("videos/12.mp4"), moment),
            Path("outputs/20260915_143052_12.mp4"),
        )

    def test_loads_carousel_caption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text(
                json.dumps({"pt": {"carousel": "Resposta dela >>>"}})
            )
            self.assertEqual(
                load_carousel_caption(path, "pt"), "Resposta dela >>>"
            )

    def test_rejects_missing_carousel_caption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text(json.dumps({"pt": {"data": ["texto"]}}))
            with self.assertRaisesRegex(VideoMakerError, "Missing carousel"):
                load_carousel_caption(path, "pt")


class SubtitleTests(unittest.TestCase):
    def test_formats_ass_time(self):
        self.assertEqual(ass_timestamp(65.432), "0:01:05.43")

    def test_escapes_ass_control_characters(self):
        self.assertEqual(escape_ass_text("a{b}\\c\nd"), r"a\{b\}\\c\Nd")

    def test_hard_wraps_japanese_text(self):
        self.assertEqual(
            wrap_cjk_text("あ" * 17),
            f"{'あ' * 16}\nあ",
        )
        self.assertEqual(wrap_cjk_text("Her reply"), "Her reply")

    def test_positions_map_to_ass_alignments(self):
        self.assertIn(",8,90,90,170,1", create_ass("top", 4, "top"))
        self.assertIn(",5,90,90,0,1", create_ass("center", 4, "center"))
        self.assertIn(",2,90,90,300,1", create_ass("bottom", 4, "bottom"))


class FinalVideoTests(unittest.TestCase):
    @patch(
        "video_maker._file_creation_time_ns",
        side_effect=lambda path: {"first clip.mp4": 1, "10.mp4": 2}[path.name],
    )
    def test_lists_any_mp4_and_assigns_numbers_by_creation_order(self, _creation_time):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first clip.mp4").touch()
            (root / "10.mp4").touch()
            (root / "ignore.txt").touch()
            self.assertEqual(
                [(number, path.name) for number, path in list_final_videos(root)],
                [(1, "first clip.mp4"), (2, "10.mp4")],
            )

    @patch(
        "video_maker._file_creation_time_ns",
        side_effect=lambda path: {"first.mp4": 1, "any-name.mp4": 2}[path.name],
    )
    def test_resolves_selected_final_video(self, _creation_time):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "first.mp4").touch()
            expected = root / "any-name.mp4"
            expected.touch()
            self.assertEqual(resolve_final_video(root, 2), expected.resolve())

    def test_rejects_unavailable_final_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "1.mp4").touch()
            with self.assertRaisesRegex(VideoMakerError, "Available: 1"):
                resolve_final_video(root, 2)


class FfmpegCommandTests(unittest.TestCase):
    @patch("video_maker.subprocess.run")
    def test_output_explicitly_discards_audio(self, run_process):
        make_video(
            "ffmpeg",
            Path("first.mp4"),
            Path("final.mp4"),
            Path("caption.ass"),
            Path("output.mp4"),
            False,
        )

        command = run_process.call_args.args[0]
        self.assertIn("-an", command)
        filter_graph = command[command.index("-filter_complex") + 1]
        self.assertIn("concat=n=2:v=1:a=0", filter_graph)

    @patch("video_maker.subprocess.run")
    def test_adds_carousel_image_to_final_video(self, run_process):
        make_video(
            "ffmpeg",
            Path("first.mp4"),
            Path("final.mp4"),
            Path("caption.ass"),
            Path("output.mp4"),
            False,
            Path("carousel.png"),
            carousel_subtitle=Path("carousel.ass"),
        )

        command = run_process.call_args.args[0]
        filter_graph = command[command.index("-filter_complex") + 1]
        self.assertIn("[1:v]scale=", filter_graph)
        self.assertIn("ass=filename='carousel.ass'[endingcaptioned]", filter_graph)
        self.assertIn("[endingcaptioned][2:v]overlay=", filter_graph)
        self.assertIn("x=main_w/2", filter_graph)
        self.assertIn("y=190:shortest=1[ending]", filter_graph)

    @patch("video_maker.subprocess.run")
    def test_carousel_image_contains_only_three_arrows(self, run_process):
        create_carousel_overlay(
            "magick",
            "Resposta dela",
            Path("right-arrow.png"),
            Path("carousel.png"),
        )

        command = run_process.call_args.args[0]
        self.assertEqual(command[command.index("-pointsize") + 1], "76")
        self.assertIn("label:Resposta dela", command)
        self.assertIn("35%x1+0+0", command)
        self.assertEqual(command[command.index("-resize") + 1], "52x52")
        self.assertEqual(command.count("right-arrow.png"), 3)

    @patch("video_maker.subprocess.run")
    def test_replaces_original_audio_with_looped_background_music(self, run_process):
        make_video(
            "ffmpeg",
            Path("first.mp4"),
            Path("final.mp4"),
            Path("caption.ass"),
            Path("output.mp4"),
            False,
            music=Path("music.mp3"),
            music_volume=0.2,
        )

        command = run_process.call_args.args[0]
        self.assertEqual(command[command.index("-stream_loop") + 1], "-1")
        self.assertIn("[aout]", command)
        self.assertIn("-shortest", command)
        self.assertNotIn("-an", command)
        filter_graph = command[command.index("-filter_complex") + 1]
        self.assertIn("[2:a]volume=0.2000", filter_graph)


class MusicPromptTests(unittest.TestCase):
    def test_default_music_volume_is_full(self):
        parser = __import__("video_maker").build_parser()
        self.assertEqual(parser.parse_args(["--no-music"]).music_volume, 1.0)

    def test_empty_answer_accepts_default_music(self):
        self.assertTrue(prompt_for_music(lambda _prompt: ""))

    def test_accepts_portuguese_yes(self):
        self.assertTrue(prompt_for_music(lambda _prompt: "sim"))

    def test_accepts_no(self):
        self.assertFalse(prompt_for_music(lambda _prompt: "n"))

    def test_requires_non_empty_music_url(self):
        answers = iter(["", "https://vm.tiktok.com/example/"])
        self.assertEqual(
            prompt_for_music_url(lambda _prompt: next(answers)),
            "https://vm.tiktok.com/example/",
        )

    def test_yes_downloads_tiktok_audio(self):
        parser = __import__("video_maker").build_parser()
        args = parser.parse_args([])
        answers = iter(["y", "https://vm.tiktok.com/example/"])
        calls = []

        def downloader(url):
            calls.append(url)
            return Path("audio/123.mp3")

        selected = resolve_cli_music(
            args, lambda _prompt: next(answers), downloader=downloader
        )
        self.assertEqual(selected, Path("audio/123.mp3"))
        self.assertEqual(calls, ["https://vm.tiktok.com/example/"])

    def test_music_url_downloads_without_prompt(self):
        parser = __import__("video_maker").build_parser()
        args = parser.parse_args(
            ["--music-url", "https://vm.tiktok.com/example/"]
        )

        selected = resolve_cli_music(
            args,
            lambda _prompt: self.fail("prompt should not be called"),
            downloader=lambda _url: Path("audio/456.mp3"),
        )
        self.assertEqual(selected, Path("audio/456.mp3"))

    def test_explicit_flags_skip_prompt(self):
        parser = __import__("video_maker").build_parser()
        without_music = parser.parse_args(["--no-music"])
        custom_music = parser.parse_args(["--music", "audio/custom.mp3"])

        def unexpected_prompt(_prompt):
            self.fail("prompt should not be called")

        self.assertIsNone(resolve_cli_music(without_music, unexpected_prompt))
        self.assertEqual(
            resolve_cli_music(custom_music, unexpected_prompt),
            Path("audio/custom.mp3"),
        )


if __name__ == "__main__":
    unittest.main()
