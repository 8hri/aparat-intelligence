"""Tests: frame extraction (scene detection, dedup, timestamps) - offline."""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import make_long_test_video, make_test_video

from scripts import frame_extractor


class TestFrameExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.video = make_test_video(
            Path("/tmp/aparat-test-src.mp4"), seconds=12).resolve()

    def test_scene_changes_detected(self):
        times = frame_extractor.detect_scene_changes(self.video, threshold=0.3)
        # 4 hard color cuts -> expect several scene changes
        self.assertGreaterEqual(len(times), 2, f"few scene cuts: {times}")
        self.assertEqual(times, sorted(times))

    def test_extract_frames_basic(self):
        out = self.video.parent / "frames_a"
        frames = frame_extractor.extract_frames(
            self.video, out, interval_seconds=4, scene_threshold=0.3,
            max_frames=10, max_width=320)
        self.assertTrue(frames)
        for f in frames:
            self.assertTrue(Path(f["path"]).is_file())
            self.assertRegex(f["hhmmss"], r"^\[\d{2}:\d{2}:\d{2}\]$")
            self.assertEqual(f["source"] in ("sample", "scene"), True)

    def test_frame_dedup(self):
        # 1s interval over a static-looking video should dedup to very few
        out = self.video.parent / "frames_b"
        frames = frame_extractor.extract_frames(
            self.video, out, interval_seconds=1, scene_threshold=0.3,
            max_frames=30, max_width=160)
        self.assertLessEqual(len(frames), 8,
                             f"dedup ineffective: {len(frames)} frames")

    def test_frames_resized(self):
        out = self.video.parent / "frames_c"
        frames = frame_extractor.extract_frames(
            self.video, out, interval_seconds=6, scene_threshold=0.3,
            max_frames=4, max_width=160)
        self.assertTrue(frames)
        from PIL import Image

        for f in frames:
            with Image.open(f["path"]) as img:
                self.assertLessEqual(img.width, 160)

    def test_max_frames_cap(self):
        out = self.video.parent / "frames_d"
        frames = frame_extractor.extract_frames(
            self.video, out, interval_seconds=1, scene_threshold=0.3,
            max_frames=3, max_width=120)
        self.assertLessEqual(len(frames), 3)

    def test_end_frame_always_extracted(self):
        """Coverage must include the video's ending, not stop one interval
        before it."""
        out = self.video.parent / "frames_e"
        frames = frame_extractor.extract_frames(
            self.video, out, interval_seconds=4, scene_threshold=0.3,
            max_frames=10, max_width=320)
        duration = frame_extractor.probe_duration(self.video)
        self.assertTrue(frames)
        last = max(f["timestamp"] for f in frames)
        self.assertGreaterEqual(
            last, duration - 1.0,
            f"last frame at {last}s does not cover the ending ({duration}s)")

    def test_end_frame_survives_cap(self):
        """Even under a strict max_frames cap the endpoint is retained."""
        out = self.video.parent / "frames_f"
        frames = frame_extractor.extract_frames(
            self.video, out, interval_seconds=1, scene_threshold=0.3,
            max_frames=3, max_width=120)
        duration = frame_extractor.probe_duration(self.video)
        self.assertLessEqual(len(frames), 3)
        last = max(f["timestamp"] for f in frames)
        self.assertGreaterEqual(last, duration - 1.0)

    def test_coverage_report_full(self):
        frames = [{"timestamp": float(i * 30)} for i in range(5)]
        cov = frame_extractor.coverage_report(frames, 150, interval_seconds=30)
        self.assertTrue(cov["covered"])
        self.assertEqual(cov["frame_count"], 5)
        self.assertEqual(cov["last"], 120)
        self.assertEqual(cov["tail_seconds"], 30)
        self.assertEqual(cov["gaps"], [])

    def test_coverage_report_detects_missing_ending(self):
        frames = [{"timestamp": float(i * 30)} for i in range(4)]
        cov = frame_extractor.coverage_report(frames, 300, interval_seconds=30)
        # 4 frames cover only 0-90s of a 300s video -> the tail is uncovered.
        self.assertFalse(cov["covered"])
        self.assertEqual(cov["tail_seconds"], 210)
        # no internal gap: the frames are evenly spaced, only the tail is missing
        self.assertEqual(cov["gaps"], [])
        self.assertEqual(cov["max_gap_seconds"], 0.0)

    def test_coverage_report_detects_gap(self):
        frames = [{"timestamp": 0.0}, {"timestamp": 200.0},
                  {"timestamp": 230.0}]
        cov = frame_extractor.coverage_report(frames, 240, interval_seconds=30)
        self.assertFalse(cov["covered"])
        self.assertTrue(cov["gaps"])
        self.assertEqual(cov["gaps"][0]["from"], 0.0)
        self.assertEqual(cov["gaps"][0]["to"], 200.0)

    def test_coverage_report_empty(self):
        cov = frame_extractor.coverage_report([], 120, interval_seconds=30)
        self.assertFalse(cov["covered"])
        self.assertEqual(cov["frame_count"], 0)

    def test_max_frames_cap_covers_full_duration(self):
        """Regression: the max_frames cap must not drop the video tail.

        A 38:31 video sampled at 30s yields 79 candidates; with max_frames=50
        the old stride-and-truncate selection kept only the first 49 samples
        (0..1440s) and the entire chapter 3 region (2032s+) vanished except
        for the exempt endpoint. Selection must spread over the whole video.
        The fixture's scene threshold detects no cuts (like the real video's
        0 detected scenes), so frame selection alone decides tail coverage.
        Uses its own temp directory so stale files from other runs cannot
        mask a regression.
        """
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="aparat-cap-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        video = make_long_test_video(tmp / "long_src.mp4", seconds=2311.0)
        duration = frame_extractor.probe_duration(video)
        frames = frame_extractor.extract_frames(
            video, tmp / "frames_cap", interval_seconds=30,
            scene_threshold=1.0, max_frames=50, max_width=320)
        self.assertLessEqual(len(frames), 50)
        ts = sorted(f["timestamp"] for f in frames)
        # Endpoint preserved.
        self.assertGreaterEqual(ts[-1], duration - 1.0)
        # Chapter 3 (starts at 2032s in the real video) is represented by
        # frames other than just the exempt endpoint.
        self.assertTrue(any(2032 <= t < duration - 1 for t in ts),
                        f"chapter 3 only covered by the endpoint: {ts}")
        # The mid-to-tail region the old bug emptied (1440s..2311s) has
        # frames beyond just the exempt endpoint.
        mid_to_tail = [t for t in ts if 1440 < t < duration - 1]
        self.assertTrue(mid_to_tail,
                        f"no frames between 1440s and the endpoint: {ts}")
        # No chapter-sized hole: static quarters legitimately dedup to a
        # single frame each (~578s quarter span), but the old bug's hole
        # from 1440s to the endpoint was 871s — the bound separates them.
        cov = frame_extractor.coverage_report(frames, duration,
                                               interval_seconds=30)
        self.assertLess(cov["max_gap_seconds"], 800)


if __name__ == "__main__":
    unittest.main()
