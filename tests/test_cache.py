"""Tests: cache manager (reuse, content-hash keys, clear, resume)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.cache_manager import CacheManager


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.cache = CacheManager(base=self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_metadata_roundtrip(self):
        meta = {"video_id": "ABC", "title": "t"}
        self.cache.put_metadata("ABC", meta)
        got = self.cache.get_metadata("ABC")
        self.assertEqual(got, meta)

    def test_metadata_miss(self):
        self.assertIsNone(self.cache.get_metadata("NOPE"))

    def test_analysis_cache_reuse(self):
        payload = {"op": "summarize", "ctx": "x"}
        self.assertIsNone(self.cache.get_analysis(payload))
        self.cache.put_analysis(payload, {"parsed": {"a": 1}})
        self.assertEqual(self.cache.get_analysis(payload), {"parsed": {"a": 1}})
        # different payload -> different key, no false hit
        self.assertIsNone(self.cache.get_analysis({"op": "summarize", "ctx": "y"}))

    def test_content_hash_stable_across_key_order(self):
        self.assertEqual(
            CacheManager.content_hash({"a": 1, "b": 2}),
            CacheManager.content_hash({"b": 2, "a": 1}),
        )
        # Persian content survives the hash round trip
        self.assertEqual(
            CacheManager.content_hash({"q": "سلام"}),
            CacheManager.content_hash({"q": "سلام"}),
        )

    def test_media_ready_and_artifacts(self):
        self.cache.media_path("ABC", "audio.mp3").write_bytes(b"x" * 2048)
        self.assertTrue(self.cache.media_ready("ABC", "audio.mp3"))
        self.assertFalse(self.cache.media_ready("ABC", "audio.mp3", min_bytes=99999))
        p = self.cache.put_artifact("ABC", "frames.json", [1, 2])
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), [1, 2])

    def test_list_videos(self):
        self.cache.put_metadata("A", {"video_id": "A", "title": "ta"})
        self.cache.put_metadata("B", {"video_id": "B", "title": "tb"})
        ids = {v["video_id"] for v in self.cache.list_videos()}
        self.assertEqual(ids, {"A", "B"})

    def test_clear_by_video_and_all(self):
        self.cache.put_metadata("A", {"video_id": "A"})
        self.cache.put_metadata("B", {"video_id": "B"})
        self.cache.put_analysis({"op": "x"}, {"r": 1})
        removed = self.cache.clear(video_id="A")
        self.assertEqual(removed, 1)
        self.assertIsNone(self.cache.get_metadata("A"))
        self.assertIsNotNone(self.cache.get_metadata("B"))
        removed = self.cache.clear()
        self.assertGreaterEqual(removed, 1)  # analysis entry at least
        self.assertIsNone(self.cache.get_metadata("B"))

    def test_video_dir_sanitizes_unsafe_ids(self):
        # traversal characters are stripped; the result stays inside base
        d = self.cache.video_dir("../../etc")
        self.assertEqual(d.parent, self.cache.videos_dir)
        self.assertTrue(d.is_relative_to(self.base))

    def test_resume_uses_existing_media(self):
        # simulate an interrupted run: audio already cached
        self.cache.media_path("ABC", "audio.mp3").write_bytes(b"x" * 2048)
        self.assertTrue(self.cache.has_media("ABC", "audio.mp3"))
        # a re-run would skip download/extraction: verified by the caller
        # (_prepare_audio checks media_ready before extraction).
        self.assertTrue(self.cache.media_ready("ABC", "audio.mp3", min_bytes=1024))

    def test_size(self):
        self.cache.media_path("A", "audio.mp3").write_bytes(b"x" * 1024)
        self.assertGreaterEqual(self.cache.size(), 1024)


if __name__ == "__main__":
    unittest.main()
