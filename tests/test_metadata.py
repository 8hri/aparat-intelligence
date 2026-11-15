"""Tests: metadata normalization."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import metadata as metadata_mod


class TestNormalization(unittest.TestCase):
    def test_yt_dlp_dict_normalized(self):
        raw = {
            "id": "wP8On",
            "title": "  تست  ",
            "description": "  ",
            "uploader": "کانال",
            "duration": 231,
            "thumbnail": "https://x/t.jpg",
            "view_count": 99,
            "upload_date": "20261114",
            "webpage_url": "https://www.aparat.com/v/wP8On",
            "extractor_key": "Aparat",
            "formats": [
                {"format_id": "http-720p", "height": 720, "ext": "mp4",
                 "filesize": 12345, "vcodec": "h264", "acodec": "none"},
            ],
        }
        m = metadata_mod.normalize(raw)
        self.assertEqual(m["video_id"], "wP8On")
        self.assertEqual(m["title"], "تست")
        self.assertIsNone(m["description"])  # empty string -> None
        self.assertEqual(m["duration"], "03:51")
        self.assertEqual(m["upload_date"], "2026-11-14")
        self.assertEqual(len(m["formats"]), 1)
        self.assertEqual(m["formats"][0]["height"], 720)
        self.assertEqual(m["extractor"], "Aparat")

    def test_duration_formatting(self):
        self.assertEqual(metadata_mod._duration_str(0), "00:00")
        self.assertEqual(metadata_mod._duration_str(65), "01:05")
        self.assertEqual(metadata_mod._duration_str(3725), "01:02:05")
        self.assertIsNone(metadata_mod._duration_str(None))
        self.assertIsNone(metadata_mod._duration_str("abc"))

    def test_scrape_dict_passthrough(self):
        raw = {"id": "X", "title": "t", "duration": "60",
               "extractor": "web-scrape"}
        m = metadata_mod.normalize(raw)
        self.assertEqual(m["duration_seconds"], 60)
        self.assertEqual(m["duration"], "01:00")

    def test_brief(self):
        m = metadata_mod.normalize({"id": "X", "title": "عنوان طولانی" * 5})
        b = metadata_mod.brief(m)
        self.assertIn("id=X", b)

    def test_empty_input(self):
        m = metadata_mod.normalize({})
        self.assertIsNone(m["title"])
        self.assertEqual(m["formats"], [])


if __name__ == "__main__":
    unittest.main()
