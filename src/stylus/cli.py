from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from collections.abc import Sequence
from pathlib import Path

from . import gitutil
from .analyzer import (
    AnalyzerInput,
    AnalyzerOutput,
    AnalyzerProvider,
    PreferenceUpdate,
    provider_from_env,
    provider_name,
)
from .hooks import (
    global_hooks_dir,
    install_global_git_config,
    install_global_post_commit_hook,
    uninstall_global_git_config,
    uninstall_global_post_commit_hook,
)
from .paths import diffs_dir as diffs_dir_for
from .paths import evidence_path
from .skill import TARGETS, SkillStore
from .state import AnalysisRecord, BaselineChange, StylusState


DEFAULT_MAX_DIFF_BYTES = 200_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stylus")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="prepare the current Git repository for Stylus")

    install = subparsers.add_parser("install", help="install Stylus integration pieces")
    install_subparsers = install.add_subparsers(dest="install_target", required=True)
    install_skill_parser = install_subparsers.add_parser(
        "skill", help="install or update the Stylus skill for codex, cursor, zcode, and claude"
    )
    install_skill_parser.add_argument(
        "--target",
        action="append",
        choices=list(TARGETS),
        default=None,
        help="install only the named target (codex, cursor, zcode, claude); may be repeated. "
        "Default: install all four.",
    )
    install_subparsers.add_parser("hook", help="install the global Stylus Git post-commit hook")
    install_subparsers.add_parser("config", help="configure Git to use the global Stylus hook path")

    uninstall = subparsers.add_parser("uninstall", help="remove Stylus integration pieces")
    uninstall_subparsers = uninstall.add_subparsers(dest="uninstall_target", required=True)
    uninstall_skill_parser = uninstall_subparsers.add_parser(
        "skill", help="remove the Stylus skill from codex, cursor, zcode, and/or claude"
    )
    uninstall_skill_parser.add_argument(
        "--target",
        action="append",
        choices=list(TARGETS),
        default=None,
        help="remove only the named target (codex, cursor, zcode, claude); may be repeated. "
        "Default: remove all four.",
    )
    uninstall_subparsers.add_parser("hook", help="remove the Stylus block from the global Git post-commit hook")
    uninstall_subparsers.add_parser("config", help="unset Git's global core.hooksPath if it points at Stylus")
    uninstall_all_parser = uninstall_subparsers.add_parser("all", help="remove skill, hook, and config in one step")
    uninstall_all_parser.add_argument(
        "--target",
        action="append",
        choices=list(TARGETS),
        default=None,
        help="remove the skill only for the named target(s); may be repeated. "
        "Default: remove all four. Hook and config are always removed.",
    )

    analyze = subparsers.add_parser("analyze", help="analyze a committed revision")
    analyze.add_argument("--commit", default="HEAD", help="commit revision to analyze")
    analyze.add_argument(
        "--debug",
        action="store_true",
        help="print the analyzer provider, its input summary, and the parsed output "
        "for diagnostics. Does not change what gets written to preferences.",
    )

    record = subparsers.add_parser("record", help="record the latest agent-produced diff as a baseline")
    record.add_argument("--summary", default="", help="short description of the agent change")
    record.add_argument("--task", default="", help="optional agent task description")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cwd = Path.cwd()

    if args.command == "init":
        return run_init(cwd)

    if args.command == "install":
        if args.install_target == "skill":
            return run_install_skill(args.target)
        if args.install_target == "hook":
            return run_install_global_hook()
        if args.install_target == "config":
            return run_install_config()

    if args.command == "uninstall":
        if args.uninstall_target == "skill":
            return run_uninstall_skill(args.target)
        if args.uninstall_target == "hook":
            return run_uninstall_hook()
        if args.uninstall_target == "config":
            return run_uninstall_config()
        if args.uninstall_target == "all":
            return run_uninstall_all(args.target)

    if args.command == "analyze":
        return run_analyze(cwd, args.commit, args.debug)

    if args.command == "record":
        return run_record(cwd, args.summary, args.task)

    return 0


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_init(cwd: Path) -> int:
    root = gitutil.repo_root(cwd)
    StylusState.load_or_create()
    SkillStore().ensure()
    print(f"Initialized Stylus in {root}")
    return 0


def run_install_skill(targets: Sequence[str] | None = None) -> int:
    """Install the Stylus skill to every requested target.

    `codex` is the single source of truth: the analyzer only updates its
    references, so we always ensure/sync the codex store first and then copy its
    `stylus/` directory onto each other target. After installing, print the path
    of every installed target so users can see where the skill landed.
    """
    requested = list(targets) if targets else list(TARGETS)

    # codex is always prepared (and is the sync source), even when the caller
    # only asked for cursor/zcode, so those targets reflect the latest learned
    # preferences instead of an empty skeleton.
    codex_store = SkillStore(target="codex")
    codex_store.ensure()

    installed: list[tuple[str, SkillStore]] = []
    for target in requested:
        store = SkillStore(target=target)
        if target == "codex":
            installed.append((target, codex_store))
            continue
        codex_store.sync_to(store)
        installed.append((target, store))

    print("Installed Stylus skill at:")
    for target, store in installed:
        print(f"  {target}: {store.skill_dir}")
    return 0


def run_install_global_hook() -> int:
    hook = install_global_post_commit_hook()
    print(f"Installed global Stylus hook at {hook}")
    return 0


def run_install_config() -> int:
    hooks_path = global_hooks_dir()
    install_global_git_config(hooks_path)
    print(f"Configured Git core.hooksPath to {hooks_path}")
    return 0


def run_uninstall_skill(targets: Sequence[str] | None = None) -> int:
    """Remove the Stylus skill directory from each requested target."""
    requested = list(targets) if targets else list(TARGETS)
    for target in requested:
        store = SkillStore(target=target)
        if store.skill_dir.exists():
            shutil.rmtree(store.skill_dir)
            print(f"Removed Stylus skill from {target}: {store.skill_dir}")
        else:
            print(f"No Stylus skill found for {target} at {store.skill_dir} (skipped)")
    return 0


def run_uninstall_hook() -> int:
    hook = uninstall_global_post_commit_hook()
    print(f"Removed Stylus block from global hook at {hook}")
    return 0


def run_uninstall_config() -> int:
    hooks_path = global_hooks_dir()
    removed = uninstall_global_git_config(hooks_path)
    if removed:
        print(f"Unset Git core.hooksPath (was {hooks_path})")
    else:
        print(f"Git core.hooksPath was not set to {hooks_path} (nothing to do)")
    return 0


def run_uninstall_all(targets: Sequence[str] | None = None) -> int:
    run_uninstall_skill(targets)
    run_uninstall_hook()
    run_uninstall_config()
    return 0


def run_analyze(cwd: Path, commit: str, debug: bool = False) -> int:
    root = gitutil.repo_root(cwd)
    repo = gitutil.repo_id(root)
    branch = gitutil.current_branch(root)
    state = StylusState.load_or_create()
    resolved_commit = gitutil.resolve_commit(root, commit)
    baseline = state.get_last_baseline(repo, branch)

    if baseline is None:
        state.append_analysis(repo, branch, AnalysisRecord(
            commit=resolved_commit,
            baseline_change_id="",
            result="skipped",
            created_at=utc_now(),
        ))
        state.save()
        print("Stylus skipped analysis: no agent baseline recorded for this branch.")
        return 0

    baseline_diff_path = Path(baseline.diff_path)
    raw_baseline_diff = baseline_diff_path.read_text(encoding="utf-8") if baseline_diff_path.exists() else ""
    raw_user_diff = gitutil.commit_diff(root, resolved_commit)

    # Apply ignore patterns to both sides so the comparison is consistent with
    # the recorded baseline (which was already filtered at record time). Reading
    # the baseline back and re-filtering keeps things correct even if the ignore
    # rules changed after the baseline was captured.
    ignore = gitutil.load_ignore(root)
    if not ignore.empty:
        keep = lambda path: not ignore.matches(path)
        raw_baseline_diff = gitutil.filter_diff_blocks(raw_baseline_diff, keep)
        raw_user_diff = gitutil.filter_diff_blocks(raw_user_diff, keep)

    if diffs_equivalent(raw_baseline_diff, raw_user_diff):
        state.append_analysis(repo, branch, AnalysisRecord(
            commit=resolved_commit,
            baseline_change_id=baseline.id,
            result="skipped",
            created_at=utc_now(),
        ))
        state.save()
        print("Stylus skipped analysis: user commit matches the agent baseline.")
        return 0

    baseline_diff = truncate_diff(raw_baseline_diff, "baseline_diff")
    user_diff = truncate_diff(raw_user_diff, "user_diff")
    prefs_path = SkillStore().references_dir / "preferences.md"
    current_preferences = prefs_path.read_text(encoding="utf-8") if prefs_path.exists() else ""

    provider = provider_from_env()
    analyzer_input = AnalyzerInput(
        repo_id=repo,
        branch=branch,
        commit=resolved_commit,
        baseline_change_id=baseline.id,
        baseline_diff=baseline_diff,
        user_diff=user_diff,
        current_preferences=current_preferences,
    )

    if debug:
        _print_debug_header(provider, analyzer_input, raw_baseline_diff, raw_user_diff)

    try:
        output = provider.analyze(analyzer_input)
        if debug:
            _print_debug_output(output)
        SkillStore().merge_preferences(output.preferences, output.obsolete_preferences)
        _append_evidence(output.preferences)
        result = "updated"
    except RuntimeError as exc:
        print(f"Stylus analysis failed: {exc}", file=sys.stderr)
        state.append_analysis(repo, branch, AnalysisRecord(
            commit=resolved_commit,
            baseline_change_id=baseline.id,
            result="failed",
            created_at=utc_now(),
        ))
        state.save()
        return 1

    state.append_analysis(repo, branch, AnalysisRecord(
        commit=resolved_commit,
        baseline_change_id=baseline.id,
        result=result,
        created_at=utc_now(),
    ))
    state.save()
    print(f"Stylus updated preferences from commit {resolved_commit}.")
    return 0


EVIDENCE_HEADER = "# Stylus Evidence\n\n"


def _append_evidence(updates: list[PreferenceUpdate]) -> None:
    """Append evidence lines for each learned preference to the global log.

    Evidence is internal bookkeeping (which commit produced which preference)
    and lives under the Stylus home, not inside the skill directory, so it is
    not synced to cursor/zcode and does not clutter the skill the agents read.
    """
    if not updates:
        return
    path = evidence_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(EVIDENCE_HEADER, encoding="utf-8")
    evidence = path.read_text(encoding="utf-8")
    for update in updates:
        evidence_key = f"`{update.source_commit}` [{update.topic}] {update.evidence} => {update.instruction}"
        if evidence_key not in evidence:
            evidence += (
                f"- `{update.source_commit}` [{update.topic}] {update.evidence} "
                f"=> {update.instruction}\n"
            )
    path.write_text(evidence, encoding="utf-8")


def _print_debug_header(
    provider: AnalyzerProvider,
    analyzer_input: AnalyzerInput,
    raw_baseline_diff: str,
    raw_user_diff: str,
) -> None:
    print("=== Stylus analyze (debug) ===", file=sys.stderr)
    print(f"provider: {provider_name(provider)}", file=sys.stderr)
    print(f"repo_id: {analyzer_input.repo_id}", file=sys.stderr)
    print(f"branch: {analyzer_input.branch}", file=sys.stderr)
    print(f"commit: {analyzer_input.commit}", file=sys.stderr)
    print(f"baseline_change_id: {analyzer_input.baseline_change_id}", file=sys.stderr)
    print(
        f"baseline_diff: {len(raw_baseline_diff)} bytes raw, "
        f"{len(analyzer_input.baseline_diff)} bytes sent"
        + (" (truncated)" if len(analyzer_input.baseline_diff) < len(raw_baseline_diff) else ""),
        file=sys.stderr,
    )
    print(
        f"user_diff: {len(raw_user_diff)} bytes raw, "
        f"{len(analyzer_input.user_diff)} bytes sent"
        + (" (truncated)" if len(analyzer_input.user_diff) < len(raw_user_diff) else ""),
        file=sys.stderr,
    )
    print(f"current_preferences: {len(analyzer_input.current_preferences)} bytes", file=sys.stderr)


def _print_debug_output(output: AnalyzerOutput) -> None:
    print("=== analyzer output ===", file=sys.stderr)
    print(json.dumps(asdict(output), ensure_ascii=False, indent=2), file=sys.stderr)
    print("=== end analyzer output ===", file=sys.stderr)


def max_diff_bytes() -> int:
    raw = os.environ.get("STYLUS_MAX_DIFF_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAX_DIFF_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_DIFF_BYTES
    return max(value, 0)


def truncate_diff(diff: str, label: str) -> str:
    limit = max_diff_bytes()
    raw = diff.encode("utf-8")
    if limit == 0 or len(raw) <= limit:
        return diff
    truncated = raw[:limit].decode("utf-8", errors="ignore")
    return (
        truncated
        + f"\n\n[Stylus truncated {label}: original {len(raw)} bytes, "
        + f"kept {limit} bytes. Set STYLUS_MAX_DIFF_BYTES to adjust.]\n"
    )


def diffs_equivalent(a: str, b: str) -> bool:
    """Return True when two diffs describe the same set of file changes.

    `git show` lists files in tree order while the recorded agent baseline
    groups tracked changes before untracked files, so identical change sets can
    appear in a different order. Splitting each diff into per-file blocks and
    comparing the sorted blocks makes the check order-independent, and avoids a
    needless analyzer run when the user commits the agent baseline verbatim.
    """
    return _diff_file_blocks(a) == _diff_file_blocks(b)


def _diff_file_blocks(diff: str) -> list[str]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    return sorted("\n".join(block).strip() for block in blocks if block)


def run_record(cwd: Path, summary: str, task: str) -> int:
    root = gitutil.repo_root(cwd)
    repo = gitutil.repo_id(root)
    branch = gitutil.current_branch(root)
    base_revision = gitutil.resolve_commit(root, "HEAD") if gitutil.has_commits(root) else ""
    diff = gitutil.working_tree_diff(root)
    if not diff.strip():
        staged = gitutil.staged_diff(root)
        if staged.strip():
            print(
                "Stylus: refusing to record — changes are already staged. "
                "The agent should leave edits unstaged so Stylus can capture them.",
                file=sys.stderr,
            )
            return 1
        print("Stylus: no working-tree changes to record.", file=sys.stderr)
        return 1
    created_at = utc_now()
    digest = hashlib.sha256(f"{repo}\0{branch}\0{base_revision}\0{diff}\0{created_at}".encode()).hexdigest()[:16]
    target_diffs_dir = diffs_dir_for(repo)
    target_diffs_dir.mkdir(parents=True, exist_ok=True)
    diff_path = target_diffs_dir / f"{digest}.diff"
    diff_path.write_text(diff, encoding="utf-8")

    state = StylusState.load_or_create()
    state.set_last_baseline(repo, branch, BaselineChange(
        id=digest,
        base_revision=base_revision,
        diff_path=str(diff_path),
        summary=summary,
        created_at=created_at,
        task=task,
    ))
    state.save()
    print(f"Recorded agent baseline {digest} for {branch}.")
    return 0
