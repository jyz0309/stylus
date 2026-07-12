from __future__ import annotations

import os
import re
import shutil
from collections.abc import Sequence
from pathlib import Path

from .analyzer import PreferenceUpdate


PREFERENCES_HEADER = "# Stylus Preferences\n\n"

# Install targets in canonical order. `codex` is the single source of truth:
# the analyzer only updates its references, and `install skill` syncs the codex
# skill directory to every other target.
TARGETS: tuple[str, ...] = ("codex", "cursor", "zcode", "claude")


class SkillStore:
    def __init__(self, skills_root: Path | None = None, target: str = "codex") -> None:
        self.target = target
        self.skills_root = skills_root or default_skills_root(target)
        self.skill_dir = self.skills_root / "stylus"
        self.references_dir = self.skill_dir / "references"

    @classmethod
    def for_targets(cls, targets: Sequence[str]) -> list[SkillStore]:
        """Build a SkillStore per requested target, preserving order."""
        return [cls(target=t) for t in targets]

    def ensure(self) -> None:
        self.references_dir.mkdir(parents=True, exist_ok=True)
        (self.skill_dir / "agents").mkdir(parents=True, exist_ok=True)
        self._write_if_missing(self.skill_dir / "SKILL.md", default_skill_md())
        self._write_if_missing(self.skill_dir / "agents" / "openai.yaml", default_openai_yaml())
        self._write_if_missing(self.references_dir / "preferences.md", PREFERENCES_HEADER)

    def sync_to(self, target_store: SkillStore) -> None:
        """Copy this store's `stylus/` directory onto another target store.

        The codex store is the single source of truth; this propagates its
        learned `references/`, `SKILL.md`, and `agents/` to cursor/zcode/claude
        so all installed copies stay consistent. Idempotent: re-running
        overwrites existing files in place.
        """
        target_store.ensure()
        shutil.copytree(
            self.skill_dir,
            target_store.skill_dir,
            dirs_exist_ok=True,
        )

    def merge_preferences(self, updates: list[PreferenceUpdate], obsolete_instructions: list[str]) -> None:
        self.ensure()
        prefs_path = self.references_dir / "preferences.md"
        prefs = prefs_path.read_text(encoding="utf-8")

        for obsolete in obsolete_instructions:
            prefs = _remove_preference_line(prefs, obsolete)
        prefs = _drop_empty_topic_sections(prefs)

        for update in updates:
            pref_line = f"- [{update.confidence}] {update.instruction}\n"
            if not _preference_line_present(prefs, update.instruction):
                prefs = _append_topic_line(prefs, update.topic, pref_line)

        prefs_path.write_text(prefs, encoding="utf-8")

    @staticmethod
    def _write_if_missing(path: Path, content: str) -> None:
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def default_skills_root(target: str = "codex") -> Path:
    if target == "codex":
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            return Path(codex_home) / "skills"
        return Path.home() / ".codex" / "skills"
    if target == "cursor":
        override = os.environ.get("STYLUS_CURSOR_SKILLS_ROOT")
        if override:
            return Path(override)
        return Path.home() / ".cursor" / "skills"
    if target == "zcode":
        override = os.environ.get("STYLUS_ZCODE_SKILLS_ROOT")
        if override:
            return Path(override)
        return Path.home() / ".agents" / "skills"
    if target == "claude":
        override = os.environ.get("STYLUS_CLAUDE_SKILLS_ROOT")
        if override:
            return Path(override)
        return Path.home() / ".claude" / "skills"
    raise ValueError(f"unknown skill target: {target!r}")


def default_skill_md() -> str:
    return """---
name: stylus
description: Personal coding-style preferences learned from comparing user commits to prior agent changes. Use for coding tasks to align implementation style, scope, abstractions, tests, comments, and verification with the user's observed habits.
---

# Stylus

Before coding, read `references/preferences.md` when it exists. Apply those preferences unless they conflict with explicit user instructions or stronger repository-local patterns.

After making code changes in a Git repository, leave the edits **unstaged** in the working tree and record the agent baseline before handing work back:

```bash
stylus record --summary "<short summary of agent changes>"
```

Do not run `git add`, `git commit`, or `git stash` before recording. Stylus captures the working-tree diff and will refuse to record if the changes are already staged.

If `stylus` is not on `PATH`, use `python3 -m stylus record --summary "<short summary of agent changes>"` from a checkout where Stylus is importable. If the command fails because Stylus is not installed for that repository, mention that baseline recording was skipped.

When preferences conflict, follow the newest explicit user instruction first, then repository conventions, then Stylus preferences.
"""


def default_openai_yaml() -> str:
    return """display_name: Stylus
short_description: Apply learned personal coding-style preferences.
default_prompt: Use my learned Stylus coding preferences for this task.
"""


def _append_topic_line(content: str, topic: str, line: str) -> str:
    heading = f"## {topic}\n"
    if heading not in content:
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n{heading}"
    start = content.index(heading) + len(heading)
    next_heading = content.find("\n## ", start)
    marker = len(content) if next_heading == -1 else next_heading
    prefix = content[:marker]
    suffix = content[marker:]
    if not prefix.endswith("\n"):
        prefix += "\n"
    return prefix + line + suffix


_PREF_LINE_RE = re.compile(r"^- \[[^\]]+\] (?P<instruction>.+)$", re.MULTILINE)


def _preference_line_present(content: str, instruction: str) -> bool:
    for match in _PREF_LINE_RE.finditer(content):
        if match.group("instruction") == instruction:
            return True
    return False


def count_preferences(prefs_text: str) -> tuple[int, int]:
    """Return (preference_count, topic_count) from a ``preferences.md`` body.

    A preference is any ``- [confidence] instruction`` bullet; a topic is any
    ``## heading``. Used by ``stylus status`` to summarize learned preferences.
    """
    pref_count = len(_PREF_LINE_RE.findall(prefs_text))
    topic_count = len(re.findall(r"^## ", prefs_text, re.MULTILINE))
    return pref_count, topic_count


def _remove_preference_line(content: str, instruction: str) -> str:
    pattern = re.compile(
        rf"^- \[[^\]]+\] {re.escape(instruction)}\n",
        re.MULTILINE,
    )
    return pattern.sub("", content)


def _drop_empty_topic_sections(content: str) -> str:
    # Remove a `## topic` heading whose section body has no `- ` bullets.
    pattern = re.compile(
        r"\n?## [^\n]+\n(?P<body>(?:(?!\n## )[^\n]*\n)*)",
    )

    def replace(match: re.Match[str]) -> str:
        body = match.group("body")
        if any(line.startswith("- ") for line in body.splitlines()):
            return match.group(0)
        return ""

    return pattern.sub(replace, content)
