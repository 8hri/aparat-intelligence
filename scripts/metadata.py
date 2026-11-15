"""Normalize metadata from yt-dlp or web-scrape into one schema."""

from __future__ import annotations

import re


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _duration_str(seconds) -> str | None:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return None
    if total < 0:
        return None
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _parse_duration(value):
    """Accept int seconds or a [HH:]MM:SS string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", str(value).strip())
    if not m:
        try:
            return int(str(value).strip())
        except ValueError:
            return None
    h, mm, ss = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return h * 3600 + mm * 60 + ss


def normalize(raw: dict) -> dict:
    """yt-dlp info dict / scrape dict -> normalized metadata record."""
    raw = raw or {}
    duration = _parse_duration(_clean(raw.get("duration")))
    upload_date = _clean(raw.get("upload_date"))
    if upload_date and re.fullmatch(r"\d{8}", upload_date):
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"

    formats = []
    for f in raw.get("formats") or []:
        if not isinstance(f, dict):
            continue
        formats.append({
            "format_id": _clean(f.get("format_id")),
            "height": f.get("height"),
            "ext": _clean(f.get("ext")),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "vcodec": _clean(f.get("vcodec")),
            "acodec": _clean(f.get("acodec")),
        })

    uploader = _clean(raw.get("uploader")) or _clean(raw.get("channel"))
    if not uploader and isinstance(raw.get("uploader"), str):
        uploader = _clean(raw["uploader"])

    return {
        "video_id": _clean(raw.get("id") or raw.get("video_id")),
        "title": _clean(raw.get("title")),
        "description": _clean(raw.get("description")),
        "uploader": uploader,
        "duration_seconds": int(duration) if duration is not None else None,
        "duration": _duration_str(duration),
        "thumbnail": _clean(raw.get("thumbnail")),
        "view_count": raw.get("view_count"),
        "like_count": raw.get("like_count"),
        "upload_date": upload_date,
        "webpage_url": _clean(raw.get("webpage_url"))
        or _clean(raw.get("original_url")),
        "extractor": _clean(raw.get("extractor_key") or raw.get("extractor")),
        "formats": formats,
    }


def brief(metadata: dict) -> str:
    """One-line human summary, safe for logs (no metadata interpolation)."""
    meta = metadata or {}
    parts = [f"id={meta.get('video_id') or '?'}"]
    if meta.get("title"):
        parts.append(f"title={meta['title'][:60]}")
    if meta.get("duration"):
        parts.append(f"duration={meta['duration']}")
    return " ".join(parts)
