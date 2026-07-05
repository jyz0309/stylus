from __future__ import annotations

import hashlib
import os
from pathlib import Path


def stylus_home() -> Path:
    """Root data directory for Stylus state and per-repo diffs.

    Defaults to `~/.stylus`. Override with the `STYLUS_HOME` environment
    variable (useful for tests and multi-machine setups).
    """
    override = os.environ.get("STYLUS_HOME", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".stylus"


def state_path() -> Path:
    """Path to the global `state.json` (shared across all repositories)."""
    return stylus_home() / "state.json"


def evidence_path() -> Path:
    """Path to the global `evidence.md` audit log.

    Evidence is internal Stylus bookkeeping (which commit produced which
    preference) and is not needed by the AI agents that read the skill, so it
    lives under the Stylus home rather than inside the skill directory.
    """
    return stylus_home() / "evidence.md"


def repo_hash(repo_id: str) -> str:
    """Stable short hash for a repository path, used as a directory name."""
    return hashlib.sha256(repo_id.encode()).hexdigest()[:16]


def repo_dir(repo_id: str) -> Path:
    """Per-repository data directory under the Stylus home."""
    return stylus_home() / "repositories" / repo_hash(repo_id)


def diffs_dir(repo_id: str) -> Path:
    """Per-repository directory holding recorded agent baseline diffs."""
    return repo_dir(repo_id) / "diffs"
