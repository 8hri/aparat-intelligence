"""Tests: transcription utilities (timestamp format, chunk merge, subtitles).

No provider, no LLM. Pure utilities only.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import config, transcription as transcription_mod  # noqa: E402


class TestTimestampFormat(unittest.TestCase):
    def test_fmt_ts(self):
        self.assertEqual(transcription_mod.fmt_ts(0), "[00:00:00]")
        self.assertEqual(transcription_mod.fmt_ts(65), "[00:01:05]")
        self.assertEqual(transcription_mod.fmt_ts(3725), "[01:02:05]")
        self.assertEqual(transcription_mod.fmt_ts(-5), "[00:00:00]")

    def test_markdown(self):
        merged = {"segments": [
            {"start": 0, "end": 1, "text": "اول"},
            {"start": 65, "end": 70, "text": "دوم"},
        ]}
        md = transcription_mod.transcript_to_markdown(merged, title="تیتر")
        self.assertIn("[00:00:00] اول", md)
        self.assertIn("[00:01:05] دوم", md)
        self.assertIn("# پیاده‌سازی متن — تیتر", md)


class TestMergeChunks(unittest.TestCase):
    def test_offsets_shift_to_video_time(self):
        chunks = [
            {"start": 0.0, "end": 10.0, "text": "بند اول.",
             "segments": [{"start": 0.0, "end": 5.0, "text": "بند اول."}]},
            {"start": 600.0, "end": 610.0, "text": "بند دوم.",
             "segments": [{"start": 0.0, "end": 5.0, "text": "بند دوم."}]},
        ]
        merged = transcription_mod.merge_stt_chunks(chunks)
        self.assertEqual(merged["segments"][0]["start"], 0.0)
        self.assertEqual(merged["segments"][1]["start"], 600.0)
        self.assertIn("بند اول", merged["text"])
        self.assertIn("بند دوم", merged["text"])

    def test_overlap_deduplication(self):
        chunks = [
            {"start": 0.0, "end": 10.0, "text": "",
             "segments": [
                 {"start": 8.0, "end": 10.0, "text": "جمله مشترک."},
                 {"start": 10.0, "end": 10.5, "text": "جمله پایانی."}]},
            {"start": 8.0, "end": 20.0, "text": "",
             "segments": [
                 {"start": 0.0, "end": 1.0, "text": "جمله مشترک."},
                 {"start": 1.0, "end": 2.0, "text": "جمله جدید."}]},
        ]
        merged = transcription_mod.merge_stt_chunks(chunks)
        texts = [s["text"] for s in merged["segments"]]
        self.assertEqual(texts.count("جمله مشترک."), 1,
                         f"duplicated overlap not removed: {texts}")
        self.assertTrue(merged["overlap_deduplicated"])

    def test_no_false_dedup_for_distinct_sentences(self):
        chunks = [
            {"start": 0.0, "end": 10.0, "text": "",
             "segments": [{"start": 0.0, "end": 5.0, "text": "یک."},
                          {"start": 5.0, "end": 10.0, "text": "دو."}]},
            {"start": 8.0, "end": 20.0, "text": "",
             "segments": [{"start": 0.0, "end": 2.0, "text": "سه."},
                          {"start": 2.0, "end": 4.0, "text": "چهار."}]},
        ]
        merged = transcription_mod.merge_stt_chunks(chunks)
        self.assertEqual(len(merged["segments"]), 4)
        self.assertFalse(merged["overlap_deduplicated"])

    def test_synthetic_segments_from_plain_text(self):
        merged = transcription_mod.merge_stt_chunks([
            {"start": 10.0, "end": 20.0,
             "text": "بخش یک. بخش دو. بخش سه.", "segments": []},
        ])
        self.assertEqual(len(merged["segments"]), 3)
        self.assertAlmostEqual(merged["segments"][0]["start"], 10.0, delta=0.1)

    def test_mixed_language(self):
        merged = transcription_mod.merge_stt_chunks([
            {"start": 0.0, "end": 5.0, "text": "",
             "segments": [{"start": 0, "end": 1, "text": "mixed hello and سلام دنیا"}]},
        ])
        # language is heuristic "auto" by default; not guaranteed to be fa
        self.assertIn(merged["language"], ("fa", "en", "auto", None))


if __name__ == "__main__":
    unittest.main()
