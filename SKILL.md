---
name: aparat-intelligence
description: "Aparat video → Persian study package + active Hermes model."
version: 2.0.0
author: Mehrab Sayyad (8hri), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Aparat, Video, Transcription, Persian, Media]
    related_skills: [youtube-content]
---

# Aparat Intelligence

Turns an Aparat video URL into a full Persian-first study package: timestamped
transcript, summaries, topics/chapters, study notes, quizzes, flashcards,
visual frame analysis, and multi-video comparison.

**Architecture.** This is now an orchestration + media-toolkit layer. The skill
**never calls an external LLM** — no OpenRouter, Groq, Gemini, API keys, or
model selection anywhere. Every AI task (transcription, summarization, Q&A,
notes, quizzes, flashcards, frame analysis, comparison) is performed by the
**active Hermes model** in conversation, using Hermes tools.

## When to Use

- The user shares an Aparat URL (`aparat.com/v/...`) and wants a transcript,
  summary, notes, quiz, flashcards, or to ask questions about the video.
- The user wants to compare two or more Aparat videos on a topic.
- The user asks to "analyze", "study", or "extract content from" an Aparat video.

Don't use for: YouTube (see the `youtube-content` skill), local video files,
or downloading videos without analysis.

## Architecture

```
Aparat URL
  ↓
1. Metadata (yt-dlp / web scrape)          → metadata.json
  ↓
2. Video download (yt-dlp)                 → source.mp4
  ↓
3. Audio extraction (ffmpeg)               → audio.mp3
  ↓
4. Audio chunking (ffmpeg)                 → chunks/*.mp3
  ↓
5. Frames extraction (ffmpeg)              → frames/*.jpg
  ↓
6. Text intelligence  … done by the model
   (summarize, topics, Q&A, notes, quiz,
    flashcards, comparison)                → generated in chat, saved to files
  ↓
7. Reports (Markdown / JSON)               → output/<video_id>/
```

## How the Model Orchestrates

Follow these steps for every analysis request. The active Hermes model does the
AI work; the scripts only process media locally.

### Step 1 — Inspect the video

Ask the model to **download metadata** and report the video title, uploader,
duration, and whether it is available.

```
python <skill_dir>/scripts/main.py metadata "https://www.aparat.com/v/VIDEO_ID"
```

- If the video is unavailable, deleted, or private, report this to the user and stop.
- Record the `video_id` and `title` from the output.
- **If this fails with a yt-dlp extractor error** (Aparat's web pages are
  JavaScript SPAs with no server-rendered meta tags, so the extractor finds
  no `og:title`), do not report a network outage — `aparat.com` itself is
  reachable. Use the browser-API extraction path in
  `references/aparat-extraction.md`, which queries Aparat's own video API
  and returns richer metadata than the scraper, including the description's
  chapter timeline.

### Step 2 — Download the video

```
python <skill_dir>/scripts/main.py download "https://www.aparat.com/v/VIDEO_ID" <cache_dir>
```

`<cache_dir>` defaults to `~/.cache/aparat-intelligence/`. The video is cached,
so re-runs reuse it. If the download fails or the video is unavailable, report
this to the user. **If yt-dlp fails on the SPA**, take a direct CDN MP4 URL
from the browser-API response (`file_link_all[].urls[]`) and `curl` it into
`<cache_dir>/<video_id>/source.mp4` — see `references/aparat-extraction.md`.
If the host genuinely cannot reach Aparat (connection reset to every
`*.aparat.com` host including the CDNs), report this as a network restriction,
not a skill bug.

### Step 3 — Extract audio

```
python <skill_dir>/scripts/main.py audio "https://www.aparat.com/v/VIDEO_ID" <cache_dir>
```

This runs FFmpeg to produce a mono 16 kHz MP3 (`audio.mp3`). The output is
cached; re-runs skip re-extraction.

### Step 4 — Chunk audio

```
python <skill_dir>/scripts/main.py chunks <cache_dir>/<video_id>/audio.mp3 <cache_dir>/<video_id>/chunks
```

Audio is split into cached 10-minute chunks (with a 2-second overlap) so they
can be processed independently. Chunks overlap at sentence boundaries; the
merge logic de-duplicates the overlap.

### Step 5 — Extract frames (full duration)

```
python <skill_dir>/scripts/main.py frames "https://www.aparat.com/v/VIDEO_ID" <cache_dir>
```

Frames are key frames (scene-change detection + periodic sampling), downscaled
for efficiency, and cached. **The video's ending is always sampled**, and a
coverage check compares extracted frame timestamps with the video duration —
the result is printed and written to `<cache_dir>/<video_id>/frames/coverage.json`.
If coverage reports `INCOMPLETE` (a missing tail or gaps), re-run with a smaller
`--interval-seconds` or larger `--max-frames` before analyzing. The model
analyzes these frames for visual content.

### Step 6 — Transcription

Ask the model to transcribe the audio. Check whether the **active model**
supports audio input — many text-only models do not. If the host has a local
STT provider configured in Hermes (`hermes config set stt.provider local`,
faster-whisper), use that tool to transcribe. Otherwise, explain the limitation
to the user: *"The active model cannot process audio directly. To transcribe,
configure a local STT provider in Hermes (`hermes config set stt.provider local`);
then the model can transcribe the extracted audio."*

Keep the extracted `audio.mp3` in the cache regardless.

### Step 7 — Visual analysis and OCR (if supported)

Ask the model to analyze the extracted frames. Two distinct evidence types:

- **OCR (deterministic, always available):** text rendered on the frames
  (slide titles, captions) can be extracted with a local OCR tool such as
  tesseract. Tag every such finding `source: "ocr"`. **OCR text is NOT speech
  transcription and NOT visual understanding** — it is on-screen text only.
- **Visual observations (only if the model supports image input):** the model
  uses its `vision_analyze` tool on the frame paths. Tag every such finding
  `source: "vision"`.

If the model does not support vision, explain the limitation: frames are
extracted and available at `<cache_dir>/<video_id>/frames/` but visual
analysis requires an image-capable model. Report this rather than inventing
visual findings. Never present OCR output as if the model "saw" the scene,
and never present the video's description/metadata as if it were a transcript.

### Step 8 — Produce the study package

In conversation, the model generates each artifact. For every AI output, the
model **must cite transcript timestamps `[HH:MM:SS]`** and must **distinguish
explicitly stated facts from model inferences**. If the content does not
contain an answer, the model says so — never invents.

**Provenance is mandatory.** Every finding must be labeled with its evidence
source, and the sources must never be conflated:

| Source label | Meaning | Never present it as |
|--------------|---------|---------------------|
| transcript | Speech transcribed from the audio (STT) | — |
| ocr | Text extracted from frames with local OCR | speech transcription, or visual understanding |
| vision | Actual visual observations by an image-capable model | OCR text |
| metadata | The video's official description, title, uploader, chapters | speech transcription, or visual understanding |

The report template groups findings under these labels automatically when
entries carry a `source` field; mirror that labeling in every artifact the
model writes.

| Artifact | How it is produced |
|----------|-------------------|
| Summary | Model's reasoning over transcript + metadata |
| Topics & chapters | Model's reasoning over transcript |
| Study notes | Model's reasoning over transcript |
| Quiz | Model's reasoning over transcript |
| Flashcards | Model's reasoning over transcript |
| Q&A | Model's reasoning over transcript (verbatim answers) |
| Comparison | Model's reasoning over multiple transcripts/artifacts |
| Visual findings | `vision_analyze` tool on frame paths (if supported) |

### Step 9 — Write deliverables

The model writes the final artifacts to `~/.local/share/aparat-intelligence/output/<video_id>/`
(or the configured `output_dir` in `~/.config/aparat-intelligence/config.json`),
using the `file` tool. Deliverables:

- `report.md` — the complete study package
- `transcript.md` / `transcript.json` — timestamped transcript
- `summary.md`, `study_notes.md`, `quiz.md`, `flashcards.md`
- `metadata.json`, `visual_analysis.json`, `frames.json`

All media artifacts (audio, frames, chunks) stay in the local cache.

## Commands

`<skill_dir>` is the directory containing this SKILL.md. Run any command
through the `terminal` tool from any working directory.

### Media pipeline (orchestrated by the model)

| Command | What it does |
|---------|--------------|
| `python scripts/main.py analyze "URL"` | Full pipeline: metadata, download, audio, chunks, frames, reports. Returns artifacts to the model. |
| `python scripts/main.py metadata "URL"` | Extract video metadata only (no download). |
| `python scripts/main.py download "URL" DIR` | Download the video to `DIR`. |
| `python scripts/main.py audio "URL" DIR` | Download + extract audio. |
| `python scripts/main.py chunks AUDIO CHUNK_DIR` | Chunk an audio file (or re-chunk if different settings). |
| `python scripts/main.py frames "URL" DIR` | Extract key frames. |
| `python scripts/main.py cache list \| clear \| size` | Cache management. |

### In-conversation (done by the model, not scripts)

| Task | Tool |
|------|------|
| Summarize, topics, Q&A, notes, quiz, flashcards, comparison | The model's own reasoning over transcript + artifacts |
| Visual analysis of frames | `vision_analyze` on frame paths (if the model supports image input) |
| Transcription | STT tools (only if the active model has audio input; see Step 6) |

## Prerequisites

- `ffmpeg` and `ffprobe` on PATH (audio extraction, chunking, frames).
- `yt-dlp` on PATH (Aparat metadata + download).
- Python 3.11+ (the scripts use only the standard library).
- The active Hermes model does the AI work — no API keys required.

## Limitations

- **No external AI.** The skill uses no remote LLM. All content generation is
  done by the active Hermes model through its own tools. If a task is not
  possible with the active model, the skill reports the limitation instead of
  silently switching providers.
- **No native audio:** the active model may be text-only. Transcription
  requires a local STT provider configured in Hermes.
- **No native vision:** if the active model does not support image input, the
  skill extracts frames but cannot describe them.
- **Aparat network access:** `aparat.com` and its CDNs are reachable over
  HTTPS from this host. The known failure mode is the **yt-dlp Aparat
  extractor**, which breaks whenever Aparat's SPA ships no server-rendered
  meta tags — that is an extractor limitation, not a network problem, and the
  browser-API path (see `references/aparat-extraction.md`) works around it.
  Only report a network restriction if every `*.aparat.com` host resets the
  connection.

## Verification

```bash
python -m unittest discover -s tests -p "test_*.py"
python scripts/main.py cache size
```

The suite (offline, ~80 tests) covers URL validation, metadata, audio extraction
and chunking, frame extraction, caching, and report generation. AI outputs are
verified by the model's own reasoning and the artifact files on disk.
