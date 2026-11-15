"""Frame-handling utilities for Aparat Intelligence.

The LLM-based visual analysis is removed. The active Hermes model performs
visual analysis via its own ``vision_analyze`` tool on the frame paths
produced here. This module keeps:

- Frame extraction (FFmpeg scene detection + periodic sampling, dhash dedup).
- Frame metadata and cache helpers.
- Utilities to build the visual context the model can reason over.

No LLM, no API key, no provider code anywhere in this skill.
"""

from __future__ import annotations

import base64
import logging
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("aparat.vision")


# ------------------------------------------------------------------
# Frame extraction (pure local media processing — no LLM)
# ------------------------------------------------------------------

def _run(cmd: list, timeout: int = 1800) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          check=False)
    if proc.returncode not in (0,):
        raise RuntimeError(f"command failed: {(proc.stderr or '')[:300]}")
    return proc.stderr or ""


def probe_duration(media_path: Path) -> float:
    """Duration in seconds via ffprobe."""
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe not found on PATH")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(media_path)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    try:
        return float(__import__("json").loads(proc.stdout)["format"]["duration"])
    except Exception as e:
        raise RuntimeError(f"could not read duration: {e}") from e


def _downscale_filter(max_width: int) -> str:
    return f"scale='if(gt(iw,{max_width}),{max_width},iw)':-2"


def _dhash(path: Path, size: int = 8) -> int | None:
    """Perceptual difference hash via PIL; None if Pillow is unavailable."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as img:
            img = img.convert("L").resize((size + 1, size), Image.LANCZOS)
            pixels = list(img.getdata())
    except Exception:
        return None
    bits = 0
    for row in range(size):
        for col in range(size):
            left = pixels[row * (size + 1) + col]
            right = pixels[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def extract_frames(video_path: Path, out_dir: Path, interval_seconds: int = 30,
                   scene_threshold: float = 0.3, max_frames: int = 50,
                   max_width: int = 1280) -> list[dict]:
    """Extract representative frames.

    Combines periodic sampling with scene-change detection, removes
    near-duplicates by perceptual hash, and downscales for efficiency.
    Returns [{path, timestamp, hhmmss, source}] — pure file operations.
    """
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")

    duration = probe_duration(video_path)
    times: dict[float, str] = {}

    step = max(int(interval_seconds), 1)
    for t in range(0, int(max(duration, 1)), step):
        times[min(float(t), max(duration - 0.1, 0.0))] = "sample"

    try:
        for t in detect_scene_changes(video_path, scene_threshold):
            times[round(t, 2)] = "scene"
    except RuntimeError as e:
        log.warning("scene detection unavailable: %s", e)

    # Always keep the first frame (title cards / opening slides).
    if not any(t < 1.0 for t in times):
        times[0.0] = "scene"

    if len(times) > max_frames:
        scene_times = {t: s for t, s in times.items() if s == "scene"}
        sample_times = {t: s for t, s in times.items() if s == "sample"}
        keep = set(scene_times)
        need = max_frames - len(keep)
        if need > 0 and sample_times:
            step_n = max(len(sample_times) // need, 1)
            picked = sorted(sample_times)[::step_n][:need]
            keep.update(picked)
        times = {t: times[t] for t in sorted(keep)}

    log.info("extracting %d frames (%d scene cuts)", len(times),
             sum(1 for s in times.values() if s == "scene"))

    frames: list[dict] = []
    prev_hash = None
    for t in sorted(times):
        hh, rem = divmod(int(t), 3600)
        mm, ss = divmod(rem, 60)
        name = f"frame_{int(t):06d}.jpg"
        target = out_dir / name
        if not target.is_file():
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{t:.3f}", "-i", str(video_path),
                "-frames:v", "1",
                "-vf", _downscale_filter(max_width),
                "-q:v", "3",
                str(target),
            ]
            try:
                _run(cmd)
            except RuntimeError as e:
                log.warning("frame at %ss failed: %s", t, e)
                continue
        if not target.is_file():
            continue
        h = _dhash(target)
        if prev_hash is not None and h is not None and _hamming(prev_hash, h) < 6:
            target.unlink(missing_ok=True)
            continue
        prev_hash = h if h is not None else prev_hash
        frames.append({
            "path": str(target),
            "timestamp": round(t, 2),
            "hhmmss": f"[{hh:02d}:{mm:02d}:{ss:02d}]",
            "source": times[t],
        })
    return frames


def detect_scene_changes(video_path: Path, threshold: float = 0.3) -> list[float]:
    """Timestamps of detected scene changes (seconds)."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")
    cmd = [
        "ffmpeg", "-hide_banner", "-i", str(video_path),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                              check=False)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("scene detection timed out") from e
    times = []
    for line in (proc.stderr or "").splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            try:
                times.append(float(m.group(1)))
            except ValueError:
                continue
    return sorted(times)


def encode_frame_b64(path: str | Path, max_bytes: int) -> str | None:
    """Base64 for an image, or None when it exceeds the payload cap."""
    p = Path(path)
    if not p.is_file():
        return None
    if p.stat().st_size > max_bytes:
        log.warning("skipping oversized frame %s (%d bytes)", p.name, p.stat().st_size)
        return None
    return base64.b64encode(p.read_bytes()).decode("ascii")


# ------------------------------------------------------------------
# Visual context for the model (no LLM)
# ------------------------------------------------------------------

def frame_lines(frames: list[dict]) -> str:
    """Plain-text list of frames for the model to consume."""
    lines = []
    for f in frames:
        lines.append(f"{f['hhmmss']}  {f['path']}")
    return "\n".join(lines) or "(no frames)"


def VisualContext(frames: list[dict], visual_findings: list[dict] | None = None) -> str:
    """Build a text block the model can reason over (no LLM).

    Findings are labeled by evidence ``source`` so the model never mistakes
    OCR text for speech transcription or for visual understanding.
    """
    lines = [f"{len(frames)} frames extracted." if frames else "No frames."]
    for f in frames:
        lines.append(f"Frame {f['hhmmss']}: {f['path']}")
        if visual_findings:
            vf = next((v for v in visual_findings if v.get("hhmmss") == f["hhmmss"]), None)
            if vf:
                src = str(vf.get("source", "unspecified"))
                lines.append(f"  source={src}")
                lines.append(f"  visible_text={vf.get('visible_text','')}")
                lines.append(f"  content_type={vf.get('content_type','')}")
                lines.append(f"  description={vf.get('description','')}")
                lines.append(f"  visual_only_info={vf.get('visual_only_info','')}")
    if frames:
        lines.append(
            "note: entries with source=ocr are text extracted from frames by "
            "local OCR; they are NOT speech transcription and NOT visual "
            "understanding. Only source=vision entries are actual visual "
            "observations by an image-capable model.")
    return "\n".join(lines)
