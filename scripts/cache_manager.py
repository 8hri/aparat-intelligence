"""Local cache: metadata, media, transcripts, frames, AI analysis.

Layout:  <cache>/videos/<video_id>/<kind>/...
         <cache>/analysis/<sha256 of payload>.json

Media (audio/frames) is cached under videos/<id>/ and never re-downloaded
or re-extracted when the artifact already exists. AI analysis results are
cached by the sha256 of the exact request payload (model + messages), so
identical questions hit the cache and edited questions re-run.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from . import config


class CacheManager:
    def __init__(self, base: Path | None = None):
        self.base = Path(base) if base else config.cache_dir()
        self.videos_dir = self.base / "videos"
        self.analysis_dir = self.base / "analysis"
        self.manifest_path = self.base / "manifest.json"
        self.base.mkdir(parents=True, exist_ok=True)
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        self.analysis_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ keys
    @staticmethod
    def content_hash(payload) -> str:
        """Stable sha256 of a JSON-serialisable payload."""
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def video_dir(self, video_id: str) -> Path:
        safe_id = "".join(c for c in video_id if c.isalnum() or c in "-_")
        if not safe_id:
            raise ValueError(f"unsafe video id: {video_id!r}")
        path = self.videos_dir / safe_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def media_path(self, video_id: str, name: str) -> Path:
        """Canonical path for a media artifact (audio file, frame, chunk)."""
        return self.video_dir(video_id) / name

    # ----------------------------------------------------------------- media
    def has_media(self, video_id: str, name: str) -> bool:
        return self.media_path(video_id, name).is_file()

    def media_ready(self, video_id: str, name: str, min_bytes: int = 1024) -> bool:
        path = self.media_path(video_id, name)
        return path.is_file() and path.stat().st_size >= min_bytes

    # -------------------------------------------------------------- analysis
    def get_analysis(self, payload) -> object | None:
        """Return cached analysis result for this exact payload, if present."""
        digest = self.content_hash(payload)
        path = self.analysis_dir / f"{digest}.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def put_analysis(self, payload, result) -> None:
        digest = self.content_hash(payload)
        path = self.analysis_dir / f"{digest}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)

    # ------------------------------------------------------------- metadata
    def get_metadata(self, video_id: str) -> dict | None:
        path = self.video_dir(video_id) / "metadata.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def put_metadata(self, video_id: str, metadata: dict) -> None:
        self.put_artifact(video_id, "metadata.json", metadata)

    def put_artifact(self, video_id: str, name: str, obj) -> Path:
        path = self.video_dir(video_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        if isinstance(obj, (dict, list)):
            tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        else:
            tmp.write_text(str(obj), encoding="utf-8")
        tmp.replace(path)
        return path

    # ------------------------------------------------------------------ list
    def list_videos(self) -> list[dict]:
        out = []
        for entry in sorted(self.videos_dir.iterdir()):
            if not entry.is_dir():
                continue
            meta = self.get_metadata(entry.name)
            out.append({
                "video_id": entry.name,
                "title": (meta or {}).get("title"),
                "cached_at": entry.stat().st_mtime,
                "artifacts": sorted(p.name for p in entry.iterdir() if p.is_file()),
            })
        return out

    # ----------------------------------------------------------------- clear
    def clear(self, video_id: str | None = None, older_than_days: float | None = None) -> int:
        """Remove cached data. Returns number of entries removed."""
        removed = 0
        if video_id:
            path = self.videos_dir / video_id
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
            return removed
        cutoff = time.time() - (older_than_days or 0) * 86400 if older_than_days else None
        for entry in list(self.videos_dir.iterdir()):
            if entry.is_dir() and (cutoff is None or entry.stat().st_mtime < cutoff):
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        for entry in list(self.analysis_dir.iterdir()):
            if cutoff is None or entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
                removed += 1
        return removed

    def size(self) -> int:
        total = 0
        for root, _dirs, files in os.walk(self.base):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except OSError:
                    pass
        return total
