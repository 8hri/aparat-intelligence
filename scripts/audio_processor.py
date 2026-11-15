"""Audio extraction and chunking with FFmpeg.

Pipeline: video file -> mono 16 kHz mp3 -> N second chunks (with a small
overlap) so each chunk fits provider upload limits. Every chunk remembers
its start offset so transcription timestamps map back to the video.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("aparat.audio")


def _run_ffmpeg(args: list, timeout: int = 1200) -> None:
    """Run an ffmpeg command built as an argv list - never shell."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH (required for audio extraction)")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {(proc.stderr or '')[:300]}")


def probe_duration(media_path: Path) -> float:
    """Duration in seconds via ffprobe."""
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(media_path)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {(proc.stderr or '')[:200]}")
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        raise RuntimeError(f"could not read duration: {e}") from e


def extract_audio(media_path: Path, out_path: Path) -> Path:
    """Extract audio as mono 16 kHz mp3 (Whisper-compatible, compact)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg([
        "-i", str(media_path),
        "-vn", "-map", "a:0",
        "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-q:a", "5",
        str(out_path),
    ])
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError("audio extraction produced no data (no audio track?)")
    log.info("audio extracted: %s (%d bytes)", out_path.name, out_path.stat().st_size)
    return out_path


def chunk_audio(audio_path: Path, out_dir: Path, chunk_seconds: int = 600,
                overlap_seconds: int = 2) -> list[dict]:
    """Split audio into chunks.

    Returns [{"path", "start", "end"}] with start/end in seconds. The final
    chunk is trimmed to the file duration. Chunks overlap by
    ``overlap_seconds`` so sentence boundaries are not cut; the transcription
    merge step de-duplicates the overlap.
    """
    audio_path = Path(audio_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total = probe_duration(audio_path)
    if total <= 0:
        raise RuntimeError(f"invalid audio duration: {total}")
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be positive")
    if not 0 <= overlap_seconds < chunk_seconds:
        raise ValueError("overlap must be smaller than the chunk length")

    chunks: list[dict] = []
    start = 0.0
    idx = 0
    while start < total:
        end = min(start + chunk_seconds, total)
        chunk_start = max(0.0, start - (overlap_seconds if idx > 0 else 0))
        name = f"chunk_{idx:04d}.mp3"
        target = out_dir / name
        if not target.is_file():
            _run_ffmpeg([
                "-ss", f"{chunk_start:.3f}",
                "-t", f"{end - chunk_start:.3f}",
                "-i", str(audio_path),
                "-c", "copy",
                str(target),
            ])
        chunks.append({"path": str(target), "start": chunk_start, "end": end})
        start = end
        idx += 1
    log.info("audio chunked: %d chunks over %.1fs", len(chunks), total)
    return chunks


def detect_language_persian(text: str) -> bool:
    """Heuristic: majority of letters are Arabic-script -> Persian."""
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return False
    arabic = sum(1 for c in letters if "\u0600" <= c <= "\u06FF")
    return arabic / len(letters) > 0.3
