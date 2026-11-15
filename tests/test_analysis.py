"""Tests: text analyzer JSON parsing + context + new utilities (no LLM).

The active Hermes model does all AI inference; these tests verify the
helper functions the model uses to build context and the pure utilities
that survived the refactor.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import fake_metadata, fake_transcript, fake_visual  # noqa: E402

from scripts import text_analyzer, transcription as transcription_mod  # noqa: E402
from scripts.vision_analyzer import frame_lines, VisualContext  # noqa: E402


class TestLooseJSON(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(text_analyzer.parse_json_loose('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(
            text_analyzer.parse_json_loose('```json\n{"a": 1}\n```'), {"a": 1})

    def test_embedded(self):
        self.assertEqual(
            text_analyzer.parse_json_loose('sure: {"a": 1} done'), {"a": 1})

    def test_invalid_returns_none(self):
        self.assertIsNone(text_analyzer.parse_json_loose("no json here"))
        self.assertIsNone(text_analyzer.parse_json_loose(""))


class TestContextBuilder(unittest.TestCase):
    def test_context_includes_timestamps(self):
        ctx = text_analyzer.build_context(fake_transcript(), fake_metadata(), fake_visual())
        self.assertIn("[00:00:00]", ctx)
        self.assertIn("Machine Learning", ctx)
        self.assertIn("یافته‌های تصویری", ctx)
        self.assertIn("متن روی تصویر", ctx)

    def test_context_labels_ocr_findings(self):
        """OCR findings must be labeled as OCR — never as transcription."""
        visual = [
            {"hhmmss": "[00:00:03]", "content_type": "slide", "source": "ocr",
             "visible_text": "Machine Learning", "description": "اسلاید"},
        ]
        ctx = text_analyzer.build_context(
            fake_transcript(), fake_metadata(), visual)
        self.assertIn("OCR", ctx)
        self.assertIn("این متن گفتار نیست و درک تصویری نیست", ctx)
        # The transcript section keeps its own distinct label.
        self.assertIn("متن پیاده‌سازی شده با مهر زمانی (منبع: transcript", ctx)

    def test_context_separates_vision_from_ocr(self):
        """Vision observations and OCR text land in separate labeled groups."""
        visual = [
            {"hhmmss": "[00:00:03]", "content_type": "slide", "source": "ocr",
             "visible_text": "OCR text"},
            {"hhmmss": "[00:00:09]", "content_type": "chart", "source": "vision",
             "description": "نمودار", "visual_only_info": "رنگ"},
        ]
        ctx = text_analyzer.build_context(
            fake_transcript(), fake_metadata(), visual)
        self.assertIn("متن استخراج‌شده از روی فریم‌ها با OCR", ctx)
        self.assertIn("مشاهدات تصویری مدل", ctx)

    def test_context_without_transcript_omits_transcript_section(self):
        """No segments → no transcript section (no empty fake section)."""
        ctx = text_analyzer.build_context(
            {}, fake_metadata(), fake_visual())
        self.assertNotIn("متن پیاده‌سازی شده", ctx)
        self.assertIn("فراداده/توضیحات رسمی", ctx)

    def test_context_truncates_long_transcript(self):
        long_transcript = {
            "segments": [
                {"start": i, "end": i + 1, "text": f"متن شماره {i} که تکرار می‌شود"}
                for i in range(400)
            ]
        }
        ctx = text_analyzer.build_context(long_transcript, fake_metadata())
        self.assertLess(len(ctx), 40000)

    def test_build_context_omits_visual(self):
        ctx = text_analyzer.build_context(fake_transcript(), fake_metadata(), None)
        self.assertNotIn("یافته‌های تصویری", ctx)


class TestTranscriptionUtils(unittest.TestCase):
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

    def test_merge_stt_chunks_offsets_shift(self):
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

    def test_merge_stt_chunks_overlap_dedup(self):
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

    def test_merge_stt_chunks_no_false_dedup(self):
        chunks = [
            {"start": 0.0, "end": 10.0, "text": "",
             "segments": [{"start": 0.0, "end": 5.0, "text": "یک."},
                          {"start": 5.0, "end": 10.0, "text": "دو."}]},
            {"start": 8.0, "end": 20.0, "text": "",
             "segments": [{"start": 0.0, "end": 2.0, "text": " سه."},
                          {"start": 2.0, "end": 4.0, "text": "چهار."}]},
        ]
        merged = transcription_mod.merge_stt_chunks(chunks)
        self.assertEqual(len(merged["segments"]), 4)
        self.assertFalse(merged["overlap_deduplicated"])

    def test_merge_stt_chunks_synthetic_from_text(self):
        merged = transcription_mod.merge_stt_chunks([
            {"start": 10.0, "end": 20.0,
             "text": "بخش یک. بخش دو. بخش سه.", "segments": []},
        ])
        self.assertEqual(len(merged["segments"]), 3)
        self.assertAlmostEqual(merged["segments"][0]["start"], 10.0, delta=0.1)


class TestVisionUtilities(unittest.TestCase):
    def test_frame_lines(self):
        frames = [
            {"path": "/tmp/f1.jpg", "timestamp": 0.0, "hhmmss": "[00:00:00]", "source": "sample"},
        ]
        out = frame_lines(frames)
        self.assertIn("[00:00:00]", out)
        self.assertIn("/tmp/f1.jpg", out)

    def test_frame_lines_empty(self):
        self.assertEqual(frame_lines([]), "(no frames)")

    def test_visual_context(self):
        frames = [{"path": "/tmp/f1.jpg", "timestamp": 0.0,
                   "hhmmss": "[00:00:00]", "source": "sample"}]
        findings = [{"hhmmss": "[00:00:00]", "visible_text": "OCR",
                     "content_type": "slide", "description": "د",
                     "visual_only_info": "i"}]
        out = VisualContext(frames, findings)
        self.assertIn("1 frames extracted", out)
        self.assertIn("OCR", out)
        self.assertIn("د", out)

    def test_visual_context_no_findings(self):
        frames = [{"path": "/tmp/f1.jpg", "timestamp": 0.0,
                   "hhmmss": "[00:00:00]", "source": "sample"}]
        out = VisualContext(frames, None)
        self.assertNotIn("visible_text=", out)


if __name__ == "__main__":
    unittest.main()
