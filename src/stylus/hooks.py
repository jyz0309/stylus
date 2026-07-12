from __future__ import annotations

import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path


BEGIN = "# BEGIN STYLUS POST-COMMIT"
END = "# END STYLUS POST-COMMIT"


def install_post_commit_hook(repo_root: Path) -> Path:
    hooks_dir = repo_root / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "post-commit"
    return _install_post_commit_hook_file(hook)


def install_global_post_commit_hook(home: Path | None = None) -> Path:
    hook = global_hooks_dir(home) / "post-commit"
    return _install_post_commit_hook_file(hook)


def uninstall_global_post_commit_hook(home: Path | None = None) -> Path:
    """Remove the Stylus post-commit block from the global hook file.

    Returns the path of the hook file that was cleaned up. If the file does not
    exist or contains no Stylus block, this is a no-op.
    """
    hook = global_hooks_dir(home) / "post-commit"
    if not hook.exists():
        return hook
    existing = hook.read_text(encoding="utf-8")
    if BEGIN not in existing or END not in existing:
        return hook
    before = existing[: existing.index(BEGIN)]
    after = existing[existing.index(END) + len(END):]
    content = (before.rstrip() + "\n" + after.lstrip("\n")).strip()
    if content:
        if not content.startswith("#!"):
            content = "#!/bin/sh\n" + content
        hook.write_text(content + "\n", encoding="utf-8")
    else:
        hook.write_text("#!/bin/sh\n", encoding="utf-8")
    return hook


def install_global_git_config(hooks_path: Path, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        ["git", "config", "--global", "core.hooksPath", str(hooks_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "failed to configure global Git hooks path")


def uninstall_global_git_config(hooks_path: Path, env: dict[str, str] | None = None) -> bool:
    """Unset Git's global `core.hooksPath` if it points at the Stylus hooks dir.

    Returns True if the config was removed, False if it was already unset or
    pointed elsewhere (so we never clobber a user's unrelated hooks config).
    """
    result = subprocess.run(
        ["git", "config", "--global", "core.hooksPath"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        return False
    current = result.stdout.strip()
    if current != str(hooks_path):
        return False
    subprocess.run(
        ["git", "config", "--global", "--unset", "core.hooksPath"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    return True


def global_hooks_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config" / "stylus" / "git-hooks"


def _install_post_commit_hook_file(hook: Path) -> Path:
    hook.parent.mkdir(parents=True, exist_ok=True)
    existing = hook.read_text(encoding="utf-8") if hook.exists() else "#!/bin/sh\n"
    if not existing.startswith("#!"):
        existing = "#!/bin/sh\n" + existing

    pythonpath = os.environ.get("PYTHONPATH", "")
    export_line = (
        f"PYTHONPATH={shlex.quote(pythonpath)}; export PYTHONPATH\n" if pythonpath else ""
    )
    python_executable = shlex.quote(sys.executable)
    block = f"""{BEGIN}
{export_line}if command -v stylus >/dev/null 2>&1; then
  stylus analyze --commit HEAD --background || echo "stylus: failed to start background analysis; commit preserved" >&2
else
  {python_executable} -m stylus analyze --commit HEAD --background || echo "stylus: failed to start background analysis; commit preserved" >&2
fi
{END}
"""

    if BEGIN in existing and END in existing:
        before = existing[: existing.index(BEGIN)]
        after = existing[existing.index(END) + len(END) :]
        content = before.rstrip() + "\n\n" + block + after.lstrip("\n")
    else:
        content = existing.rstrip() + "\n\n" + block

    hook.write_text(content, encoding="utf-8")
    mode = hook.stat().st_mode
    hook.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook
