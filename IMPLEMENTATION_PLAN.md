# Architectural Plan — Aparat Intelligence Refactor

**Status: COMPLETE and verified (all 79 tests pass).**
Provider files deleted, all scripts rewritten, README/references updated,
no API keys or LLM calls remain anywhere in the skill.

**Goal:** Convert the skill from a stand-alone LLM pipeline (OpenRouter/Groq/Gemini,
provider_manager, API keys) into a Hermes orchestration + local media-toolkit layer.
The active Hermes model does all AI reasoning.

**Host:** ~/.hermes/skills/media/aparat-intelligence/
**Active model:** upstage/solar-mini4:free (Nous Portal, provider `nous`)
**Hermes tools available:** terminal, file, web, vision, browser, etc.
**Key constraint:** No Python script may call or access the active model directly.
The skill instructs the model; the model uses Hermes tools.

## Files to KEEP (pure local processing)
| File | Role | Why kept |
|------|------|----------|
| scripts/audio_processor.py | ffmpeg audio extract/chunk | DONE — no LLM |
| scripts/frame_extractor.py | ffmpeg frame + scene detection | DONE — no LLM |
| scripts/extractor.py | yt-dlp metadata + download | DONE — no LLM |
| scripts/metadata.py | JSON normalization | DONE — no LLM |
| scripts/cache_manager.py | file cache | DONE — no LLM |
| scripts/report_generator.py | markdown/JSON writers | DONE — no LLM |
| scripts/config.py | paths + limits + redact | Keep, remove API keys |
| scripts/__init__.py | package marker | DONE |

## Files to DELETE
| File | Reason |
|------|--------|
| scripts/provider_manager.py | All provider orchestration |
| scripts/providers/ (base.py, gemini.py, groq.py, openrouter.py, __init__.py) | All provider code |
| scripts/text_analyzer.py | All LLM-based analysis removed |
| scripts/transcription.py | Manager-based STT removed; keep fmt_ts only |
| scripts/vision_analyzer.py | LLM-based vision removed |
| references/providers.md | No providers remain |
| references/troubleshooting.md | Updated for new architecture |
| tests/test_provider_manager.py | Removed |
| (adapted) tests/test_analysis.py, test_transcription.py, test_vision.py* | No LLM |
| (adapted) tests/*.py | Updated to new interfaces |

## Files to write/rewrite
| File | Change |
|------|--------|
| SKILL.md | Overhaul: orchestration instructions, no provider text |
| scripts/config.py | Remove api_keys / free_tier_confirmed / provider settings |
| scripts/main.py | Orchestrate without providers; no call() to LLM |
| scripts/transcription.py | Thin utilities only (fmt_ts, subtitle stash) |
| scripts/vision_analyzer.py | Frame-listing utilities only |
| scripts/text_analyzer.py | Remove all LLM calls; pure utilities if any |

## Architectural invariants
1. Zero external LLM inference in any script.
2. Zero API keys, model selection, or provider config anywhere in the skill.
3. Scripts write artifacts to disk; return results to the model via stdout + files.
4. The model generates summary/qa/notes/quiz/flashcards/comparison in conversation
   using the console output and artifacts.
5. Vision: if the active model supports image input, the model uses vision_analyze
   on frame paths; otherwise the skill explains the limitation.
6. Transcription: the model uses its STT tools if available; otherwise the skill
   explains the limitation (solar-mini4 is text-only).
7. Audio chunking: deterministic ffmpeg, cached and resumable.
8. Subtitle extraction: from the Aparat webpage via yt-dlp (if available) or
   from the downloaded video metadata; returned as text.
