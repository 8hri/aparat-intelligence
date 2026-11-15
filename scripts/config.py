"""Aparat Intelligence - configuration and path resolution.

All paths are resolved from the environment / XDG dirs so the skill works
from any working directory. Nothing here is user-specific or machine-specific.

No API keys, no provider config, no model selection. The skill is a
local media toolkit; the active Hermes model does all AI reasoning.
"""

from __future__ import annotations

import json
import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "aparat-intelligence"

# Skill directory = parent of the directory containing this file's package.
SKILL_DIR = Path(__file__).resolve().parent.parent

HDD_DIRS = [
    "output",
    "cache",
]

def _xdg(env_var: str, default_sub: str) -> Path:
    base = os.environ.get(env_var)
    if base:
        return Path(base)
    return Path.home() / default_sub


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / APP_NAME


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / APP_NAME


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / APP_NAME


CONFIG_FILE = config_dir() / "config.json"
TOML_FILE = config_dir() / "config.toml"
OUTPUT_DIR = data_dir() / "output"


# Hard safety limits (media-side only; no provider limits remain).
DEFAULT_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024  # 512 MiB
DEFAULT_CHUNK_SECONDS = 600  # 10 min mono 16 kHz mp3 ~ 5-10 MB
DEFAULT_OVERLAP_SECONDS = 2
DEFAULT_FRAME_INTERVAL = 30
DEFAULT_SCENE_THRESHOLD = 0.3
DEFAULT_MAX_FRAMES = 50
DEFAULT_FRAME_MAX_WIDTH = 1280
DEFAULT_HTTP_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 4
DEFAULT_MAX_IMAGE_BYTES = 4 * 1024 * 1024  # per-image payload cap
DEFAULT_TRANSCRIPTION_LANGUAGE = "auto"


@dataclass
class Settings:
    # Media processing
    chunk_seconds: int = DEFAULT_CHUNK_SECONDS
    overlap_seconds: int = DEFAULT_OVERLAP_SECONDS
    frame_interval: int = DEFAULT_FRAME_INTERVAL
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD
    max_frames: int = DEFAULT_MAX_FRAMES
    frame_max_width: int = DEFAULT_FRAME_MAX_WIDTH
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
    http_timeout: int = DEFAULT_HTTP_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    # Transcription language hint (for local STT, not LLM)
    transcription_language: str = DEFAULT_TRANSCRIPTION_LANGUAGE
    # Output
    output_dir: Path = field(default_factory=lambda: OUTPUT_DIR)


def load_settings() -> Settings:
    """Load settings from JSON or TOML config file (JSON wins if both exist)."""
    settings = Settings()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    elif TOML_FILE.exists():
        try:
            data = tomllib.loads(TOML_FILE.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            data = {}

    if not isinstance(data, dict):
        data = {}

    tr = data.get("transcription") or {}
    if isinstance(tr, dict):
        settings.transcription_language = str(
            tr.get("language", settings.transcription_language)
        )
        settings.chunk_seconds = int(tr.get("chunk_seconds", settings.chunk_seconds))

    fr = data.get("frames") or {}
    if isinstance(fr, dict):
        settings.frame_interval = int(fr.get("interval_seconds", settings.frame_interval))
        settings.scene_threshold = float(fr.get("scene_threshold", settings.scene_threshold))
        settings.max_frames = int(fr.get("max_frames", settings.max_frames))

    dl = data.get("download") or {}
    if isinstance(dl, dict):
        settings.max_download_bytes = int(
            dl.get("max_bytes", settings.max_download_bytes)
        )

    out = data.get("output_dir")
    if isinstance(out, str) and out.strip():
        settings.output_dir = Path(out).expanduser()
    return settings


def ensure_secure_config(path: Path) -> None:
    """Create config parent dir and enforce 0600 on the config file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.stat().st_mode & stat.S_IRWXG | stat.S_IRWXO):
        path.chmod(0o600)


def redact(text: str) -> str:
    """Redact anything that looks like an API key from text before logging."""
    import re

    return re.sub(
        r"(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+|(gsk_[A-Za-z0-9]{6})[A-Za-z0-9]+|(AIza[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+",
        lambda m: m.group(1) or m.group(2) or m.group(3) + "...(redacted)",
        text,
    )
