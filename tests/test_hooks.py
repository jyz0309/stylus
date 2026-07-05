import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stylus.hooks import global_hooks_dir, install_global_git_config, install_global_post_commit_hook, install_post_commit_hook


class HookTests(unittest.TestCase):
    def test_installs_post_commit_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_dir = root / ".git"
            git_dir.mkdir()

            install_post_commit_hook(root)

            hook = git_dir / "hooks" / "post-commit"
            self.assertTrue(hook.exists())
            self.assertIn("stylus analyze --commit HEAD", hook.read_text())
            self.assertTrue(os.access(hook, os.X_OK))

    def test_preserves_existing_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hooks = root / ".git" / "hooks"
            hooks.mkdir(parents=True)
            hook = hooks / "post-commit"
            hook.write_text("#!/bin/sh\necho existing\n")

            install_post_commit_hook(root)

            text = hook.read_text()
            self.assertIn("echo existing", text)
            self.assertEqual(text.count("BEGIN STYLUS POST-COMMIT"), 1)
            install_post_commit_hook(root)
            self.assertEqual(hook.read_text().count("BEGIN STYLUS POST-COMMIT"), 1)

    def test_installs_global_post_commit_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            hook = install_global_post_commit_hook(home=home)

            self.assertEqual(hook, home / ".config" / "stylus" / "git-hooks" / "post-commit")
            self.assertIn("stylus analyze --commit HEAD", hook.read_text())
            self.assertTrue(os.access(hook, os.X_OK))

    def test_installs_global_git_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "gitconfig"
            hooks = Path(tmp) / "hooks"

            install_global_git_config(hooks, env={**os.environ, "GIT_CONFIG_GLOBAL": str(config)})

            result = subprocess.run(
                ["git", "config", "--global", "--get", "core.hooksPath"],
                env={**os.environ, "GIT_CONFIG_GLOBAL": str(config)},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(hooks))

    def test_global_hooks_dir_uses_home_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(global_hooks_dir(Path(tmp)), Path(tmp) / ".config" / "stylus" / "git-hooks")


if __name__ == "__main__":
    unittest.main()
