from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any

from .paths import state_path


STATE_VERSION = 1
MAX_ANALYSES_PER_BRANCH = 100


@dataclass(frozen=True)
class BaselineChange:
    id: str
    base_revision: str
    diff_path: str
    summary: str
    created_at: str
    task: str = ""


@dataclass(frozen=True)
class AnalysisRecord:
    commit: str
    baseline_change_id: str
    result: str
    created_at: str


class StylusState:
    def __init__(self, data: dict[str, Any]) -> None:
        self.path = state_path()
        self.data = data

    @classmethod
    def load_or_create(cls) -> "StylusState":
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != STATE_VERSION:
                raise ValueError(f"unsupported Stylus state version: {data.get('version')}")
        else:
            data = {"version": STATE_VERSION, "repositories": {}}
            cls(data).save()
        return cls(data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def _branch(self, repo_id: str, branch: str) -> dict[str, Any]:
        repos = self.data.setdefault("repositories", {})
        repo = repos.setdefault(repo_id, {"branches": {}})
        branches = repo.setdefault("branches", {})
        return branches.setdefault(branch, {"last_baseline": None, "analyses": []})

    def set_last_baseline(self, repo_id: str, branch: str, change: BaselineChange) -> None:
        self._branch(repo_id, branch)["last_baseline"] = asdict(change)

    def get_last_baseline(self, repo_id: str, branch: str) -> BaselineChange | None:
        raw = self._branch(repo_id, branch).get("last_baseline")
        if not raw:
            return None
        return BaselineChange(**raw)

    def append_analysis(self, repo_id: str, branch: str, record: AnalysisRecord) -> None:
        analyses = self._branch(repo_id, branch).setdefault("analyses", [])
        analyses.append(asdict(record))
        if len(analyses) > MAX_ANALYSES_PER_BRANCH:
            del analyses[:-MAX_ANALYSES_PER_BRANCH]
