"""Content-purpose utilities for Aparat Intelligence.

This module no longer performs any AI inference. The active Hermes model is
responsible for every content task (summary, topics, Q&A, notes, quiz,
flashcards, comparison). This module only carries lightweight utilities that
the model needs when it orchestrates.

No LLM, no API key, no provider code anywhere in this skill.
"""

from __future__ import annotations

import json
import re


def parse_json_loose(text: str):
    """Parse a JSON object/array from a model response, tolerating fences.

    Kept here because the active model (text in conversation) may emit JSON
    that the skill's reporting layers consume. No LLM calls.
    """
    text = (text or "").strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
    return None


def fmt_ts(seconds: float) -> str:
    """[HH:MM:SS] - the skill's canonical timestamp format."""
    total = max(int(round(seconds)), 0)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"[{h:02d}:{m:02d}:{s:02d}]"


def truncate(body: str, max_chars: int = 40000) -> str:
    """Truncate a long transcript with an explicit note."""
    if len(body) > max_chars:
        return body[:max_chars] + "\n... (متن کوتاه شد)"
    return body


def build_context(transcript: dict, metadata: dict | None = None,
                  visual: list[dict] | None = None, max_chars: int = 40000) -> str:
    """Assemble a transcript+metadata+visual context blob for the model.

    Returns a plain-text context block the model can reason over. No LLM.
    Each evidence type is labeled distinctly: transcript speech, OCR text
    from frames, and visual observations are never conflated.
    """
    meta = metadata or {}
    lines = [f"عنوان ویدیو: {meta.get('title') or 'نامشخص'}"]
    if meta.get("uploader"):
        lines.append(f"منتشرکننده: {meta['uploader']}")
    if meta.get("duration"):
        lines.append(f"مدت: {meta['duration']}")
    lines.append("منبع: فراداده/توضیحات رسمی ویدیو (این منبع گفتار یا تصویر نیست)")
    segments = (transcript or {}).get("segments") or []
    if segments:
        body = "\n".join(f"{fmt_ts(s['start'])} {s['text']}" for s in segments)
        body = truncate(body, max_chars)
        lines.append("متن پیاده‌سازی شده با مهر زمانی (منبع: transcript از گفتار):")
        lines.append(body)
        lines.append("")
    if visual:
        ocr_items = [v for v in visual if str(v.get("source", "")).lower() == "ocr"]
        vision_items = [v for v in visual
                        if str(v.get("source", "")).lower() == "vision"]
        other_items = [v for v in visual
                       if str(v.get("source", "")).lower() not in ("ocr", "vision")]
        if ocr_items:
            lines.append("متن استخراج‌شده از روی فریم‌ها با OCR (با مهر زمانی) — "
                         "این متن گفتار نیست و درک تصویری نیست:")
            lines.append("\n".join(_visual_line(v) for v in ocr_items))
            lines.append("")
        if vision_items:
            lines.append("مشاهدات تصویری مدل (با مهر زمانی) — تحلیل تصویری واقعی:")
            lines.append("\n".join(_visual_line(v) for v in vision_items))
            lines.append("")
        if other_items:
            lines.append("یافته‌های تصویری (منبع نامشخص — برچسب‌گذاری نشده):")
            lines.append("\n".join(_visual_line(v) for v in other_items))
            lines.append("")
    return "\n".join(lines)


def _visual_line(v: dict) -> str:
    parts = f"{v['hhmmss']} [{v['content_type']}] {v.get('description', '')}"
    if v.get("visible_text"):
        parts += f" | متن روی تصویر: {v['visible_text']}"
    if v.get("visual_only_info"):
        parts += f" | اطلاعات فقط تصویری: {v['visual_only_info']}"
    return parts
