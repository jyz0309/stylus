import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stylus import gitutil
from stylus.ignore import IgnoreFilter, load_ignore


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)


class IgnoreFilterTests(unittest.TestCase):
    def test_empty_patterns_match_nothing(self):
        f = IgnoreFilter([])
        self.assertTrue(f.empty)
        self.assertFalse(f.matches("secret.pem"))
        self.assertFalse(f.matches("src/secret.pem"))

    def test_comment_and_blank_lines_skipped(self):
        f = IgnoreFilter(["# a comment", "", "   ", "# another", r"\.env$"])
        self.assertFalse(f.empty)
        self.assertFalse(f.matches("config.py"))
        self.assertTrue(f.matches(".env"))
        self.assertTrue(f.matches("deploy/.env"))

    def test_matches_by_relative_path(self):
        f = IgnoreFilter([r"^build/"])
        self.assertTrue(f.matches("build/output.o"))
        self.assertFalse(f.matches("src/build_helper.py"))

    def test_matches_by_bare_filename(self):
        # Pattern targets the path form but should also match via filename.
        f = IgnoreFilter([r"secret"])
        self.assertTrue(f.matches("secret.pem"))
        self.assertTrue(f.matches("config/secret.key"))

    def test_invalid_regex_skipped_without_error(self):
        # An unclosed bracket is invalid; it must be skipped, not fatal.
        f = IgnoreFilter([r"[unclosed", r"\.lock$"])
        self.assertFalse(f.matches("[unclosed"))
        self.assertTrue(f.matches("package.lock"))

    def test_pattern_with_leading_whitespace(self):
        f = IgnoreFilter([r"  \.log$"])
        self.assertTrue(f.matches("app.log"))

    def test_gitignore_matched_via_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            _init_repo(root)
            (root / ".gitignore").write_text("*.log\nvendor/\n")

            # No global regex rules; only the repo .gitignore applies.
            f = IgnoreFilter([], git_root=root)
            self.assertFalse(f.empty)
            self.assertTrue(f.matches("app.log"))
            self.assertTrue(f.matches("vendor/lib.py"))
            self.assertFalse(f.matches("src/app.py"))

    def test_gitignore_ignored_when_no_gitignore_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            _init_repo(root)
            # No .gitignore present -> git_root must not be consulted.
            f = IgnoreFilter([], git_root=root)
            self.assertTrue(f.empty)
            self.assertFalse(f.matches("anything.log"))


class LoadIgnoreTests(unittest.TestCase):
    def test_global_regex_and_repo_gitignore_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            _init_repo(root)
            stylus_home = tmp_path / "stylus"
            stylus_home.mkdir()
            (stylus_home / "ignore").write_text(r"\.env$" + "\n")
            (root / ".gitignore").write_text("*.lock\n")

            with patch.dict(os.environ, {"STYLUS_HOME": str(stylus_home)}):
                f = load_ignore(root)

            # Global regex rule.
            self.assertTrue(f.matches(".env"))
            # Repo .gitignore rule (via git).
            self.assertTrue(f.matches("package.lock"))
            # Unaffected file.
            self.assertFalse(f.matches("main.py"))

    def test_no_global_file_and_no_gitignore_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            _init_repo(root)
            with patch.dict(os.environ, {"STYLUS_HOME": str(root / "nohome")}):
                f = load_ignore(root)
            self.assertTrue(f.empty)


class FilterDiffBlocksTests(unittest.TestCase):
    def test_filters_ignored_file_blocks(self):
        diff = (
            "diff --git a/keep.py b/keep.py\n"
            "index 111..222 100644\n--- a/keep.py\n+++ b/keep.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
            "diff --git a/secret.pem b/secret.pem\n"
            "index 333..444 100644\n--- a/secret.pem\n+++ b/secret.pem\n"
            "@@ -1 +1 @@\n-private\n+leaked\n"
        )
        ignore = IgnoreFilter([r"secret"])
        result = gitutil.filter_diff_blocks(diff, lambda p: not ignore.matches(p))
        self.assertIn("keep.py", result)
        self.assertNotIn("secret.pem", result)

    def test_handles_new_file_with_dev_null_header(self):
        diff = (
            "diff --git a/.env b/.env\n"
            "new file mode 100644\nindex 000..111\n--- /dev/null\n+++ b/.env\n"
            "@@ -0,0 +1 @@\n+SECRET=1\n"
            "diff --git a/app.py b/app.py\n"
            "new file mode 100644\nindex 000..222\n--- /dev/null\n+++ b/app.py\n"
            "@@ -0,0 +1 @@\n+print()\n"
        )
        ignore = IgnoreFilter([r"\.env$"])
        result = gitutil.filter_diff_blocks(diff, lambda p: not ignore.matches(p))
        self.assertIn("app.py", result)
        self.assertNotIn(".env", result)
        self.assertNotIn("SECRET=1", result)


class WorkingTreeDiffIgnoreTests(unittest.TestCase):
    def test_untracked_ignored_file_excluded_from_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            _init_repo(root)
            (root / "tracked.txt").write_text("v1\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, stdout=subprocess.PIPE)

            (root / "keep.txt").write_text("keep\n")
            (root / ".env").write_text("SECRET=1\n")
            (root / "secret.pem").write_text("private\n")

            # Place the global ignore file OUTSIDE the repo so it is not itself
            # listed as an untracked file of the repo under test.
            stylus_home = tmp_path / "stylus-home"
            stylus_home.mkdir()
            (stylus_home / "ignore").write_text(r"\.env$" + "\n" + r"secret" + "\n")

            with patch.dict(os.environ, {"STYLUS_HOME": str(stylus_home)}):
                diff = gitutil.working_tree_diff(root)

            self.assertIn("keep.txt", diff)
            self.assertNotIn(".env", diff)
            self.assertNotIn("secret.pem", diff)
            self.assertNotIn("SECRET=1", diff)

    def test_tracked_modified_ignored_file_excluded_from_diff(self):
        """A tracked file matching .gitignore is still filtered (no-index).

        Git exempts tracked files from .gitignore, so without --no-index a
        committed ``config.lock`` would leak into the diff despite ``*.lock``.
        Stylus uses check-ignore --no-index so the user's intent is honored.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            _init_repo(root)
            (root / "app.py").write_text("v1\n")
            (root / "config.lock").write_text("v1\n")
            subprocess.run(["git", "add", "app.py", "config.lock"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, stdout=subprocess.PIPE)

            # .gitignore rule that would normally NOT apply to tracked files.
            (root / ".gitignore").write_text("*.lock\n")

            # Modify both tracked files.
            (root / "app.py").write_text("v2\n")
            (root / "config.lock").write_text("v2\n")

            # No global ignore file; rely solely on .gitignore.
            empty_home = tmp_path / "empty-home"
            empty_home.mkdir()
            with patch.dict(os.environ, {"STYLUS_HOME": str(empty_home)}):
                diff = gitutil.working_tree_diff(root)

            self.assertIn("app.py", diff)
            self.assertNotIn("config.lock", diff)

    def test_repo_gitignore_excludes_untracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            _init_repo(root)
            (root / "tracked.txt").write_text("v1\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, stdout=subprocess.PIPE)

            (root / "keep.txt").write_text("keep\n")
            (root / "debug.log").write_text("log\n")
            (root / ".gitignore").write_text("*.log\n")

            empty_home = tmp_path / "empty-home"
            empty_home.mkdir()
            with patch.dict(os.environ, {"STYLUS_HOME": str(empty_home)}):
                diff = gitutil.working_tree_diff(root)

            self.assertIn("keep.txt", diff)
            self.assertNotIn("debug.log", diff)

    def test_no_ignore_files_keeps_existing_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "repo"
            root.mkdir()
            _init_repo(root)
            (root / "tracked.txt").write_text("v1\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, stdout=subprocess.PIPE)

            (root / "new.txt").write_text("new\n")

            empty_home = tmp_path / "empty-home"
            empty_home.mkdir()
            with patch.dict(os.environ, {"STYLUS_HOME": str(empty_home)}):
                diff = gitutil.working_tree_diff(root)

            self.assertIn("new.txt", diff)
            self.assertIn("+new", diff)


if __name__ == "__main__":
    unittest.main()
