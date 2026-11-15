"""Frame extraction with scene-change detection and duplicate removal."""

from __future__ import annotations

import base64
import logging
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("aparat.frames")


def _run(cmd: list, timeout: int = 1800) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          check=False)
    if proc.returncode not in (0,):
        raise RuntimeError(f"command failed: {(proc.stderr or '')[:300]}")
    return proc.stderr or ""


def probe_duration(media_path: Path) -> float:
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
    near-duplicates by perceptual hash, and downscales for API efficiency.
    The video's ending is always sampled so coverage includes the final
    section (see ``coverage_report``).
    Returns [{"path","timestamp","hhmmss","source":"sample|scene"}].
    """
    video_path = Path(video_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH")

    duration = probe_duration(video_path)
    # Endpoint: a sample just before the end, always kept so the last
    # chapter/credits are covered.
    end_t = round(max(duration - 0.1, 0.0), 2)
    times: dict[float, str] = {}

    step = max(int(interval_seconds), 1)
    for t in range(0, int(max(duration, 1)), step):
        times[min(float(t), end_t)] = "sample"
    if duration > 0:
        times[end_t] = "sample"

    try:
        for t in detect_scene_changes(video_path, scene_threshold):
            times[round(t, 2)] = "scene"
    except RuntimeError as e:
        log.warning("scene detection unavailable: %s", e)

    # Always keep the first frame (title cards / opening slides).
    if not any(t < 1.0 for t in times):
        times[0.0] = "scene"

    if len(times) > max_frames:
        # Keep scene cuts and the endpoint, then evenly spaced samples.
        scene_times = {t: s for t, s in times.items() if s == "scene"}
        keep = set(scene_times)
        if end_t in times:
            keep.add(end_t)
        sample_times = {t for t, s in times.items() if s == "sample"}
        need = max_frames - len(keep)
        if need > 0 and sample_times:
            remaining = sorted(t for t in sample_times if t not in keep)
            need_n = min(need, len(remaining))
            if need_n:
                # Even index spread across the whole remaining span, so the
                # mid and tail sections are sampled — stride-and-truncate
                # (remaining[::n][:need]) silently keeps only the earliest
                # samples and drops the entire video tail.
                idx = {round(i * (len(remaining) - 1) / max(need_n - 1, 1))
                       for i in range(need_n)}
                keep.update(remaining[i] for i in idx)
        # Strict cap that still includes the endpoint (and the first frame
        # when possible) so coverage validation stays meaningful.
        if len(keep) > max_frames and max_frames >= 1:
            ordered = sorted(keep)
            if max_frames == 1:
                keep = {ordered[0]}
            else:
                picks = {round(i * (len(ordered) - 1) / (max_frames - 1))
                         for i in range(max_frames)}
                keep = {ordered[i] for i in picks}
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
        # The endpoint frame is never deduped: even when the last frames are
        # visually static, coverage of the video's ending must be preserved.
        is_endpoint = abs(t - end_t) < 0.5
        h = _dhash(target)
        if not is_endpoint and prev_hash is not None and h is not None \
                and _hamming(prev_hash, h) < 6:
            target.unlink(missing_ok=True)
            continue  # near-duplicate of the previous kept frame
        prev_hash = h if h is not None else prev_hash
        frames.append({
            "path": str(target),
            "timestamp": round(t, 2),
            "hhmmss": f"[{hh:02d}:{mm:02d}:{ss:02d}]",
            "source": times[t],
        })
    return frames


def coverage_report(frames: list[dict], duration: float,
                    interval_seconds: int = 30) -> dict:
    """Validate frame coverage against the full video duration.

    Compares extracted frame timestamps with the video duration and reports
    the covered span, any temporal gaps wider than twice the sampling
    interval, and the uncovered tail after the last frame. ``covered`` is
    True only when the whole timeline is within tolerance.
    """
    step = max(float(interval_seconds), 1.0)
    base = {
        "covered": False,
        "video_duration": round(float(duration), 2),
        "frame_count": len(frames),
        "first": None,
        "last": None,
        "tail_seconds": None,
        "gaps": [],
        "max_gap_seconds": 0.0,
        "note": "no frames extracted" if not frames else "",
    }
    if not frames or duration <= 0:
        return base
    ts = sorted(float(f["timestamp"]) for f in frames)
    gaps = [
        {"from": round(prev, 2), "to": round(cur, 2),
         "seconds": round(cur - prev, 2)}
        for prev, cur in zip(ts, ts[1:])
        if cur - prev > step * 2
    ]
    tail = duration - ts[-1]
    covered = tail <= step and not gaps
    return {
        "covered": covered,
        "video_duration": round(float(duration), 2),
        "frame_count": len(frames),
        "first": ts[0],
        "last": ts[-1],
        "tail_seconds": round(tail, 2),
        "gaps": gaps,
        "max_gap_seconds": round(max((g["seconds"] for g in gaps), default=0.0), 2),
        "note": ("OK: full duration covered" if covered else
                 "missing sections: see gaps/tail_seconds"),
    }


def encode_frame_b64(path: str | Path, max_bytes: int) -> str | None:
    """Base64 for an image, or None when it exceeds the payload cap."""
    p = Path(path)
    if not p.is_file():
        return None
    if p.stat().st_size > max_bytes:
        log.warning("skipping oversized frame %s (%d bytes)", p.name, p.stat().st_size)
        return None
    return base64.b64encode(p.read_bytes()).decode("ascii")
