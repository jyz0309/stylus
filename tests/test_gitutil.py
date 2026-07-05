import subprocess
import tempfile
import unittest
from pathlib import Path

from stylus import gitutil


class GitUtilTests(unittest.TestCase):
    def test_working_tree_diff_includes_untracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)
            (root / "tracked.txt").write_text("tracked\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE)

            (root / "new.txt").write_text("new\n")

            diff = gitutil.working_tree_diff(root)

            self.assertIn("new.txt", diff)
            self.assertIn("+new", diff)

    def test_commit_diff_includes_merge_commit_first_parent_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "config", "user.email", "stylus@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Stylus Test"], cwd=root, check=True)

            (root / "base.txt").write_text("base\n")
            subprocess.run(["git", "add", "base.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, stdout=subprocess.PIPE)

            subprocess.run(["git", "checkout", "-b", "feature"], cwd=root, check=True, stdout=subprocess.PIPE)
            (root / "feature.txt").write_text("feature\n")
            subprocess.run(["git", "add", "feature.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "feature"], cwd=root, check=True, stdout=subprocess.PIPE)

            subprocess.run(["git", "checkout", "main"], cwd=root, check=True, stdout=subprocess.PIPE)
            (root / "main.txt").write_text("main\n")
            subprocess.run(["git", "add", "main.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "main"], cwd=root, check=True, stdout=subprocess.PIPE)
            subprocess.run(["git", "merge", "--no-ff", "feature", "-m", "merge feature"], cwd=root, check=True, stdout=subprocess.PIPE)

            diff = gitutil.commit_diff(root, "HEAD")

            self.assertIn("feature.txt", diff)
            self.assertIn("+feature", diff)


if __name__ == "__main__":
    unittest.main()
