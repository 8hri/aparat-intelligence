"""Shared fixtures: synthetic media (ffmpeg), fake transcript data, paths.

Tests run fully offline: no network, no API keys, no Aparat access.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts import config  # noqa: E402


def make_test_video(path: Path, seconds: float = 12.0) -> Path:
    """Synthetic video: color slides changing every few seconds + sine tone.

    Provides real scene changes for frame extraction and a real audio track
    for extraction/chunking - all offline.
    """
    if not shutil.which("ffmpeg"):
        raise unittest.SkipTest("ffmpeg unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    # 4 distinct color segments (real scene changes) + a sine audio tone.
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=640x360:d=3",
        "-f", "lavfi", "-i", "color=c=green:s=640x360:d=3",
        "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=3",
        "-f", "lavfi", "-i", "color=c=white:s=640x360:d=3",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]",
        "-map", "[v]", "-map", "4:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                          check=False)
    if proc.returncode != 0:
        raise unittest.SkipTest(f"ffmpeg test video failed: {proc.stderr[:200]}")
    return path


def make_long_test_video(path: Path, seconds: float = 2311.0) -> Path:
    """Long synthetic video (~38 min) with 4 texturally distinct quarters —
    a regression stand-in for the real 38:31 Aparat video whose 79 sample
    candidates exceeded max_frames=50.

    Quarters use different lavfi patterns (SMPTE bars, horizontal/vertical
    gradients, animated test pattern last) so perceptual-hash dedup treats
    them as distinct scenes; solid colors would all hash identically. The
    animated final quarter stands in for the real video's chapter 3, and
    extraction is run with a scene threshold that detects no cuts — matching
    the real tQH3G video (0 detected scenes) so frame selection alone
    determines tail coverage.
    No audio, a small canvas and a low frame rate keep the build fast; the
    rate is 15fps (not 5) because the extraction endpoint sits at
    duration-0.1s and needs a frame newer than that — at 5fps the last
    frame's PTS is 0.2s before the end and input seeking drops it.
    """
    if not shutil.which("ffmpeg"):
        raise unittest.SkipTest("ffmpeg unavailable")
    path.parent.mkdir(parents=True, exist_ok=True)
    seg = seconds / 4
    srcs = [
        f"smptebars=s=160x90:d={seg:.1f}:r=15",
        f"gradients=s=160x90:d={seg:.1f}:r=15"
        f":c0=0x102030:c1=0xff8800:x0=0:x1=1:y0=0:y1=0",
        f"gradients=s=160x90:d={seg:.1f}:r=15"
        f":c0=0xffffff:c1=0x0044aa:x0=0:x1=0:y0=0:y1=1",
        f"testsrc2=s=160x90:d={seg:.1f}:r=15",
    ]
    inputs: list[str] = []
    for src in srcs:
        inputs += ["-f", "lavfi", "-i", src]
    filt = "".join(f"[{i}:v]" for i in range(4)) + "concat=n=4:v=1:a=0[v]"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", filt, "-map", "[v]",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                          check=False)
    if proc.returncode != 0:
        raise unittest.SkipTest(f"ffmpeg long test video failed: "
                                f"{proc.stderr[:200]}")
    return path


def fake_transcript() -> dict:
    """Deterministic Persian+English transcript for report/QA tests."""
    return {
        "language": "fa",
        "overlap_deduplicated": False,
        "text": "سلام به این ویدیو خوش آمدید. امروز درباره یادگیری ماشین صحبت می‌کنیم. "
                "یادگیری ماشین زیرشاخه‌ای از هوش مصنوعی است. Machine learning uses data to "
                "build models. در پایان به جمع‌بندی می‌رسیم.",
        "segments": [
            {"start": 0.0, "end": 2.5, "text": "سلام به این ویدیو خوش آمدید."},
            {"start": 2.5, "end": 5.5,
             "text": "امروز درباره یادگیری ماشین صحبت می‌کنیم."},
            {"start": 5.5, "end": 9.0,
             "text": "یادگیری ماشین زیرشاخه‌ای از هوش مصنوعی است."},
            {"start": 9.0, "end": 13.0,
             "text": "Machine learning uses data to build models."},
            {"start": 13.0, "end": 16.0, "text": "در پایان به جمع‌بندی می‌رسیم."},
        ],
    }


def fake_metadata() -> dict:
    return {
        "video_id": "TEST1",
        "title": "ویدیوی آزمایشی یادگیری ماشین",
        "description": "توضیحات آزمایشی",
        "uploader": "کانال تست",
        "duration_seconds": 16,
        "duration": "00:16",
        "thumbnail": "https://www.aparat.com/photo.jpg",
        "view_count": 1234,
        "like_count": 56,
        "upload_date": "2026-01-01",
        "webpage_url": "https://www.aparat.com/v/TEST1",
        "extractor": "Aparat",
        "formats": [
            {"format_id": "http-720p", "height": 720, "ext": "mp4",
             "filesize": 5000000, "vcodec": "h264", "acodec": "mp3"},
        ],
    }


def fake_visual() -> list[dict]:
    return [
        {"timestamp": 3.0, "hhmmss": "[00:00:03]", "path": "/tmp/f1.jpg",
         "visible_text": "Machine Learning", "content_type": "slide",
         "description": "اسلایدی با عنوان یادگیری ماشین",
         "visual_only_info": "نمودار دایره‌ای که در صوت توضیح داده نشد"},
    ]


def fake_llm_response(obj: dict) -> object:
    """A provider-like object returning a fixed JSON response."""

    class _FakeProvider:
        name = "fake"

        def chat(self, messages, temperature=0.2, max_tokens=None):
            return json.dumps(obj, ensure_ascii=False)

    return _FakeProvider()


class BaseTest(unittest.TestCase):
    """Auto-skips tests whose cache/config dirs can't be created."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aparat-test-"))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil as _shutil

        _shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, **overrides) -> config.Settings:
        values = dict(
            output_dir=self.tmp / "output",
            max_retries=2,
            http_timeout=5,
        )
        values.update(overrides)
        return config.Settings(**values)
