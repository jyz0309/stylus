from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .paths import stylus_home

# Global ignore file under the Stylus home (overridable via STYLUS_HOME).
GLOBAL_IGNORE_FILENAME = "ignore"


class IgnoreFilter:
    """Combined ignore matcher: global regex rules + project ``.gitignore``.

    Two independent sources of ignore rules are merged:

    * **Global** (``~/.stylus/ignore``): each non-empty, non-comment line is a
      Python regular expression matched with ``re.search``. This covers
      cross-repository concerns (e.g. ``\\.env$``, ``\\.pem$``).
    * **Project** (the repository's own ``.gitignore``): semantics are owned by
      Git itself, so matching is delegated to ``git check-ignore --no-index``
      rather than reimplemented. This reuses the user's existing ignore rules
      with no extra configuration and stays correct for every gitignore quirk
      (negation, anchored paths, directory patterns, nested files, …).

    A path is ignored when either source matches.
    """

    def __init__(
        self,
        regex_patterns: list[str],
        git_root: Path | None = None,
    ) -> None:
        self._compiled: list[re.Pattern[str]] = []
        for raw in regex_patterns:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                self._compiled.append(re.compile(line))
            except re.error as exc:
                print(
                    f"Stylus: skipping invalid ignore pattern {line!r}: {exc}",
                    file=sys.stderr,
                )
        # Only consult Git when the repo actually has a .gitignore; otherwise
        # check-ignore could never match and we save a subprocess per path.
        self._git_root = git_root if git_root is not None and (git_root / ".gitignore").is_file() else None

    @property
    def empty(self) -> bool:
        """True when no source of ignore rules can match anything."""
        return not self._compiled and self._git_root is None

    def matches(self, path: str) -> bool:
        """Return True when ``path`` should be ignored.

        ``path`` is a path relative to the repository root in POSIX form. For
        the global regex rules the bare file name is also tested so users can
        ignore by either the full path or just the name.
        """
        if self._compiled:
            name = path.rsplit("/", 1)[-1]
            if any(rx.search(path) or rx.search(name) for rx in self._compiled):
                return True
        if self._git_root is not None and _git_check_ignore(self._git_root, path):
            return True
        return False


def _git_check_ignore(root: Path, path: str) -> bool:
    """Return True when Git's ``.gitignore`` rules ignore ``path``.

    Uses ``git check-ignore --no-index`` so that already-tracked files are still
    evaluated against ``.gitignore`` (Git otherwise exempts tracked files from
    ignore rules, which would let e.g. a committed ``app.log`` leak into the
    Stylus diff despite a ``*.log`` pattern). Exit code 0 means ignored; 1 means
    not ignored; any other code is treated as "not ignored" so a missing
    ``.gitignore`` or an unusual setup never blocks analysis.
    """
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--", path],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


def load_ignore(root: Path) -> IgnoreFilter:
    """Build an ``IgnoreFilter`` from the global file plus the repo's gitignore.

    Global rules (``~/.stylus/ignore``) are always loaded when present. The
    repository's ``.gitignore`` is evaluated by Git at match time, so we only
    need to remember the repo root for it.
    """
    patterns: list[str] = []

    global_path = stylus_home() / GLOBAL_IGNORE_FILENAME
    if global_path.is_file():
        patterns.extend(global_path.read_text(encoding="utf-8").splitlines())

    return IgnoreFilter(patterns, git_root=root)
