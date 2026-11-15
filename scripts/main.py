"""Aparat Intelligence CLI - the local media pipeline orchestrator.

Usage (run from any directory; SKILL.md instructs the model to call these
through the `terminal` tool):
    python -m scripts.main analyze  <aparat-url>
    python -m scripts.main metadata <aparat-url>
    python -m scripts.main download <aparat-url> <dir>
    python -m scripts.main audio     <aparat-url> <dir>
    python -m scripts.main chunks   <audio> <chunk_dir>
    python -m scripts.main frames    <aparat-url> <dir>
    python -m scripts.main cache     list|clear|size
    python -m scripts.main report    <video_id>

All commands handle local media only: downloading Aparat video metadata and
files, extracting audio and frames with FFmpeg, caching artifacts, and writing
Markdown/JSON reports. **No LLM, no API key, no provider selection is performed
here.** Every AI task is performed by the active Hermes model in conversation.

Completion criterion for `analyze`: the command exits 0 and the workspace
contains metadata.json, audio.mp3, frames/, and report files.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

# Make the skill dir importable regardless of cwd.
_HERE = Path(__file__).resolve().parent
_SKILL_DIR = _HERE.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from scripts import (  # noqa: E402
    audio_processor,
    cache_manager,
    config as config_mod,
    extractor,
    frame_extractor,
    main as main_mod,  # noqa: F401
    metadata as metadata_mod,
    report_generator,
    transcription as transcription_mod,
    vision_analyzer,
)
from scripts.audio_processor import chunk_audio as _chunk_audio
from scripts.cache_manager import CacheManager  # noqa: E402
from scripts.frame_extractor import coverage_report as _coverage_report
from scripts.frame_extractor import extract_frames as _extract_frames
from scripts.frame_extractor import probe_duration as _probe_duration
from scripts.metadata import normalize as _normalize

log = logging.getLogger("aparat.main")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )


def _resolve_video_id(url_or_id: str) -> str:
    vid = extractor.parse_url(url_or_id)
    if vid:
        return vid
    if url_or_id and all(c.isalnum() or c in "-_" for c in url_or_id) \
            and 1 <= len(url_or_id) <= 40:
        return url_or_id
    raise SystemExit(f"not an Aparat URL or video id: {url_or_id!r}")


def _cache() -> CacheManager:
    return CacheManager()


def _video_scope(video_id: str) -> Path:
    """Canonical on-disk scope for a video's artifacts."""
    safe = "".join(c for c in video_id if c.isalnum() or c in "-_")
    return config_mod.cache_dir() / "videos" / safe


def _print_json(obj, indent: int = 2) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=indent))


# ----------------------------------------------------------------------
# Media pipeline steps
# ----------------------------------------------------------------------

def cmd_metadata(args) -> int:
    """Extract video metadata only (no download)."""
    cache = _cache()
    vid = _resolve_video_id(args.url)

    cached = cache.get_metadata(vid)
    if cached:
        log.info("metadata cache hit for %s", vid)
        _print_json(cached)
        return 0

    raw = extractor.fetch_metadata(args.url)
    normalized = _normalize(raw)
    if not normalized.get("video_id"):
        log.warning("no metadata parsed from %s", args.url)
        _print_json({"video_id": None, "url": args.url})
        return 0

    if args.output_dir:
        (Path(args.output_dir) / "metadata.json").write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        cache.put_metadata(normalized["video_id"], normalized)
    _print_json(normalized)
    return 0


def cmd_download(args) -> int:
    """Download the video (or audio-only) file from Aparat."""
    dest_dir = Path(args.dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    downloaded = extractor.download_best(
        args.url, dest_dir, config_mod.Settings().max_download_bytes, audio_only=False,
    )
    if downloaded is None:
        print("ERROR: download failed (yt-dlp may be unavailable or Aparat is "
              "unreachable from this host)")
        return 3

    print(f"downloaded: {downloaded}")
    print(f"size: {downloaded.stat().st_size} bytes")
    return 0


def cmd_download(url: str, dest_dir: Path) -> int:
    """Download the video (or audio-only) file from Aparat."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    downloaded = extractor.download_best(
        url, dest_dir, config_mod.Settings().max_download_bytes, audio_only=False,
    )
    if downloaded is None:
        print("ERROR: download failed (yt-dlp may be unavailable or Aparat is "
              "unreachable from this host)")
        return 3
    print(f"downloaded: {downloaded}")
    print(f"size: {downloaded.stat().st_size} bytes")
    return 0


def cmd_audio(args) -> int:
    """Download the video and extract audio (mono 16 kHz mp3)."""
    settings = config_mod.Settings()
    vid = _resolve_video_id(args.url)
    dest_dir = cache.video_dir(vid)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 1) Download the video.
    downloaded = extractor.download_best(
        args.url, dest_dir, settings.max_download_bytes, audio_only=False,
    )
    if downloaded is None:
        print("ERROR: download failed (yt-dlp may be unavailable or Aparat is "
              "unreachable from this host)")
        return 3
    if downloaded.suffix not in (".mp4", ".mkv", ".webm"):
        # yt-dlp may have fetched an audio file directly when audio_only=True;
        # we requested a full video, so if a plain audio file appeared, fall
        # back to treating it as the source.
        if downloaded.suffix in (".mp3", ".m4a", ".opus", ".aac", ".wav"):
            audio_path = dest_dir / "audio.mp3"
            audio_path.write_bytes(downloaded.read_bytes())
            downloaded.unlink(missing_ok=True)
            print(f"downloaded audio-only: {audio_path}")
            return 0
        audio_path = cache_audio_from_video(downloaded, dest_dir)
    else:
        audio_path = cache_audio_from_video(downloaded, dest_dir)

    print(f"audio extracted: {audio_path}")
    print(f"audio size: {audio_path.stat().st_size} bytes")
    return 0


def cache_audio_from_video(video_path: Path, out_dir: Path) -> Path:
    """Extract audio from a downloaded video into out_dir/audio.mp3."""
    audio_path = out_dir / "audio.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_processor.extract_audio(video_path, audio_path)
    return audio_path


def cmd_chunks(args) -> int:
    """Chunk an audio file into cached pieces."""
    audio_path = Path(args.audio)
    chunk_dir = Path(args.chunk_dir)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    chunks = _chunk_audio(audio_path, chunk_dir,
                          chunk_seconds=args.chunk_seconds,
                          overlap_seconds=args.overlap_seconds)
    print(f"chunks: {len(chunks)}")
    for c in chunks:
        print(f"  {c['start']:>8.1f}-{c['end']:>8.1f}  {Path(c['path']).name}")
    return 0


def cmd_frames(args) -> int:
    """Extract key frames from the video (download if needed)."""
    settings = config_mod.Settings()
    cache = _cache()
    vid = _resolve_video_id(args.url)
    dest_dir = cache.video_dir(vid)
    dest_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = dest_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    video_path = dest_dir / "source.mp4"
    if not video_path.is_file() and not args.no_download:
        downloaded = extractor.download_best(
            args.url, dest_dir, settings.max_download_bytes, audio_only=False,
        )
        if downloaded is None:
            print("ERROR: download failed (yt-dlp may be unavailable or Aparat "
                  "is unreachable from this host)")
            return 3
        video_path = downloaded
    elif not video_path.is_file():
        print("ERROR: no video file found (run audio/do download first)")
        return 3

    frames = _extract_frames(
        video_path, frames_dir,
        interval_seconds=args.interval_seconds,
        scene_threshold=args.scene_threshold,
        max_frames=args.max_frames,
        max_width=args.max_width,
    )
    print(f"frames: {len(frames)}")
    for f in frames[:20]:
        print(f"  {f['hhmmss']} [{f['source']}] {f['path']}")
    _emit_coverage(frames, video_path, frames_dir, args.interval_seconds)
    return 0


def _emit_coverage(frames: list[dict], video_path: Path, dest_dir: Path,
                   interval_seconds: int) -> None:
    """Validate frame coverage against the video duration and report it.

    Writes ``coverage.json`` next to the frames so the model (and the user)
    can verify the whole timeline — including the ending — was sampled.
    """
    try:
        duration = _probe_duration(video_path)
    except RuntimeError as e:
        print(f"coverage: could not read duration ({e})")
        return
    cov = _coverage_report(frames, duration, interval_seconds=interval_seconds)
    try:
        (Path(dest_dir) / "coverage.json").write_text(
            json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("could not write coverage.json: %s", e)
    status = "covered" if cov["covered"] else "INCOMPLETE"
    print(f"coverage: {status}  frames={cov['frame_count']} "
          f"first={cov['first']} last={cov['last']} "
          f"tail={cov['tail_seconds']}s gaps={len(cov['gaps'])}")
    if not cov["covered"]:
        print("  WARNING: parts of the video are not covered by any frame; "
              "see coverage.json")
    elif cov["gaps"]:
        for g in cov["gaps"]:
            print(f"  note: gap {g['from']}s -> {g['to']}s ({g['seconds']}s; "
                  "likely deduped static segments)")


def _write_report(workspace: Path, metadata: dict, transcript: dict | None,
                  visual: list[dict], text_only: bool) -> dict:
    """Write the report artifact set. No AI content here — the model writes
    summary.md, study_notes.md, quiz.md, flashcards.md, and the full report."""
    _write_json(workspace / "metadata.json", metadata)
    if transcript is not None:
        _write_json(workspace / "transcript.json", transcript)
        _write_text(workspace / "transcript.md",
                    transcription_mod.transcript_to_markdown(transcript, metadata.get("title")))
    _write_json(workspace / "visual_analysis.json", visual)

    # The model supplies the AI content. We write the shell artifacts and the
    # report template; the model fills in the intelligence.
    report = render_template(metadata, transcript, visual)
    _write_text(workspace / "report.md", report)
    if text_only:
        _write_text(workspace / "report.txt", report)
    return {"workspace": str(workspace),
            "files": sorted(p.name for p in workspace.iterdir() if p.is_file())}


def _visual_by_source(visual: list[dict]) -> dict[str, list[dict]]:
    """Split visual findings by their evidence ``source``.

    Known sources: ``ocr`` (text extracted from frames by local OCR),
    ``vision`` (actual visual observations by an image-capable model).
    Entries without an explicit source are treated as ``unspecified`` and
    must never be reported as visual understanding.
    """
    groups: dict[str, list[dict]] = {"ocr": [], "vision": [], "unspecified": []}
    for v in visual or []:
        groups.setdefault(str(v.get("source", "unspecified")), []).append(v)
    unspecified = groups.pop("unspecified", [])
    groups["unspecified"] = unspecified
    return groups


def render_template(metadata: dict, transcript: dict | None,
                    visual: list[dict]) -> str:
    """Render the report template with the structure; the model replaces the
    AI sections with its own reasoning.

    Findings are grouped and labeled by evidence source (transcript, OCR,
    metadata/description, visual observations) so no OCR or metadata content
    is ever presented as speech transcription or visual understanding.
    """
    t = transcript or {}
    segments = t.get("segments") or []
    chapters = t.get("chapters") or []
    groups = _visual_by_source(visual)
    out = ["# گزارش تحلیل ویدیو — Aparat Intelligence", ""]
    out.append(f"- ** شناسه:** {metadata.get('video_id')}")
    out.append(f"- **عنوان:** {metadata.get('title') or 'نامشخص'}")
    if metadata.get("uploader"):
        out.append(f"- **منتشرکننده:** {metadata['uploader']}")
    if metadata.get("duration"):
        out.append(f"- **مدت زمان:** {metadata['duration']}")
    if metadata.get("upload_date"):
        out.append(f"- **تاریخ انتشار:** {metadata['upload_date']}")
    if metadata.get("webpage_url"):
        out.append(f"- **آدرس:** {metadata['webpage_url']}")
    out.append("")
    # Evidence provenance summary.
    out.append("## منابع شواهد (Provenance)")
    out.append("یافته‌های این گزارش بر اساس منابع زیر تولید شده‌اند و هر بخش "
               "با منبع خود برچسب‌گذاری شده است:")
    if segments:
        out.append("- **پیاده‌سازی گفتار (transcript):** موجود — متن گفتار با "
                   "مهر زمانی")
    else:
        out.append("- **پیاده‌سازی گفتار (transcript):** در دسترس نیست")
    if groups["ocr"]:
        out.append(f"- **OCR (متن استخراج‌شده از روی فریم‌ها):** "
                   f"{len(groups['ocr'])} یافته — این متن گفتار نیست و درک "
                   "تصویری نیست")
    if groups["vision"]:
        out.append(f"- **مشاهدات تصویری مدل:** {len(groups['vision'])} یافته")
    if groups["unspecified"]:
        out.append(f"- **یافته‌های بدون منبع مشخص:** "
                   f"{len(groups['unspecified'])} مورد — نیاز به برچسب‌گذاری")
    out.append(f"- **فراداده/توضیحات ویدیو:** عنوان، منتشرکننده، مدت و توضیحات "
               "رسمی (این منبع، گفتار یا تصویر نیست)")
    out.append("")
    out.append("## خلاصه")
    out.append("_تولیدشده توسط مدل در مکالمه — مقدمه‌ای نهایی را در report.md بگذارید._")
    out.append("")
    if chapters:
        out.append("## فصل‌بندی")
        for ch in chapters:
            out.append(f"- {ch.get('start','')} – {ch.get('end','')}: "
                       f"**{ch.get('title','')}** — {ch.get('summary','')}")
        out.append("")
    if segments:
        out.append("## متن پیاده‌سازی شده (منبع: transcript)")
        for s in segments:
            out.append(f"{transcription_mod.fmt_ts(s['start'])} {s['text']}")
        out.append("")
    if groups["ocr"]:
        out.append("## متن استخراج‌شده با OCR (منبع: فریم‌ها — نه گفتار، نه درک تصویری)")
        for v in groups["ocr"]:
            out.append(f"### {v['hhmmss']} [{v.get('content_type','')}]")
            if v.get("visible_text"):
                out.append(f"**متن روی تصویر:** {v['visible_text']}")
            if v.get("description"):
                out.append(v["description"])
            out.append("")
    if groups["vision"]:
        out.append("## مشاهدات تصویری مدل (منبع: تحلیل تصویری واقعی)")
        for v in groups["vision"]:
            out.append(f"### {v['hhmmss']} [{v.get('content_type','')}]")
            if v.get("description"):
                out.append(v["description"])
            if v.get("visual_only_info"):
                out.append(f"**اطلاعات فقط تصویری:** {v['visual_only_info']}")
            out.append("")
    if groups["unspecified"]:
        out.append("## یافته‌های تصویری (منبع نامشخص — برچسب‌گذاری نشده)")
        for v in groups["unspecified"]:
            out.append(f"### {v['hhmmss']} [{v.get('content_type','')}]")
            if v.get("description"):
                out.append(v["description"])
            if v.get("visible_text"):
                out.append(f"**متن روی تصویر:** {v['visible_text']}")
            if v.get("visual_only_info"):
                out.append(f"**اطلاعات فقط تصویری:** {v['visual_only_info']}")
            out.append("")
    out.append("---")
    out.append("*تولیدشده توسط Aparat Intelligence (مدل: فرآیندهای هوش مصنوعی در مکالمه). "
               "OCR و فراداده، پیاده‌سازی گفتار یا درک تصویری نیستند.*")
    return "\n".join(out)


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------

def cmd_analyze(args) -> int:
    cache = _cache()
    settings = config_mod.Settings()
    vid = _resolve_video_id(args.url)

    # 1. Metadata (cached).
    metadata = cache.get_metadata(vid)
    if not metadata:
        raw = extractor.fetch_metadata(args.url)
        metadata = _normalize(raw)
        if metadata.get("video_id"):
            cache.put_metadata(metadata["video_id"], metadata)
    if not metadata.get("video_id"):
        print("ERROR: could not parse Aparat metadata for " + args.url)
        return 3

    # 2. Download video (cached).
    video_path = cache.video_dir(vid) / "source.mp4"
    if not video_path.is_file():
        downloaded = extractor.download_best(
            args.url, cache.video_dir(vid), settings.max_download_bytes, audio_only=False,
        )
        if downloaded is None:
            print("ERROR: download failed (yt-dlp may be unavailable or Aparat is "
                  "unreachable from this host)")
            return 3
        video_path = downloaded

    # 3. Extract audio (cached).
    audio_path = cache.media_path(vid, "audio.mp3")
    if not audio_path.is_file():
        audio_processor.extract_audio(video_path, audio_path)

    # 4. Chunk audio.
    chunk_dir = cache.video_dir(vid) / "chunks"
    chunks = _chunk_audio(audio_path, chunk_dir,
                          chunk_seconds=settings.chunk_seconds,
                          overlap_seconds=settings.overlap_seconds)

    # 5. Extract frames (+ coverage validation against the video duration).
    frames_dir = cache.video_dir(vid) / "frames"
    frames_manifest = cache.video_dir(vid) / "frames.json"
    frames: list[dict] = []
    if frames_manifest.is_file():
        try:
            frames = json.loads(frames_manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            frames = []
    if not frames:
        frames = frame_extractor.extract_frames(
            video_path, frames_dir,
            interval_seconds=settings.frame_interval,
            scene_threshold=settings.scene_threshold,
            max_frames=settings.max_frames,
            max_width=settings.frame_max_width,
        )
        cache.put_artifact(vid, "frames.json", frames)
    _emit_coverage(frames, video_path, frames_dir, settings.frame_interval)

    # 6. Visual analysis — leave artifact, let the model fill it in.
    visual_manifest = cache.video_dir(vid) / "visual_analysis.json"
    visual: list[dict] = []
    if visual_manifest.is_file():
        try:
            visual = json.loads(visual_manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            visual = []

    # 7. Write artifacts + template report.
    workspace = report_generator.video_workspace(
        Path(settings.output_dir), metadata["video_id"])
    result = _write_report(workspace, metadata, None, visual, text_only=args.text_only)

    print("\nتحلیل محتوا (هوش مصنوعی) باید در مکالمه انجام شود، نه در این فرمان.")
    print(f"فضای کار: {workspace}")
    print("فایل‌های تولیدشده:")
    for name in result["files"]:
        print(f"  - {name}")

    # Print the key artifacts the model should use in conversation.
    print("\n--- متاداده ---")
    _print_json(metadata)
    print("\n--- فایل‌های صدا/م شرکت/فریم‌ها ---")
    print(f"  audio.mp3  ({audio_path.stat().st_size} bytes)")
    print(f"  chunks/  ({len(chunks)} chunks)")
    print(f"  frames/  ({len(frames)} frames)")
    print("\n--- چرا مدل این کار را می‌کند؟ ---")
    print("مدل، خاموشه‌ی تراンスکریپت و یافته‌های تصویر را می‌خواند و خلاصه،")
    print("موضوعات، جزوه، آزمون، فلش‌کارت و آزمون تولید می‌کند.")
    return 0


def cmd_report(args) -> int:
    """Re-render the report template from existing artifacts."""
    cache = _cache()
    vid = args.video_id or _resolve_video_id(args.target)
    metadata = cache.get_metadata(vid)
    if not metadata:
        print("ERROR: no metadata for video " + vid)
        return 3
    workspace = report_generator.video_workspace(
        Path(config_mod.Settings().output_dir), vid)
    visual = []
    vm = cache.video_dir(vid) / "visual_analysis.json"
    if vm.is_file():
        try:
            visual = json.loads(vm.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            visual = []
    result = _write_report(workspace, metadata, None, visual, text_only=args.text_only)
    print(f"workspace: {workspace}")
    for name in result["files"]:
        print(f"  - {name}")
    return 0


def cmd_cache(args) -> int:
    cache = _cache()
    if args.action == "list":
        for v in cache.list_videos():
            print(f"{v['video_id']}  {v['title'] or ''}  "
                  f"({len(v['artifacts'])} artifacts)")
    elif args.action == "size":
        size = cache.size()
        print(f"{size / 1024 / 1024:.1f} MiB in {cache.base}")
    elif args.action == "clear":
        removed = cache.clear(video_id=args.video_id,
                              older_than_days=args.older_than_days)
        print(f"cleared {removed} entries from {cache.base}")
    return 0


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aparat-intelligence",
        description="تحلیل محتوای ویدیوهای آپارات (محلی، بدون ارائه‌دهنده AI)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("-j", "--json", action="store_true")
        sp.add_argument("--text-only", action="store_true")

    sp = sub.add_parser("analyze", help="full local media pipeline")
    sp.add_argument("url")
    sp.add_argument("--no-frames", action="store_true")
    add_common(sp)
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("metadata", help="extract metadata only")
    sp.add_argument("url")
    sp.add_argument("--output", type=Path, default=None)
    add_common(sp)
    sp.set_defaults(func=cmd_metadata)

    sp = sub.add_parser("download", help="download the video")
    sp.add_argument("url")
    sp.add_argument("dir", type=Path)
    add_common(sp)
    sp.set_defaults(func=cmd_download)

    sp = sub.add_parser("audio", help="download + extract audio")
    sp.add_argument("url")
    sp.add_argument("dir", type=Path)
    add_common(sp)
    sp.set_defaults(func=cmd_audio)

    sp = sub.add_parser("chunks", help="chunk an audio file")
    sp.add_argument("audio")
    sp.add_argument("chunk_dir", type=Path)
    sp.add_argument("--chunk-seconds", type=int, default=600)
    sp.add_argument("--overlap-seconds", type=int, default=2)
    add_common(sp)
    sp.set_defaults(func=cmd_chunks)

    sp = sub.add_parser("frames", help="extract key frames")
    sp.add_argument("url")
    sp.add_argument("dir", type=Path)
    sp.add_argument("--no-download", action="store_true")
    sp.add_argument("--interval-seconds", type=int, default=30)
    sp.add_argument("--scene-threshold", type=float, default=0.3)
    sp.add_argument("--max-frames", type=int, default=50)
    sp.add_argument("--max-width", type=int, default=1280)
    add_common(sp)
    sp.set_defaults(func=cmd_frames)

    sp = sub.add_parser("report", help="re-render report template")
    sp.add_argument("target", nargs="?", default=None,
                    help="video id (or Aparat URL)")
    sp.add_argument("--video-id", default=None)
    add_common(sp)
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("cache", help="cache management")
    sp.add_argument("action", choices=["list", "clear", "size"])
    sp.add_argument("--video-id", default=None)
    sp.add_argument("--older-than-days", type=float, default=None)
    sp.set_defaults(func=cmd_cache)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        return args.func(args)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
