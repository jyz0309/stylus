import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stylus.analyzer import PreferenceUpdate
from stylus.skill import TARGETS, SkillStore, default_skills_root


class SkillTests(unittest.TestCase):
    def test_ensure_creates_skill_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SkillStore(Path(tmp))
            store.ensure()

            self.assertTrue((Path(tmp) / "stylus" / "SKILL.md").exists())
            self.assertTrue((Path(tmp) / "stylus" / "agents" / "openai.yaml").exists())
            self.assertTrue((Path(tmp) / "stylus" / "references" / "preferences.md").exists())
            # evidence.md is no longer in the skill directory — it lives under
            # the Stylus home so it is not synced to cursor/zcode.
            self.assertFalse((Path(tmp) / "stylus" / "references" / "evidence.md").exists())
            skill_md = (Path(tmp) / "stylus" / "SKILL.md").read_text()
            self.assertIn("stylus record", skill_md)

    def test_merge_adds_preference_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SkillStore(Path(tmp))
            store.ensure()
            update = PreferenceUpdate(
                topic="change scope",
                instruction="Prefer narrow handler-layer edits for web-boundary requests.",
                confidence="medium",
                evidence="User moved validation back to the handler.",
                source_commit="abc123",
            )

            store.merge_preferences([update], [])
            store.merge_preferences([update], [])

            prefs = (Path(tmp) / "stylus" / "references" / "preferences.md").read_text()
            self.assertEqual(prefs.count("Prefer narrow handler-layer edits"), 1)

    def test_obsolete_removes_preference_line_regardless_of_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SkillStore(Path(tmp))
            store.ensure()
            update = PreferenceUpdate(
                topic="tests",
                instruction="Run every test always.",
                confidence="low",
                evidence="initial",
                source_commit="aaa111",
            )
            store.merge_preferences([update], [])

            store.merge_preferences([], ["Run every test always."])

            prefs = (Path(tmp) / "stylus" / "references" / "preferences.md").read_text()
            self.assertNotIn("Run every test always.", prefs)
            # Empty topic section should also be dropped.
            self.assertNotIn("## tests", prefs)

    def test_same_instruction_across_commits_keeps_single_preference(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SkillStore(Path(tmp))
            store.ensure()
            base = PreferenceUpdate(
                topic="scope",
                instruction="Prefer small localized diffs.",
                confidence="low",
                evidence="first commit",
                source_commit="aaa111",
            )
            second = PreferenceUpdate(
                topic="scope",
                instruction="Prefer small localized diffs.",
                confidence="low",
                evidence="second commit",
                source_commit="bbb222",
            )

            store.merge_preferences([base], [])
            store.merge_preferences([second], [])
            store.merge_preferences([second], [])  # idempotent

            prefs = (Path(tmp) / "stylus" / "references" / "preferences.md").read_text()
            self.assertEqual(prefs.count("Prefer small localized diffs."), 1)

    def test_topic_entries_append_in_chronological_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SkillStore(Path(tmp))
            store.ensure()
            first = PreferenceUpdate(
                topic="tests",
                instruction="Prefer focused tests first.",
                confidence="medium",
                evidence="first",
                source_commit="aaa111",
            )
            second = PreferenceUpdate(
                topic="tests",
                instruction="Prefer explicit smoke tests for CLI changes.",
                confidence="medium",
                evidence="second",
                source_commit="bbb222",
            )

            store.merge_preferences([first], [])
            store.merge_preferences([second], [])

            prefs = (Path(tmp) / "stylus" / "references" / "preferences.md").read_text()
            self.assertLess(
                prefs.index("Prefer focused tests first."),
                prefs.index("Prefer explicit smoke tests for CLI changes."),
            )

    def test_for_targets_builds_one_store_per_target_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = {
                "CODEX_HOME": str(tmp_path / "codex"),
                "STYLUS_CURSOR_SKILLS_ROOT": str(tmp_path / "cursor"),
                "STYLUS_ZCODE_SKILLS_ROOT": str(tmp_path / "zcode"),
                "STYLUS_CLAUDE_SKILLS_ROOT": str(tmp_path / "claude"),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                stores = SkillStore.for_targets(["zcode", "codex"])

            self.assertEqual([s.target for s in stores], ["zcode", "codex"])
            self.assertEqual(stores[0].skills_root, tmp_path / "zcode")
            self.assertEqual(stores[1].skills_root, tmp_path / "codex" / "skills")

    def test_default_skills_root_resolves_each_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = {
                "CODEX_HOME": str(tmp_path / "codex"),
                "STYLUS_CURSOR_SKILLS_ROOT": str(tmp_path / "cursor" / "skills"),
                "STYLUS_ZCODE_SKILLS_ROOT": str(tmp_path / "agents" / "skills"),
                "STYLUS_CLAUDE_SKILLS_ROOT": str(tmp_path / "claude" / "skills"),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                self.assertEqual(default_skills_root("codex"), tmp_path / "codex" / "skills")
                self.assertEqual(default_skills_root("cursor"), tmp_path / "cursor" / "skills")
                self.assertEqual(default_skills_root("zcode"), tmp_path / "agents" / "skills")
                self.assertEqual(default_skills_root("claude"), tmp_path / "claude" / "skills")

            # codex falls back to ~/.codex/skills when CODEX_HOME is unset.
            with mock.patch.dict(os.environ, {"CODEX_HOME": ""}, clear=False):
                os.environ.pop("CODEX_HOME", None)
                self.assertEqual(default_skills_root("codex"), Path.home() / ".codex" / "skills")

    def test_targets_constant_lists_all_four_targets(self):
        self.assertEqual(TARGETS, ("codex", "cursor", "zcode", "claude"))

    def test_sync_to_copies_references_and_skill_md_from_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = SkillStore(tmp_path / "codex", target="codex")
            source.ensure()
            update = PreferenceUpdate(
                topic="scope",
                instruction="Prefer small localized diffs.",
                confidence="high",
                evidence="user trimmed the diff",
                source_commit="deadbee",
            )
            source.merge_preferences([update], [])

            target = SkillStore(tmp_path / "cursor", target="cursor")
            source.sync_to(target)

            for rel in (
                "SKILL.md",
                "agents/openai.yaml",
                "references/preferences.md",
            ):
                src = (source.skill_dir / rel).read_text()
                dst = (target.skill_dir / rel).read_text()
                self.assertEqual(dst, src, f"{rel} should be copied verbatim from source")
            self.assertIn("Prefer small localized diffs.", (target.skill_dir / "references" / "preferences.md").read_text())
            # evidence.md is no longer in the skill directory.
            self.assertFalse((target.skill_dir / "references" / "evidence.md").exists())

    def test_sync_to_is_idempotent_and_overwrites_changes_in_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = SkillStore(tmp_path / "codex", target="codex")
            source.ensure()
            target = SkillStore(tmp_path / "cursor", target="cursor")

            source.sync_to(target)
            # Mutate the target copy to simulate drift, then re-sync.
            (target.skill_dir / "references" / "preferences.md").write_text("STALE CONTENT\n")
            source.sync_to(target)

            src_prefs = (source.skill_dir / "references" / "preferences.md").read_text()
            dst_prefs = (target.skill_dir / "references" / "preferences.md").read_text()
            self.assertEqual(dst_prefs, src_prefs)
            self.assertNotIn("STALE CONTENT", dst_prefs)

            # Re-syncing again must not duplicate content.
            source.sync_to(target)
            self.assertEqual(
                (target.skill_dir / "references" / "preferences.md").read_text(),
                src_prefs,
            )


if __name__ == "__main__":
    unittest.main()
