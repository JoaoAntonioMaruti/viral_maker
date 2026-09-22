import tempfile
import unittest
from pathlib import Path

import video_metadata


class VideoMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.database = Path(self.temp_directory.name) / "metadata.db"

    def test_stores_reproducible_request_body_and_type(self):
        body = {"schemaVersion": 1, "message": "こんにちは"}
        video_metadata.upsert_video(
            self.database,
            "chat-1",
            "chat-video",
            request_body=body,
        )

        stored = video_metadata.fetch(self.database, "chat-1")

        self.assertEqual(stored["type"], "chat-video")
        self.assertEqual(stored["request_body"], body)

    def test_tags_are_unique_case_insensitively(self):
        video_metadata.upsert_video(self.database, "video-1", "ugc-reaction")

        result = video_metadata.add_tags(
            self.database,
            "video-1",
            ["Anime", "anime", " Viral ", "viral"],
        )

        self.assertEqual(result["tags"], ["Anime", "Viral"])

    def test_removes_tag_case_insensitively_and_is_idempotent(self):
        video_metadata.upsert_video(self.database, "video-1", "ugc-reaction")
        video_metadata.add_tags(self.database, "video-1", ["Anime", "Viral"])

        removed = video_metadata.remove_tag(self.database, "video-1", " anime ")
        repeated = video_metadata.remove_tag(self.database, "video-1", "ANIME")

        self.assertEqual(removed["tags"], ["Viral"])
        self.assertEqual(repeated["tags"], ["Viral"])

    def test_feedback_can_change_and_be_cleared(self):
        video_metadata.upsert_video(self.database, "video-1", "ugc-reaction")

        liked = video_metadata.set_feedback(self.database, "video-1", "thumbsup")
        disliked = video_metadata.set_feedback(
            self.database, "video-1", "thumbsdown"
        )
        cleared = video_metadata.set_feedback(self.database, "video-1", None)

        self.assertEqual(liked["feedback"], "thumbsup")
        self.assertEqual(disliked["feedback"], "thumbsdown")
        self.assertIsNone(cleared["feedback"])

    def test_infers_legacy_video_types(self):
        self.assertEqual(video_metadata.infer_video_type("chat-123"), "chat-video")
        self.assertEqual(video_metadata.infer_video_type("old-output"), "ugc-reaction")


if __name__ == "__main__":
    unittest.main()
