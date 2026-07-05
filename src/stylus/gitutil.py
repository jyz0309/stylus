from __future__ import annotations

import subprocess
from pathlib import Path


def git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def repo_root(cwd: Path) -> Path:
    return Path(git(["rev-parse", "--show-toplevel"], cwd)).resolve()


def repo_id(root: Path) -> str:
    return str(root.resolve())


def current_branch(root: Path) -> str:
    branch = git(["branch", "--show-current"], root)
    return branch or "DETACHED"


def has_commits(root: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


def resolve_commit(root: Path, rev: str) -> str:
    return git(["rev-parse", rev], root)


def commit_diff(root: Path, rev: str) -> str:
    # `git show` suppresses merge diffs by default. For merge commits, compare
    # the merge result against the first parent so hook-triggered analysis still
    # sees the user's integrated changes instead of an empty diff.
    return git(["show", "--format=", "--no-ext-diff", "--first-parent", "-m", rev], root)


def working_tree_diff(root: Path) -> str:
    parts = [git(["diff", "--no-ext-diff"], root)]
    parts.extend(_untracked_file_diffs(root))
    return "\n".join(part for part in parts if part)


def staged_diff(root: Path) -> str:
    return git(["diff", "--cached", "--no-ext-diff"], root)


def _untracked_file_diffs(root: Path) -> list[str]:
    raw = git(["ls-files", "--others", "--exclude-standard", "-z"], root)
    paths = [Path(path) for path in raw.split("\0") if path]
    return [_no_index_diff(root, path) for path in paths]


def _no_index_diff(root: Path, path: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--no-index", "--", "/dev/null", str(path)],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    # `git diff --no-index` returns 1 when differences are found.
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or f"git diff --no-index {path} failed")
    return result.stdout.strip()