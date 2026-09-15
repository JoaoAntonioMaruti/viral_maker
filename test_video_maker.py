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
    default_output_path,
    escape_ass_text,
    load_captions,
    list_final_videos,
    resolve_final_video,
    select_caption,
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


class SubtitleTests(unittest.TestCase):
    def test_formats_ass_time(self):
        self.assertEqual(ass_timestamp(65.432), "0:01:05.43")

    def test_escapes_ass_control_characters(self):
        self.assertEqual(escape_ass_text("a{b}\\c\nd"), r"a\{b\}\\c\Nd")

    def test_positions_map_to_ass_alignments(self):
        self.assertIn(",8,90,90,170,1", create_ass("top", 4, "top"))
        self.assertIn(",5,90,90,0,1", create_ass("center", 4, "center"))
        self.assertIn(",2,90,90,300,1", create_ass("bottom", 4, "bottom"))


class FinalVideoTests(unittest.TestCase):
    def test_lists_and_sorts_numbered_final_videos(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "10.mp4").touch()
            (root / "2.mp4").touch()
            (root / "other.mp4").touch()
            self.assertEqual(
                [number for number, _path in list_final_videos(root)], [2, 10]
            )

    def test_resolves_selected_final_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "2.mp4"
            expected.touch()
            self.assertEqual(resolve_final_video(root, 2), expected.resolve())

    def test_rejects_unavailable_final_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "1.mp4").touch()
            with self.assertRaisesRegex(VideoMakerError, "Available: 1"):
                resolve_final_video(root, 2)


if __name__ == "__main__":
    unittest.main()
