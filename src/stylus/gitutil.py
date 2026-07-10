from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from .ignore import IgnoreFilter, load_ignore


# Matches the ``diff --git a/<path> b/<path>`` header that begins each
# per-file block of a unified diff, capturing the first path verbatim.
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")


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


def _path_of_diff_header(line: str) -> str | None:
    """Return the affected path for a ``diff --git a/.. b/..`` header.

    The old path (``a/<path>``) is returned because it is present for every
    change type (add/modify/delete), and for renames it identifies the file as
    the user would reference it before the edit. Returns ``None`` for lines
    that are not a diff header.
    """
    match = _DIFF_HEADER_RE.match(line)
    if not match:
        return None
    path = match.group(1)
    # ``git diff --no-index`` (used for untracked files) emits a literal
    # ``/dev/null`` old path when the file is brand new; fall back to the new
    # path in that case so callers can still match against the real file.
    if path == "/dev/null":
        return match.group(2)
    return path


def filter_diff_blocks(diff: str, keep: Callable[[str], bool]) -> str:
    """Drop per-file blocks whose path ``keep`` rejects.

    A "block" is the run of lines starting at a ``diff --git`` header up to the
    next header (or end of input). Blocks whose path returns ``False`` from
    ``keep`` are removed; remaining blocks are rejoined in original order.
    Lines before the first header (e.g. leading blank lines) are preserved.
    """
    kept: list[str] = []
    current: list[str] = []
    current_path: str | None = None
    in_block = False

    def flush_block() -> None:
        nonlocal current, current_path, in_block
        if in_block and current_path is not None and keep(current_path):
            kept.extend(current)
        current = []
        current_path = None
        in_block = False

    for line in diff.splitlines():
        path = _path_of_diff_header(line)
        if path is not None:
            flush_block()
            current = [line]
            current_path = path
            in_block = True
        elif in_block:
            current.append(line)
        else:
            kept.append(line)
    flush_block()
    return "\n".join(line for line in kept if line != "").strip()


def working_tree_diff(root: Path) -> str:
    ignore = load_ignore(root)
    keep = lambda path: not ignore.matches(path)
    parts: list[str] = []
    tracked = git(["diff", "--no-ext-diff"], root)
    if tracked:
        filtered = filter_diff_blocks(tracked, keep)
        if filtered:
            parts.append(filtered)
    parts.extend(_untracked_file_diffs(root, ignore))
    return "\n".join(part for part in parts if part)


def staged_diff(root: Path) -> str:
    return git(["diff", "--cached", "--no-ext-diff"], root)


def _untracked_file_diffs(root: Path, ignore: IgnoreFilter | None = None) -> list[str]:
    if ignore is None:
        ignore = load_ignore(root)
    raw = git(["ls-files", "--others", "--exclude-standard", "-z"], root)
    paths = [Path(path) for path in raw.split("\0") if path and not ignore.matches(path)]
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