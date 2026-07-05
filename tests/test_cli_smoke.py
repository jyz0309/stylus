import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stylus.analyzer import (
    CommandAnalyzerProvider,
    FakeAnalyzerProvider,
    OpenAIChatAnalyzerProvider,
    OpenAIResponsesAnalyzerProvider,
    provider_from_env,
    provider_name,
)
from stylus.cli import diffs_equivalent, truncate_diff
from stylus.paths import diffs_dir as diffs_dir_for
from stylus.paths import repo_hash, state_path, stylus_home


class CliSmokeTests(unittest.TestCase):
    def test_truncate_diff_respects_env_limit(self):
        with patch.dict(os.environ, {"STYLUS_MAX_DIFF_BYTES": "10"}):
            result = truncate_diff("0123456789abcdef", "user_diff")

        self.assertTrue(result.startswith("0123456789"))
        self.assertIn("Stylus truncated user_diff", result)
        self.assertIn("original 16 bytes", result)

    def test_truncate_diff_can_be_disabled_with_zero_limit(self):
        with patch.dict(os.environ, {"STYLUS_MAX_DIFF_BYTES": "0"}):
            result = truncate_diff("0123456789abcdef", "user_diff")

        self.assertEqual(result, "0123456789abcdef")

    def test_module_help_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "stylus", "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)
        self.assertIn("init", result.stdout)
        self.assertIn("install", result.stdout)
        self.assertNotIn("install-hook", result.stdout)
        self.assertIn("analyze", result.stdout)

    def test_install_help_lists_split_install_targets(self):
        result = subprocess.run(
            [sys.executable, "-m", "stylus", "install", "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skill", result.stdout)
        self.assertIn("hook", result.stdout)
        self.assertIn("config", result.stdout)

    def test_record_writes_branch_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            stylus_home_dir = tmp_path / "stylus-home"
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)
            (root / "example.txt").write_text("one\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE)
            (root / "example.txt").write_text("two\n")

            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src"), "STYLUS_HOME": str(stylus_home_dir)}
            result = subprocess.run(
                [sys.executable, "-m", "stylus", "record", "--summary", "change text"],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with patch.dict(os.environ, {"STYLUS_HOME": str(stylus_home_dir)}):
                state = state_path().read_text()
            self.assertIn("change text", state)
            repo_id = str(root.resolve())
            with patch.dict(os.environ, {"STYLUS_HOME": str(stylus_home_dir)}):
                diffs = list(diffs_dir_for(repo_id).glob("*.diff"))
            self.assertEqual(len(diffs), 1)
            self.assertIn("+two", diffs[0].read_text())
            # No .stylus directory should be created inside the repo.
            self.assertFalse((root / ".stylus").exists())

    def test_record_works_in_empty_repository_before_first_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            stylus_home_dir = tmp_path / "stylus-home"
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)
            (root / "main.go").write_text("package main\n")

            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src"), "STYLUS_HOME": str(stylus_home_dir)}
            result = subprocess.run(
                [sys.executable, "-m", "stylus", "record", "--summary", "initial go file"],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with patch.dict(os.environ, {"STYLUS_HOME": str(stylus_home_dir)}):
                state = state_path().read_text()
                repo_id = str(root.resolve())
                diffs = list(diffs_dir_for(repo_id).glob("*.diff"))
            self.assertIn('"base_revision": ""', state)
            self.assertEqual(len(diffs), 1)
            self.assertIn("main.go", diffs[0].read_text())
            self.assertIn("+package main", diffs[0].read_text())

    def test_record_refuses_when_changes_are_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            stylus_home_dir = tmp_path / "stylus-home"
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)
            (root / "example.txt").write_text("one\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE)
            (root / "example.txt").write_text("two\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)

            env = {**os.environ, "PYTHONPATH": str(Path.cwd() / "src"), "STYLUS_HOME": str(stylus_home_dir)}
            result = subprocess.run(
                [sys.executable, "-m", "stylus", "record", "--summary", "staged"],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already staged", result.stderr)
            with patch.dict(os.environ, {"STYLUS_HOME": str(stylus_home_dir)}):
                repo_id = str(root.resolve())
                self.assertFalse(diffs_dir_for(repo_id).exists())

    def test_hook_analyzes_user_commit_after_agent_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            # Keep CODEX_HOME / HOME / GIT_CONFIG_GLOBAL / STYLUS_HOME outside
            # the repo so the install commands do not pollute the working tree
            # that record captures.
            env = {
                **os.environ,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "CODEX_HOME": str(tmp_path / "codex-home"),
                "HOME": str(tmp_path / "home"),
                "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
                "STYLUS_HOME": str(tmp_path / "stylus-home"),
            }
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)
            (root / "example.txt").write_text("one\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE)

            subprocess.run([sys.executable, "-m", "stylus", "init"], cwd=root, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "skill"], cwd=root, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "hook"], cwd=root, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "config"], cwd=root, env=env, check=True)
            (root / "example.txt").write_text("two\n")
            subprocess.run(
                [sys.executable, "-m", "stylus", "record", "--summary", "agent changed text"],
                cwd=root,
                env=env,
                check=True,
            )
            # User corrects the agent output before committing so the commit
            # diff differs from the recorded baseline and analysis runs.
            (root / "example.txt").write_text("three\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "user correction"], cwd=root, env=env, check=True, stdout=subprocess.PIPE)

            preferences = tmp_path / "codex-home" / "skills" / "stylus" / "references" / "preferences.md"
            self.assertTrue(preferences.exists())
            self.assertIn("Review user edits", preferences.read_text())

    def test_hook_skips_analysis_when_commit_matches_agent_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            env = {
                **os.environ,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "CODEX_HOME": str(tmp_path / "codex-home"),
                "HOME": str(tmp_path / "home"),
                "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
                "STYLUS_HOME": str(tmp_path / "stylus-home"),
            }
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)
            (root / "example.txt").write_text("one\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE)

            subprocess.run([sys.executable, "-m", "stylus", "init"], cwd=root, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "skill"], cwd=root, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "hook"], cwd=root, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "config"], cwd=root, env=env, check=True)
            (root / "example.txt").write_text("two\n")
            subprocess.run(
                [sys.executable, "-m", "stylus", "record", "--summary", "agent changed text"],
                cwd=root,
                env=env,
                check=True,
            )
            # User commits the agent baseline verbatim: no correction to learn.
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "accept agent change"], cwd=root, env=env, check=True, stdout=subprocess.PIPE)

            preferences = tmp_path / "codex-home" / "skills" / "stylus" / "references" / "preferences.md"
            self.assertTrue(preferences.exists())
            self.assertNotIn("Review user edits", preferences.read_text())

            with patch.dict(os.environ, {"STYLUS_HOME": str(tmp_path / "stylus-home")}):
                state = json.loads(state_path().read_text())
            results = [
                analysis["result"]
                for repo in state.get("repositories", {}).values()
                for branch in repo.get("branches", {}).values()
                for analysis in branch.get("analyses", [])
            ]
            self.assertIn("skipped", results)
            self.assertNotIn("updated", results)

    def test_diffs_equivalent_ignores_file_order(self):
        baseline_diff = (
            "diff --git a/zfile.txt b/zfile.txt\n"
            "index 111..222 100644\n--- a/zfile.txt\n+++ b/zfile.txt\n"
            "@@ -1 +1 @@\n-one\n+two\n"
            "diff --git a/afile.txt b/afile.txt\n"
            "new file mode 100644\nindex 000..333 100644\n--- /dev/null\n+++ b/afile.txt\n"
            "@@ -0,0 +1 @@\n+brand new\n"
        )
        user_diff = (
            "diff --git a/afile.txt b/afile.txt\n"
            "new file mode 100644\nindex 000..333 100644\n--- /dev/null\n+++ b/afile.txt\n"
            "@@ -0,0 +1 @@\n+brand new\n"
            "diff --git a/zfile.txt b/zfile.txt\n"
            "index 111..222 100644\n--- a/zfile.txt\n+++ b/zfile.txt\n"
            "@@ -1 +1 @@\n-one\n+two\n"
        )
        self.assertTrue(diffs_equivalent(baseline_diff, user_diff))

    def test_diffs_equivalent_detects_changed_content(self):
        baseline_diff = (
            "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-one\n+two\n"
        )
        user_diff = (
            "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-one\n+three\n"
        )
        self.assertFalse(diffs_equivalent(baseline_diff, user_diff))

    def test_diffs_equivalent_treats_empty_diffs_as_equal(self):
        self.assertTrue(diffs_equivalent("", ""))
        self.assertFalse(diffs_equivalent("", "diff --git a/f.txt b/f.txt\n+new\n"))

    def test_install_skill_default_installs_all_four_and_prints_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            codex_root = tmp_path / "codex-home" / "skills"
            cursor_root = tmp_path / "cursor-home" / "skills"
            zcode_root = tmp_path / "agents-home" / "skills"
            claude_root = tmp_path / "claude-home" / "skills"
            env = {
                **os.environ,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "CODEX_HOME": str(tmp_path / "codex-home"),
                "STYLUS_CURSOR_SKILLS_ROOT": str(cursor_root),
                "STYLUS_ZCODE_SKILLS_ROOT": str(zcode_root),
                "STYLUS_CLAUDE_SKILLS_ROOT": str(claude_root),
            }

            result = subprocess.run(
                [sys.executable, "-m", "stylus", "install", "skill"],
                cwd=tmp_path,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Installed Stylus skill at:", result.stdout)
            self.assertIn(f"  codex: {codex_root / 'stylus'}", result.stdout)
            self.assertIn(f"  cursor: {cursor_root / 'stylus'}", result.stdout)
            self.assertIn(f"  zcode: {zcode_root / 'stylus'}", result.stdout)
            self.assertIn(f"  claude: {claude_root / 'stylus'}", result.stdout)

            for root in (codex_root, cursor_root, zcode_root, claude_root):
                self.assertTrue((root / "stylus" / "SKILL.md").exists())
                self.assertTrue((root / "stylus" / "agents" / "openai.yaml").exists())
                self.assertTrue((root / "stylus" / "references" / "preferences.md").exists())

            # All four copies share the same SKILL.md content (synced from codex).
            codex_skill = (codex_root / "stylus" / "SKILL.md").read_text()
            self.assertEqual((cursor_root / "stylus" / "SKILL.md").read_text(), codex_skill)
            self.assertEqual((zcode_root / "stylus" / "SKILL.md").read_text(), codex_skill)
            self.assertEqual((claude_root / "stylus" / "SKILL.md").read_text(), codex_skill)

    def test_install_skill_target_subset_only_installs_named_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            codex_root = tmp_path / "codex-home" / "skills"
            cursor_root = tmp_path / "cursor-home" / "skills"
            zcode_root = tmp_path / "agents-home" / "skills"
            env = {
                **os.environ,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "CODEX_HOME": str(tmp_path / "codex-home"),
                "STYLUS_CURSOR_SKILLS_ROOT": str(cursor_root),
                "STYLUS_ZCODE_SKILLS_ROOT": str(zcode_root),
            }

            result = subprocess.run(
                [sys.executable, "-m", "stylus", "install", "skill", "--target", "cursor"],
                cwd=tmp_path,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("cursor:", result.stdout)
            # codex is always prepared as the sync source, but is not reported as
            # an installed target when the caller did not request it.
            self.assertNotIn("codex:", result.stdout)
            self.assertNotIn("zcode:", result.stdout)

            # codex store exists (sync source) and cursor was synced from it.
            self.assertTrue((codex_root / "stylus" / "SKILL.md").exists())
            self.assertTrue((cursor_root / "stylus" / "SKILL.md").exists())
            self.assertFalse((zcode_root / "stylus").exists())
            self.assertEqual(
                (cursor_root / "stylus" / "SKILL.md").read_text(),
                (codex_root / "stylus" / "SKILL.md").read_text(),
            )

    def test_install_skill_target_can_be_repeated(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cursor_root = tmp_path / "cursor-home" / "skills"
            zcode_root = tmp_path / "agents-home" / "skills"
            env = {
                **os.environ,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "CODEX_HOME": str(tmp_path / "codex-home"),
                "STYLUS_CURSOR_SKILLS_ROOT": str(cursor_root),
                "STYLUS_ZCODE_SKILLS_ROOT": str(zcode_root),
            }

            result = subprocess.run(
                [
                    sys.executable, "-m", "stylus", "install", "skill",
                    "--target", "cursor", "--target", "zcode",
                ],
                cwd=tmp_path,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("cursor:", result.stdout)
            self.assertIn("zcode:", result.stdout)
            self.assertNotIn("codex:", result.stdout)
            self.assertTrue((cursor_root / "stylus" / "SKILL.md").exists())
            self.assertTrue((zcode_root / "stylus" / "SKILL.md").exists())

    def test_provider_name_labels_each_provider_type(self):
        self.assertIn("fake", provider_name(FakeAnalyzerProvider()))
        cmd = CommandAnalyzerProvider(["python3", "-c", "print('{}')"])
        self.assertIn("command", provider_name(cmd))
        self.assertIn("python3", provider_name(cmd))
        oai = OpenAIResponsesAnalyzerProvider(api_key="sk-test", model="gpt-test", base_url="http://example/v1")
        self.assertIn("openai-responses", provider_name(oai))
        self.assertIn("gpt-test", provider_name(oai))
        chat = OpenAIChatAnalyzerProvider(api_key="sk-test", model="gpt-chat", base_url="http://example/v1")
        self.assertIn("openai-chat", provider_name(chat))
        self.assertIn("gpt-chat", provider_name(chat))

    def test_analyze_debug_prints_provider_and_output_to_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            # No OPENAI_API_KEY / STYLUS_ANALYZER_CMD => FakeAnalyzerProvider,
            # which is deterministic and needs no network.
            env = {
                "PYTHONPATH": str(Path.cwd() / "src"),
                "CODEX_HOME": str(tmp_path / "codex-home"),
                "HOME": str(tmp_path / "home"),
                "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
                "STYLUS_HOME": str(tmp_path / "stylus-home"),
                # Ensure the fake provider is selected.
                "PATH": os.environ.get("PATH", ""),
            }
            for key in ("OPENAI_API_KEY", "STYLUS_ANALYZER_CMD"):
                env.pop(key, None)
            env_patch = {k: "" for k in ("OPENAI_API_KEY", "STYLUS_ANALYZER_CMD")}

            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)
            (root / "example.txt").write_text("one\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE)

            subprocess.run([sys.executable, "-m", "stylus", "init"], cwd=root, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "skill"], cwd=root, env=env, check=True)
            (root / "example.txt").write_text("two\n")
            subprocess.run(
                [sys.executable, "-m", "stylus", "record", "--summary", "agent changed text"],
                cwd=root,
                env=env,
                check=True,
            )
            # User corrects so the commit differs from the baseline.
            (root / "example.txt").write_text("three\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "user correction"], cwd=root, env=env, check=True, stdout=subprocess.PIPE)

            with patch.dict(os.environ, env_patch, clear=False):
                result = subprocess.run(
                    [sys.executable, "-m", "stylus", "analyze", "--commit", "HEAD", "--debug"],
                    cwd=root,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            # Debug diagnostics go to stderr.
            self.assertIn("Stylus analyze (debug)", result.stderr)
            self.assertIn("provider: fake", result.stderr)
            self.assertIn("commit:", result.stderr)
            self.assertIn("baseline_diff:", result.stderr)
            self.assertIn("user_diff:", result.stderr)
            self.assertIn("=== analyzer output ===", result.stderr)
            # The parsed output is JSON and contains the fake preference.
            self.assertIn("Review user edits", result.stderr)
            self.assertIn("obsolete_preferences", result.stderr)
            # The normal success message still goes to stdout.
            self.assertIn("Stylus updated preferences", result.stdout)

    def test_analyze_without_debug_omits_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            env = {
                "PYTHONPATH": str(Path.cwd() / "src"),
                "CODEX_HOME": str(tmp_path / "codex-home"),
                "HOME": str(tmp_path / "home"),
                "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
                "STYLUS_HOME": str(tmp_path / "stylus-home"),
                "PATH": os.environ.get("PATH", ""),
            }
            env.pop("OPENAI_API_KEY", None)
            env.pop("STYLUS_ANALYZER_CMD", None)

            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)
            (root / "example.txt").write_text("one\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE)

            subprocess.run([sys.executable, "-m", "stylus", "init"], cwd=root, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "skill"], cwd=root, env=env, check=True)
            (root / "example.txt").write_text("two\n")
            subprocess.run(
                [sys.executable, "-m", "stylus", "record", "--summary", "agent changed text"],
                cwd=root,
                env=env,
                check=True,
            )
            (root / "example.txt").write_text("three\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "user correction"], cwd=root, env=env, check=True, stdout=subprocess.PIPE)

            result = subprocess.run(
                [sys.executable, "-m", "stylus", "analyze", "--commit", "HEAD"],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Stylus updated preferences", result.stdout)
            self.assertNotIn("Stylus analyze (debug)", result.stderr)
            self.assertNotIn("=== analyzer output ===", result.stderr)

    def test_analyze_records_failed_result_on_provider_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            # A custom analyzer command that emits invalid JSON, forcing the
            # parse layer to raise and the CLI to record a 'failed' analysis.
            bad_analyzer = tmp_path / "bad-analyzer.py"
            bad_analyzer.write_text("print('this is not json')\n")
            env = {
                **os.environ,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "CODEX_HOME": str(tmp_path / "codex-home"),
                "HOME": str(tmp_path / "home"),
                "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
                "STYLUS_HOME": str(tmp_path / "stylus-home"),
                "STYLUS_ANALYZER_CMD": f"{sys.executable} {bad_analyzer}",
                "PATH": os.environ.get("PATH", ""),
            }
            env.pop("OPENAI_API_KEY", None)

            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)
            (root / "example.txt").write_text("one\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE)

            subprocess.run([sys.executable, "-m", "stylus", "init"], cwd=root, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "skill"], cwd=root, env=env, check=True)
            (root / "example.txt").write_text("two\n")
            subprocess.run(
                [sys.executable, "-m", "stylus", "record", "--summary", "agent changed text"],
                cwd=root, env=env, check=True,
            )
            (root / "example.txt").write_text("three\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "user correction"], cwd=root, env=env, check=True, stdout=subprocess.PIPE)

            result = subprocess.run(
                [sys.executable, "-m", "stylus", "analyze", "--commit", "HEAD"],
                cwd=root, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )

            # The CLI exits non-zero with a friendly message (no bare traceback).
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Stylus analysis failed:", result.stderr)
            self.assertIn("valid JSON", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

            # The failure is recorded in state as 'failed' for auditability.
            with patch.dict(os.environ, {"STYLUS_HOME": str(tmp_path / "stylus-home")}):
                state = json.loads(state_path().read_text())
            results = [
                analysis["result"]
                for repo in state.get("repositories", {}).values()
                for branch in repo.get("branches", {}).values()
                for analysis in branch.get("analyses", [])
            ]
            self.assertIn("failed", results)

    def test_analyze_writes_evidence_to_stylus_home_not_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            stylus_home_dir = tmp_path / "stylus-home"
            env = {
                **os.environ,
                "PYTHONPATH": str(Path.cwd() / "src"),
                "CODEX_HOME": str(tmp_path / "codex-home"),
                "HOME": str(tmp_path / "home"),
                "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
                "STYLUS_HOME": str(stylus_home_dir),
                "PATH": os.environ.get("PATH", ""),
            }
            env.pop("OPENAI_API_KEY", None)
            env.pop("STYLUS_ANALYZER_CMD", None)

            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)
            (root / "example.txt").write_text("one\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE)

            subprocess.run([sys.executable, "-m", "stylus", "init"], cwd=root, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "skill"], cwd=root, env=env, check=True)
            (root / "example.txt").write_text("two\n")
            subprocess.run(
                [sys.executable, "-m", "stylus", "record", "--summary", "agent changed text"],
                cwd=root, env=env, check=True,
            )
            (root / "example.txt").write_text("three\n")
            subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "user correction"], cwd=root, env=env, check=True, stdout=subprocess.PIPE)

            subprocess.run(
                [sys.executable, "-m", "stylus", "analyze", "--commit", "HEAD"],
                cwd=root, env=env, check=True,
            )

            # Evidence lives under STYLUS_HOME, not in the skill directory.
            evidence_file = stylus_home_dir / "evidence.md"
            self.assertTrue(evidence_file.exists(), "evidence.md should be in STYLUS_HOME")
            evidence = evidence_file.read_text()
            self.assertIn("Review user edits", evidence)

            # The skill's references directory must NOT contain evidence.md.
            codex_skill_refs = tmp_path / "codex-home" / "skills" / "stylus" / "references"
            self.assertTrue((codex_skill_refs / "preferences.md").exists())
            self.assertFalse((codex_skill_refs / "evidence.md").exists())

    def _uninstall_env(self, tmp_path: Path) -> dict:
        return {
            **os.environ,
            "PYTHONPATH": str(Path.cwd() / "src"),
            "CODEX_HOME": str(tmp_path / "codex-home"),
            "HOME": str(tmp_path / "home"),
            "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
            "STYLUS_HOME": str(tmp_path / "stylus-home"),
        }

    def test_uninstall_skill_removes_all_four_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = self._uninstall_env(tmp_path)
            # Install first.
            subprocess.run([sys.executable, "-m", "stylus", "install", "skill"], cwd=tmp_path, env=env, check=True)
            codex_skill = tmp_path / "codex-home" / "skills" / "stylus"
            cursor_skill = tmp_path / "home" / ".cursor" / "skills" / "stylus"
            zcode_skill = tmp_path / "home" / ".agents" / "skills" / "stylus"
            claude_skill = tmp_path / "home" / ".claude" / "skills" / "stylus"
            self.assertTrue(codex_skill.exists())
            self.assertTrue(cursor_skill.exists())
            self.assertTrue(zcode_skill.exists())
            self.assertTrue(claude_skill.exists())

            result = subprocess.run(
                [sys.executable, "-m", "stylus", "uninstall", "skill"],
                cwd=tmp_path, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Removed Stylus skill from codex", result.stdout)
            self.assertIn("Removed Stylus skill from cursor", result.stdout)
            self.assertIn("Removed Stylus skill from zcode", result.stdout)
            self.assertIn("Removed Stylus skill from claude", result.stdout)
            self.assertFalse(codex_skill.exists())
            self.assertFalse(cursor_skill.exists())
            self.assertFalse(zcode_skill.exists())
            self.assertFalse(claude_skill.exists())

    def test_uninstall_skill_target_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = self._uninstall_env(tmp_path)
            subprocess.run([sys.executable, "-m", "stylus", "install", "skill"], cwd=tmp_path, env=env, check=True)
            codex_skill = tmp_path / "codex-home" / "skills" / "stylus"
            cursor_skill = tmp_path / "home" / ".cursor" / "skills" / "stylus"

            result = subprocess.run(
                [sys.executable, "-m", "stylus", "uninstall", "skill", "--target", "cursor"],
                cwd=tmp_path, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(cursor_skill.exists())
            self.assertTrue(codex_skill.exists(), "codex should be untouched")

    def test_uninstall_skill_idempotent_when_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = self._uninstall_env(tmp_path)
            result = subprocess.run(
                [sys.executable, "-m", "stylus", "uninstall", "skill"],
                cwd=tmp_path, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No Stylus skill found", result.stdout)

    def test_uninstall_hook_removes_stylus_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = self._uninstall_env(tmp_path)
            subprocess.run([sys.executable, "-m", "stylus", "install", "hook"], cwd=tmp_path, env=env, check=True)
            hook_file = tmp_path / "home" / ".config" / "stylus" / "git-hooks" / "post-commit"
            self.assertTrue(hook_file.exists())
            self.assertIn("BEGIN STYLUS", hook_file.read_text())

            result = subprocess.run(
                [sys.executable, "-m", "stylus", "uninstall", "hook"],
                cwd=tmp_path, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            content = hook_file.read_text()
            self.assertNotIn("BEGIN STYLUS", content)
            self.assertNotIn("stylus analyze", content)

    def test_uninstall_config_unsets_hooks_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = self._uninstall_env(tmp_path)
            subprocess.run([sys.executable, "-m", "stylus", "install", "config"], cwd=tmp_path, env=env, check=True)

            # Verify it was set.
            check = subprocess.run(
                ["git", "config", "--global", "core.hooksPath"],
                env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(check.returncode, 0)

            result = subprocess.run(
                [sys.executable, "-m", "stylus", "uninstall", "config"],
                cwd=tmp_path, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Unset Git core.hooksPath", result.stdout)

            # Verify it was unset.
            check2 = subprocess.run(
                ["git", "config", "--global", "core.hooksPath"],
                env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertNotEqual(check2.returncode, 0)

    def test_uninstall_config_noop_when_not_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = self._uninstall_env(tmp_path)
            result = subprocess.run(
                [sys.executable, "-m", "stylus", "uninstall", "config"],
                cwd=tmp_path, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("nothing to do", result.stdout)

    def test_uninstall_all_removes_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = self._uninstall_env(tmp_path)
            subprocess.run([sys.executable, "-m", "stylus", "install", "skill"], cwd=tmp_path, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "hook"], cwd=tmp_path, env=env, check=True)
            subprocess.run([sys.executable, "-m", "stylus", "install", "config"], cwd=tmp_path, env=env, check=True)

            result = subprocess.run(
                [sys.executable, "-m", "stylus", "uninstall", "all"],
                cwd=tmp_path, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            # Skill removed.
            self.assertFalse((tmp_path / "codex-home" / "skills" / "stylus").exists())
            # Hook block removed.
            hook_file = tmp_path / "home" / ".config" / "stylus" / "git-hooks" / "post-commit"
            self.assertNotIn("BEGIN STYLUS", hook_file.read_text())
            # Config unset.
            check = subprocess.run(
                ["git", "config", "--global", "core.hooksPath"],
                env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertNotEqual(check.returncode, 0)


if __name__ == "__main__":
    unittest.main()
