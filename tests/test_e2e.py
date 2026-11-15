"""End-to-end test: full local media pipeline via main.py.

Exercises the real local pipeline - metadata, download (mocked network),
ffmpeg audio extraction + chunking, frame extraction (real ffmpeg on a
synthetic video), report/template generation, and cache reuse. No LLM,
no provider - the active Hermes model does the AI content in conversation.

Usage:  python -m unittest discover -s tests -p "test_e2e.py"
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import make_test_video, fake_metadata  # noqa: E402

from scripts import config, extractor, main as main_mod  # noqa: E402
from unittest import mock  # noqa: E402


def _run_main(argv: list[str]) -> int:
    return main_mod.main(argv)


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="aparat-e2e-"))
        cls.video = make_test_video(cls.tmp / "src.mp4", seconds=12)
        cls.video = cls.video.resolve()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run_analyze(self):
        """Run the full analyze pipeline with mocked metadata + download."""
        meta = dict(fake_metadata())
        meta["video_id"] = "E2ETEST"

        def fake_download(url, dest_dir, max_bytes, audio_only=True, timeout=1800):
            dest = Path(dest_dir) / "E2ETEST.mp4"
            shutil.copy(self.video, dest)
            return dest

        with mock.patch.object(extractor, "fetch_metadata", return_value=meta), \
             mock.patch.object(extractor, "download_best", side_effect=fake_download):
            return _run_main([
                "analyze", "https://www.aparat.com/v/E2ETEST", "--json",
            ])

    def test_full_pipeline(self):
        code = self._run_analyze()
        self.assertEqual(code, 0)
        workspace = config.data_dir() / "output" / "E2ETEST"
        report = workspace / "report.md"
        self.assertTrue(report.is_file(), f"no report at {report}")
        text = report.read_text(encoding="utf-8")
        # metadata written to disk
        meta = json.loads((workspace / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["video_id"], "E2ETEST")
        # media artifacts produced by the local pipeline (in cache dir)
        cache = config.cache_dir() / "videos" / "E2ETEST"
        self.assertTrue((cache / "audio.mp3").is_file())
        frames_dir = cache / "frames"
        self.assertTrue(frames_dir.is_dir())
        self.assertGreater(len(list(frames_dir.iterdir())), 0)
        # transcript JSON and visual template exist
        # (transcript.json is model-produced; analyze only does media pipeline)
        self.assertTrue((workspace / "visual_analysis.json").is_file())
        self.assertTrue((workspace / "report.md").is_file())
        shutil.rmtree(workspace, ignore_errors=True)

    def test_second_run_uses_cache(self):
        from scripts.cache_manager import CacheManager

        code = self._run_analyze()
        self.assertEqual(code, 0)
        cache = CacheManager()
        self.assertTrue(cache.media_ready("E2ETEST", "audio.mp3"))
        self.assertTrue(cache.get_metadata("E2ETEST"))
        # transcript + audio extraction should be cached and reused
        with mock.patch("scripts.audio_processor.extract_audio") as m_extract:
            code = self._run_analyze()
            self.assertFalse(m_extract.called)
        self.assertEqual(code, 0)
        shutil.rmtree(config.data_dir() / "output" / "E2ETEST", ignore_errors=True)

    def test_metadata_command(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = _run_main(["metadata", "https://www.aparat.com/v/E2ETEST"])
        self.assertEqual(code, 0)
        # prints JSON to stdout
        combined = buf.getvalue()
        self.assertIn("E2ETEST", combined)

    def test_cache_commands(self):
        code = _run_main(["cache", "list"])
        self.assertEqual(code, 0)
        code = _run_main(["cache", "size"])
        self.assertEqual(code, 0)
        code = _run_main(["cache", "clear"])
        self.assertEqual(code, 0)

    def test_error_on_bad_url(self):
        code = _run_main(["metadata", "not-a-valid-url"])
        self.assertNotEqual(code, 0)

    def test_chunks_command(self):
        audio = self.video.parent / "test_audio.mp3"
        # Generate a valid MP3 audio file from the video (ffmpeg only
        # accepts real audio streams).
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(self.video), "-vn", str(audio)],
            capture_output=True, text=True, timeout=120, check=True)
        code = _run_main([
            "chunks", str(audio), str(self.video.parent / "chunks"),
            "--chunk-seconds", "5", "--overlap-seconds", "2",
        ])
        self.assertEqual(code, 0)
        chunks_dir = self.video.parent / "chunks"
        self.assertTrue((chunks_dir / "chunk_0000.mp3").is_file())
        shutil.rmtree(chunks_dir, ignore_errors=True)
        audio.unlink()

    def test_frames_command_cli(self):
        """CLI regression: `frames` must not crash with NameError.

        cmd_frames used to reference an undefined `cache` variable, so the
        CLI command failed on every invocation. This test seeds a local
        source.mp4 and invokes the real CLI subcommand end-to-end.
        """
        from scripts.cache_manager import CacheManager
        from scripts.frame_extractor import probe_duration

        cache = CacheManager()
        vid = "E2EFRAMES"
        dest = cache.video_dir(vid)
        source = dest / "source.mp4"
        shutil.copy(self.video, source)
        self.addCleanup(shutil.rmtree, str(dest), True)

        code = _run_main([
            "frames", "https://www.aparat.com/v/E2EFRAMES", str(dest),
            "--no-download", "--interval-seconds", "4",
            "--scene-threshold", "0.3", "--max-frames", "10",
            "--max-width", "320",
        ])
        self.assertEqual(code, 0, "frames CLI command failed")
        # Frames were extracted and the coverage validation ran.
        frames_dir = dest / "frames"
        self.assertTrue(frames_dir.is_dir())
        self.assertGreater(len(list(frames_dir.glob("*.jpg"))), 0)
        self.assertTrue((frames_dir / "coverage.json").is_file())
        coverage = json.loads(
            (frames_dir / "coverage.json").read_text(encoding="utf-8"))
        self.assertIn("covered", coverage)
        # The video's ending is covered by a frame at the tail (the
        # solid-color fixture dedups mid-video, so only the endpoint
        # guarantee is asserted here — the full-duration spread is
        # verified by test_frames.test_max_frames_cap_covers_full_duration).
        duration = probe_duration(source)
        self.assertGreaterEqual(coverage["last"], duration - 1.0)


if __name__ == "__main__":
    unittest.main()
