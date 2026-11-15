"""Transcription support for Aparat Intelligence.

This module no longer performs speech-to-text or calls any LLM. The active
Hermes model transcribes audio (via its own audio/STT capabilities or via a
configured local STT provider); it does NOT receive audio. This module keeps:

- The canonical timestamp format utility (``fmt_ts``).
- Subtitle extraction helpers (webpage JSON-LD, JSON ``options``) — the
  Aparat page often embeds subtitles in the page HTML itself.
- Audio-chunk reference utilities so the model can point to cached chunks.

All audio processing (download, extract, chunk) lives in ``audio_processor.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def fmt_ts(seconds: float) -> str:
    """[HH:MM:SS] - the skill's canonical timestamp format."""
    total = max(int(round(seconds)), 0)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"[{h:02d}:{m:02d}:{s:02d}]"


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences (Persian and English punctuation)."""
    parts = re.split(r"(?<=[.!?؟۔])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def merge_stt_chunks(chunk_results: list[dict]) -> dict:
    """Merge per-chunk STT results into one timestamped transcript dict.

    ``chunk_results``: [{\"start\": sec, \"end\": sec, \"text\": str, \"segments\": [...]}].
    The caller is responsible for having obtained the per-chunk text (e.g. via
    the model's own STT tool over the cached chunks).

    Returns {"segments": [...], "text": str, "overlap_deduplicated": bool}.
    This is a pure merge; no LLM, no audio.
    """
    all_segments: list[dict] = []
    prev_sentences: list[str] = []
    overlap_used = False

    for i, chunk in enumerate(sorted(chunk_results, key=lambda c: c["start"])):
        offset = float(chunk.get("start", 0.0))
        text = (chunk.get("text") or "").strip()
        segments = chunk.get("segments") or []
        if not segments and text:
            segs = _split_sentences(text)
            if segs:
                spread = max(float(chunk.get("end", offset + 1)) - offset, 1.0)
                per = spread / max(len(segs), 1)
                segments = [
                    {"start": j * per, "end": (j + 1) * per, "text": s}
                    for j, s in enumerate(segs)
                ]
        if segments:
            if i > 0 and prev_sentences:
                head = _split_sentences(" ".join(s.get("text", "") for s in segments[:3]))
                dropped = 0
                kept_head = []
                for sent in head:
                    if dropped < 3 and any(
                        _similarity(sent, prev) > 0.75 for prev in prev_sentences[-4:]
                    ):
                        dropped += 1
                        overlap_used = True
                        continue
                    kept_head.append(sent)
                if dropped:
                    drop_ids = {s["text"] for s in segments[:dropped]}
                    segments = [
                        s for s in segments
                        if s["text"] not in drop_ids or (
                            s["start"] - offset > 2.0
                        )
                    ]
            for seg in segments:
                all_segments.append({
                    "start": round(float(seg.get("start", 0.0)) + offset, 2),
                    "end": round(float(seg.get("end", 0.0)) + offset, 2),
                    "text": seg.get("text", "").strip(),
                })
            prev_sentences = _split_sentences(" ".join(s["text"] for s in segments[-4:]))
        elif text:
            all_segments.append({"start": offset, "end": float(chunk.get("end", offset)),
                                 "text": text})

    all_segments = [s for s in all_segments if s["text"]]
    return {
        "segments": all_segments,
        "text": " ".join(s["text"] for s in all_segments),
        "language": "auto",
        "overlap_deduplicated": overlap_used,
    }


def _similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def transcript_to_markdown(merged: dict, title: str | None = None) -> str:
    """Render merged transcript segments as Markdown with [HH:MM:SS] prefixes."""
    lines: list[str] = []
    if title:
        lines.append(f"# پیاده‌سازی متن — {title}")
        lines.append("")
    for seg in merged.get("segments", []):
        lines.append(f"{fmt_ts(seg['start'])} {seg['text']}")
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------------
# Subtitle helpers (webpage-embedded subtitles)
# -------------------------------------------------------------------------

SUB_RE = re.compile(r"options\s*=\s*(\{.+?\});\s*$", re.S | re.M)
LD_JSON_RE = re.compile(r"<script[^>]*type=\"application/ld\+json\"[^>]*>(.*?)</script>", re.S)


def extract_embedded_subtitles(html: str) -> list[dict]:
    """Extract subtitle/cue data embedded in the Aparat page HTML.

    The Aparat page often stores a JSON ``options`` object or JSON-LD
    ``VideoObject`` in the page. We extract any array of subtitle cues
    (each with a timestamp range and text).

    Returns a list of {"start": sec, "end": sec, "text": str}.
    """
    cues: list[dict] = []

    # 1) options JSON on the page
    m = SUB_RE.search(html)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                raw = data.get("subtitles") or data.get("subs") or data.get("captions") or []
                if isinstance(raw, list):
                    cues = _normalize_cues(raw)
        except json.JSONDecodeError:
            pass

    # 2) JSON-LD VideoObject
    if not cues:
        for m in LD_JSON_RE.finditer(html):
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "VideoObject":
                raw = data.get("description") or ""
                # Some pages embed subtitle text in JSON-LD under "text"
                for key in ("subtitles", "subs", "captions", "text", "transcript"):
                    if isinstance(data.get(key), (str, list)):
                        val = data[key]
                        if isinstance(val, str):
                            val = [val]
                        cues = _normalize_cues(val)
                        if cues:
                            break

    # 3) fall back to any heuristic cue blocks
    if not cues:
        for m in re.finditer(r"(\{.*?\})\s*,\s*\"type\"\s*:\s*\"\w*sub\w*\"", html, re.S):
            try:
                cand = json.loads(m.group(1))
                if isinstance(cand, dict) and cand.get("start") is not None:
                    cues.append(_sanitize(cand))
            except json.JSONDecodeError:
                continue

    # de-dup by start time
    seen = set()
    out = []
    for c in cues:
        key = round(c.get("start", 0), 2)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _normalize_cues(raw: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start = item.get("start") or item.get("time") or item.get("tStart") or 0
        end = item.get("end") or item.get("timeEnd") or item.get("tEnd") or start
        text = item.get("text") or item.get("caption") or item.get("content") or ""
        if isinstance(text, dict):
            text = " ".join(str(v) for v in text.values() if v)
        out.append({
            "start": float(start) if _is_number(start) else 0.0,
            "end": float(end) if _is_number(end) else 0.0,
            "text": str(text),
        })
    return out


def _is_number(x) -> bool:
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def _sanitize(item: dict) -> dict:
    return {
        "start": float(item.get("start", 0) or 0),
        "end": float(item.get("end", 0) or 0),
        "text": str(item.get("text", "") or ""),
    }
