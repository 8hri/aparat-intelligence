"""Report generation: Markdown / JSON / plain text, Persian by default.

Each video gets its own workspace:

    <output>/<video_id>/
        metadata.json
        transcript.json
        transcript.md
        visual_analysis.json
        summary.md
        study_notes.md
        flashcards.json
        quiz.json
        report.md
"""

from __future__ import annotations

import json
from pathlib import Path

from .transcription import fmt_ts


def video_workspace(root: Path, video_id: str) -> Path:
    ws = Path(root) / "".join(c for c in video_id if c.isalnum() or c in "-_")
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def render_summary_md(analysis: dict, metadata: dict) -> str:
    p = analysis.get("parsed") or {}
    out = ["# خلاصه ویدیو", ""]
    out.append(f"**عنوان:** {metadata.get('title') or 'نامشخص'}  ")
    if metadata.get("uploader"):
        out.append(f"**منتشرکننده:** {metadata['uploader']}  ")
    if metadata.get("duration"):
        out.append(f"**مدت زمان:** {metadata['duration']}  ")
    if metadata.get("webpage_url"):
        out.append(f"**آدرس:** {metadata['webpage_url']}")
    out.append("")
    out.append("## خلاصه اجرایی")
    out.append(p.get("executive_summary") or "_در دسترس نیست_")
    out.append("")
    out.append("## خلاصه مفصل")
    out.append(p.get("detailed_summary") or "_در دسترس نیست_")
    out.append("")
    out.append("## نکات اصلی")
    for point in p.get("main_points") or []:
        out.append(f"- {point}")
    out.append("")
    out.append("## حقایق کلیدی")
    for fact in p.get("key_facts") or []:
        out.append(f"- {fact}")
    out.append("")
    out.append("## نتیجه‌گیری‌ها")
    for c in p.get("conclusions") or []:
        out.append(f"- {c}")
    out.append("")
    return "\n".join(out)


def render_notes_md(analysis: dict) -> str:
    p = analysis.get("parsed") or {}
    out = ["# جزوه مطالعاتی", ""]
    for note in p.get("notes") or []:
        out.append(f"## {note.get('heading', 'بخش')}")
        out.append(str(note.get("content") or ""))
        for concept in note.get("key_concepts") or []:
            out.append(f"- مفهوم: {concept}")
        for definition in note.get("definitions") or []:
            out.append(f"- **{(definition or {}).get('term', '')}**: "
                       f"{(definition or {}).get('definition', '')}")
        for example in note.get("examples") or []:
            out.append(f"- مثال: {example}")
        for formula in note.get("formulas") or []:
            out.append(f"- فرمول: `{formula}`")
        out.append("")
    glossary = p.get("glossary") or []
    if glossary:
        out.append("## واژه‌نامه")
        for g in glossary:
            out.append(f"- **{g.get('term', '')}**: {g.get('definition', '')}")
        out.append("")
    return "\n".join(out)


def render_quiz(analysis: dict) -> tuple[str, dict]:
    p = analysis.get("parsed") or {}
    quiz = p.get("quiz") or []
    out = ["# آزمون", ""]
    for i, q in enumerate(quiz, 1):
        difficulty = q.get("difficulty", "")
        out.append(f"## سوال {i} ({q.get('type', '')}"
                   f"{', ' + difficulty if difficulty else ''})")
        out.append(q.get("question") or "")
        if q.get("type") == "multiple_choice":
            for j, opt in enumerate(q.get("options") or []):
                out.append(f"{'ABCD'[j]}) {opt}")
            out.append(f"**پاسخ صحیح:** {q.get('answer_index', '?')}")
        else:
            out.append(f"**پاسخ:** {q.get('answer', '')}")
        if q.get("explanation"):
            out.append(f"*توضیح:* {q['explanation']}")
        out.append("")
    return "\n".join(out), {"quiz": quiz}


def render_flashcards(analysis: dict) -> tuple[str, dict]:
    p = analysis.get("parsed") or {}
    cards = p.get("flashcards") or []
    out = ["# فلش‌کارتها", ""]
    for i, c in enumerate(cards, 1):
        out.append(f"## کارت {i} [{c.get('category', '')}]")
        out.append(f"**رو:** {c.get('front', '')}  ")
        out.append(f"**پشت:** {c.get('back', '')}")
        out.append("")
    return "\n".join(out), {"flashcards": cards}


def render_full_report(metadata: dict, transcript: dict, visual: list[dict],
                       summary: dict, topics: dict, notes: dict,
                       quiz: dict, flashcards: dict) -> str:
    t = transcript or {}
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

    sm = (summary or {}).get("parsed") or {}
    if sm:
        out += ["## خلاصه", sm.get("executive_summary") or "", ""]
        for point in sm.get("main_points") or []:
            out.append(f"- {point}")
        out.append("")

    tp = (topics or {}).get("parsed") or {}
    chapters = tp.get("chapters") or []
    if chapters:
        out += ["## فصل‌بندی", ""]
        for ch in chapters:
            out.append(f"- {ch.get('start', '')} – {ch.get('end', '')}: "
                       f"**{ch.get('title', '')}** — {ch.get('summary', '')}")
        out.append("")

    if visual:
        out += ["## تحلیل تصویری", ""]
        for v in visual:
            out.append(f"### {v['hhmmss']} [{v.get('content_type', '')}]")
            if v.get("description"):
                out.append(v["description"])
            if v.get("visible_text"):
                out.append(f"**متن روی تصویر:** {v['visible_text']}")
            if v.get("visual_only_info"):
                out.append(f"**اطلاعات فقط تصویری:** {v['visual_only_info']}")
            out.append("")

    segments = t.get("segments") or []
    if segments:
        out += ["## متن پیاده‌سازی شده", ""]
        for s in segments:
            out.append(f"{fmt_ts(s['start'])} {s['text']}")
        out.append("")

    nt = (notes or {}).get("parsed") or {}
    if nt.get("notes"):
        out += ["## جزوه", ""]
        for note in nt["notes"]:
            out.append(f"### {note.get('heading', '')}")
            out.append(str(note.get("content") or ""))
            out.append("")
    qz = (quiz or {}).get("parsed") or {}
    if qz.get("quiz"):
        out += ["## آزمون", ""]
        for i, q in enumerate(qz["quiz"], 1):
            out.append(f"{i}. {q.get('question', '')}")
            if q.get("type") == "multiple_choice":
                for j, opt in enumerate(q.get("options") or []):
                    out.append(f"   {'ABCD'[j]}) {opt}")
                out.append(f"   *پاسخ:* {q.get('answer_index', '?')}")
            else:
                out.append(f"   *پاسخ:* {q.get('answer', '')}")
            out.append("")
    fc = (flashcards or {}).get("parsed") or {}
    if fc.get("flashcards"):
        out += ["## فلش‌کارتها", ""]
        for c in fc["flashcards"]:
            out.append(f"- **{c.get('front', '')}** → {c.get('back', '')}")
        out.append("")
    out.append("---")
    out.append("*تولیدشده توسط Aparat Intelligence*")
    return "\n".join(out)


def write_all(workspace: Path, metadata: dict, transcript: dict,
              visual: list[dict], summary: dict, topics: dict, notes: dict,
              quiz: dict, flashcards: dict,
              text_only: bool = False) -> dict:
    """Write the full artifact set. Returns {path: relative name}."""
    _write_json(workspace / "metadata.json", metadata)
    _write_json(workspace / "transcript.json", transcript)
    _write_text(workspace / "transcript.md",
                "\n".join(f"{fmt_ts(s['start'])} {s['text']}"
                          for s in (transcript or {}).get("segments") or []))
    _write_json(workspace / "visual_analysis.json", visual)
    _write_text(workspace / "summary.md", render_summary_md(summary, metadata))
    _write_text(workspace / "study_notes.md", render_notes_md(notes))
    quiz_md, quiz_json = render_quiz(quiz)
    cards_md, cards_json = render_flashcards(flashcards)
    if not text_only:
        _write_text(workspace / "quiz.md", quiz_md)
        _write_text(workspace / "flashcards.md", cards_md)
    _write_json(workspace / "quiz.json", quiz_json)
    _write_json(workspace / "flashcards.json", cards_json)
    report = render_full_report(metadata, transcript, visual, summary, topics,
                                notes, quiz, flashcards)
    _write_text(workspace / "report.md", report)
    if text_only:
        _write_text(workspace / "report.txt", report)
    return {
        "workspace": str(workspace),
        "files": sorted(p.name for p in workspace.iterdir() if p.is_file()),
    }
