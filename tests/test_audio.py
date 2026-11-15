"""Tests: audio extraction and chunking (offline, synthetic ffmpeg video)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import make_test_video

from scripts import audio_processor


class TestAudio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.video = make_test_video(Path("/tmp/aparat-test-src.mp4"), seconds=12)
        cls.video = cls.video.resolve()

    def test_probe_duration(self):
        d = audio_processor.probe_duration(self.video)
        self.assertAlmostEqual(d, 12.0, delta=1.5)

    def test_extract_audio_mono_16k(self):
        out = self.video.parent / "audio.mp3"
        path = audio_processor.extract_audio(self.video, out)
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 2000)
        proc = __import__("subprocess").run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=sample_rate,channels", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120, check=True)
        import json

        streams = json.loads(proc.stdout)["streams"]
        self.assertTrue(any(s.get("sample_rate") == "16000" and
                            s.get("channels") == 1 for s in streams),
                        f"expected mono 16kHz, got {streams}")

    def test_chunking_offsets_and_count(self):
        out_dir = self.video.parent / "chunks"
        chunks = audio_processor.chunk_audio(
            self.video.parent / "audio.mp3", out_dir,
            chunk_seconds=5, overlap_seconds=2)
        self.assertEqual(chunks[0]["start"], 0.0)
        # 12s / 5s chunks -> 3 chunks (0-5, 5-10, 10-12)
        self.assertEqual(len(chunks), 3)
        # second chunk starts 2s early (overlap) to preserve boundaries
        self.assertAlmostEqual(chunks[1]["start"], 3.0, delta=0.01)
        self.assertAlmostEqual(chunks[-1]["end"], 12.0, delta=1.5)
        for c in chunks:
            self.assertTrue(Path(c["path"]).is_file())

    def test_chunking_reuses_cache(self):
        out_dir = self.video.parent / "chunks"
        before = [p.stat().st_mtime for p in out_dir.iterdir()]
        chunks = audio_processor.chunk_audio(
            self.video.parent / "audio.mp3", out_dir,
            chunk_seconds=5, overlap_seconds=2)
        after = [p.stat().st_mtime for p in out_dir.iterdir()]
        self.assertEqual(len(chunks), len(before))
        self.assertEqual(before, after)  # files were not re-encoded

    def test_invalid_chunk_args(self):
        with self.assertRaises(ValueError):
            audio_processor.chunk_audio(
                self.video.parent / "audio.mp3", self.video.parent / "c2",
                chunk_seconds=0)

    def test_persian_language_detection(self):
        self.assertTrue(audio_processor.detect_language_persian(
            "سلام این یک متن فارسی است"))
        self.assertFalse(audio_processor.detect_language_persian(
            "This is plain English text"))
        self.assertFalse(audio_processor.detect_language_persian(""))


if __name__ == "__main__":
    unittest.main()
