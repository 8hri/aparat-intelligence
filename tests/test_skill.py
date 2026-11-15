"""Tests: SKILL.md discovery + integrity (Hermes skill frontmatter standards).

Now that this skill is an orchestration layer with no external LLM, the
expected script set is the local media toolkit only.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SKILL_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_ROOT / "SKILL.md"


class TestSkillDiscovery(unittest.TestCase):
    def test_skill_md_exists_and_parses(self):
        self.assertTrue(SKILL_MD.is_file(), f"missing {SKILL_MD}")
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"),
                        "frontmatter must start at byte 0")
        m = re.search(r"\n---\s*\n", text[3:])
        self.assertIsNotNone(m, "frontmatter must close with ---")
        front = text[3:3 + m.start() + 3]
        self.assertIn("name:", front)
        self.assertIn("description:", front)

    def test_description_under_60_chars(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        m = re.search(r"\n---\s*\n", text[3:])
        front = text[3:3 + m.start() + 3]
        dm = re.search(r"^description:\s*(.+)$", front, re.M)
        self.assertIsNotNone(dm)
        value = dm.group(1).strip().strip('"').strip("'")
        self.assertLessEqual(len(value), 60,
                             f"description too long ({len(value)}): {value}")
        self.assertTrue(value.endswith("."))

    def test_referenced_paths_exist(self):
        """Every scripts/* / references/* path named in SKILL.md exists."""
        text = SKILL_MD.read_text(encoding="utf-8")
        missing = []
        for m in re.finditer(r"\b(scripts|references)/[A-Za-z0-9_./-]+\b",
                             text):
            rel = m.group(0).rstrip("/.\\")
            if not (SKILL_ROOT / rel).exists():
                missing.append(rel)
        self.assertEqual(missing, [], f"SKILL.md references missing files: {missing}")

    def test_cli_scripts_exist(self):
        """Local media toolkit scripts that remain."""
        for name in ("main.py", "extractor.py", "transcription.py",
                     "audio_processor.py", "frame_extractor.py",
                     "config.py", "report_generator.py", "metadata.py",
                     "cache_manager.py", "vision_analyzer.py",
                     "text_analyzer.py"):
            self.assertTrue((SKILL_ROOT / "scripts" / name).is_file(), name)
        # No provider / provider_manager / vision_analyzer LLM code.
        for name in ("provider_manager.py", "base.py", "openrouter.py",
                     "groq.py", "gemini.py"):
            self.assertFalse((SKILL_ROOT / "scripts" / name).is_file(), name)
            self.assertFalse((SKILL_ROOT / "providers" / name).is_file(), name)

    def test_no_machine_local_paths(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"/home/[A-Za-z0-9_]+/")
        self.assertNotRegex(text, r"/Users/[A-Za-z0-9_]+/")

    def test_no_hardcoded_paid_models(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertNotIn("api.openai.com/v1/chat", text)

    def test_env_example_exists(self):
        self.assertTrue((SKILL_ROOT / ".env.example").is_file())

    def test_skill_size_within_limits(self):
        size = SKILL_MD.stat().st_size
        self.assertLess(size, 100_000, f"SKILL.md is {size} bytes")
