"""Aparat URL validation and video acquisition.

Strategy:
  1. yt-dlp (system binary) for metadata + download - its Aparat extractor
     handles /v/<id> and embed URLs.
  2. Fallback: fetch the video page with HTTP and parse the embedded
     ``options = {...};`` JSON + JSON-LD, mirroring yt-dlp's extractor,
     for metadata only.

Safety: all subprocess calls use argument lists (never shell=True), all
downloaded data lands in a cache dir, and metadata from the network is
treated as untrusted text (never executed, never interpolated into shell).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config

log = logging.getLogger("aparat.extractor")

VALID_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.)?aparat\.com/"
    r"(?:v/(?P<id1>[A-Za-z0-9]+)"
    r"|video/video/embed/videohash/(?P<id2>[A-Za-z0-9]+))/?$"
)


def parse_url(url: str) -> str | None:
    """Return the video id if url is a valid Aparat video URL, else None."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    m = VALID_URL_RE.match(url)
    if not m:
        return None
    return m.group("id1") or m.group("id2")


def normalize_url(video_id: str) -> str:
    return f"https://www.aparat.com/v/{video_id}"


def validate(url: str) -> tuple[str | None, str | None]:
    """Return (video_id, error)."""
    vid = parse_url(url)
    if vid:
        return vid, None
    return None, (
        "Not a valid Aparat video URL. Expected forms like "
        "https://www.aparat.com/v/XXXXX or the embed URL "
        "https://www.aparat.com/video/video/embed/videohash/XXXXX"
    )


def _http_get(url: str, timeout: int) -> str:
    """Plain HTTP GET with a browser UA. Uses requests when available."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 "
            "Firefox/128.0"
        ),
        "Accept-Language": "fa,en;q=0.8",
    }
    try:
        import requests

        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.text
    except ImportError:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")


class VideoUnavailableError(Exception):
    pass


def fetch_metadata_ytdlp(url: str, timeout: int = 90) -> dict | None:
    """yt-dlp metadata via --dump-json (no download). None if it fails."""
    if not shutil.which("yt-dlp"):
        log.warning("yt-dlp not found on PATH")
        return None
    cmd = [
        "yt-dlp", "--no-playlist", "--skip-download", "--dump-json",
        "--socket-timeout", str(timeout // 2 or 30),
        "--no-warnings", url,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        log.warning("yt-dlp metadata timed out")
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        err = (proc.stderr or "")[:300]
        if "HTTP Error 404" in err or "Private" in err or "removed" in err.lower():
            raise VideoUnavailableError(f"video unavailable: {err}")
        log.debug("yt-dlp metadata failed: %s", config.redact(err))
        return None
    try:
        return json.loads(proc.stdout.splitlines()[0])
    except json.JSONDecodeError:
        return None


def fetch_metadata_scrape(url_or_id: str, timeout: int = 60) -> dict | None:
    """Fallback metadata: parse the embedded options JSON + JSON-LD."""
    video_id = parse_url(url_or_id) or str(url_or_id)
    try:
        html = _http_get(normalize_url(video_id), timeout)
    except Exception as e:
        log.debug("scrape failed for %s: %s", video_id, e)
        return None
    if not html:
        return None
    info: dict = {"id": video_id, "extractor": "web-scrape"}

    m = re.search(r"options\s*=\s*(\{.+?\})\s*;", html, re.S)
    if m:
        try:
            options = json.loads(m.group(1))
        except json.JSONDecodeError:
            options = {}
        if isinstance(options, dict):
            info["duration"] = options.get("duration")
            info["thumbnail"] = options.get("poster")

    m = re.search(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html, re.S,
    )
    if m:
        try:
            ld = json.loads(m.group(1))
        except json.JSONDecodeError:
            ld = {}
        if isinstance(ld, dict) and ld.get("@type") == "VideoObject":
            info["title"] = ld.get("name")
            info["description"] = ld.get("description")
            info["uploader"] = (ld.get("author") or {}).get("name") \
                if isinstance(ld.get("author"), dict) else ld.get("author")
            info["upload_date"] = ld.get("uploadDate")
            if ld.get("thumbnailUrl"):
                thumbs = ld["thumbnailUrl"]
                info["thumbnail"] = thumbs[0] if isinstance(thumbs, list) else thumbs

    if not info.get("title"):
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
        if m:
            info["title"] = m.group(1)
    return info if info.get("title") else None


def fetch_metadata(url: str, timeout: int = 90) -> dict:
    """Full metadata fetch with fallback. Raises VideoUnavailableError."""
    video_id = parse_url(url)
    if not video_id:
        raise ValueError(f"invalid Aparat URL: {url}")
    data = fetch_metadata_ytdlp(url, timeout)
    if data is None:
        data = fetch_metadata_scrape(video_id, timeout)
    if data is None:
        raise VideoUnavailableError(
            f"could not retrieve metadata for {video_id} (video may be "
            "private, removed, or unreachable from this network)"
        )
    return data


def download_best(url: str, dest_dir: Path, max_bytes: int,
                  audio_only: bool = True, timeout: int = 1800) -> Path:
    """Download audio (default) or full video with yt-dlp into dest_dir.

    Returns the path of the downloaded file. Raises on failure or when the
    file exceeds max_bytes (yt-dlp aborts via --max-filesize).
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp is required for download but not on PATH")
    dest_dir.mkdir(parents=True, exist_ok=True)
    fmt = "bestaudio/best" if audio_only else "best[height<=720]/best"
    cmd = [
        "yt-dlp", "--no-playlist", "-f", fmt,
        "--max-filesize", str(max_bytes),
        "-o", str(dest_dir / "%(id)s.%(ext)s"),
        "--no-progress", "--socket-timeout", "60",
        url,
    ]
    log.info("downloading %s (audio_only=%s)", config.redact(url), audio_only)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"download failed: {config.redact((proc.stderr or '')[:300])}"
        )
    # locate the produced file (largest media file in dest_dir)
    candidates = [p for p in dest_dir.iterdir() if p.is_file() and p.suffix in (
        ".mp3", ".m4a", ".webm", ".mp4", ".opus", ".aac", ".wav")]
    if not candidates:
        raise RuntimeError("download produced no media file")
    best = max(candidates, key=lambda p: p.stat().st_size)
    if best.stat().st_size > max_bytes:
        best.unlink(missing_ok=True)
        raise RuntimeError(
            f"downloaded file exceeds limit ({best.stat().st_size} > {max_bytes})"
        )
    return best


def temp_workspace(prefix: str = "aparat-") -> Path:
    """Fresh temp dir inside the system temp area; caller removes it."""
    return Path(tempfile.mkdtemp(prefix=prefix))
