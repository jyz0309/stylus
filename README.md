# Stylus

[English](README.md) | [简体中文](README_cn.md)

> Learn your coding style from Git commits and share it across agents.

Stylus watches how you revise, narrow, or correct the code that an agent
produced. After each commit it compares your changes with the latest recorded
agent change on the same branch, extracts coding-style preferences, and writes
them into a local **skill** that agents read.

The result: agents you use — **Codex**, **Cursor**, **ZCode**, **Claude** — gradually write
code that looks more like yours *before* you have to correct it.

---

## Table of Contents

- [How it works](#how-it-works)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Configuration](#configuration)
- [Analyzer](#analyzer)
- [Uninstall](#uninstall)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## How it works

```
  Agent edits ──▶ stylus record (capture baseline diff)
                        │
                        ▼
              you review & revise the working tree
                        │
                        ▼
                git commit (your correction)
                        │
                        ▼
        post-commit hook ──▶ stylus analyze
                        │
                        ▼
   compare your commit vs. recorded agent change
                        │
                        ▼
      update ~/.stylus skill preferences (shared)
                        │
                        ▼
   next agent session reads the skill and adapts
```

1. After an agent finishes its changes, Stylus automatically runs
   `stylus record`. Stylus captures the working-tree diff as the **baseline**
   for that branch.
2. You review, revise, and `git commit`. A non-blocking `post-commit` hook
   triggers `stylus analyze --commit HEAD`.
3. Stylus compares your commit against the recorded baseline, asks the
   configured analyzer to extract reusable preferences, and merges them into the
   stylus skill under the installed agents.

If your commit reproduces the agent baseline verbatim, Stylus skips analysis
(nothing to learn) and records a `skipped` result.

## Features

- **Learns from corrections, not from commands** — no manual rule authoring.
- **One skill, four agents** — a single source of truth synced to Codex,
  Cursor, ZCode, and Claude.
- **OpenAI-compatible** — supports the official OpenAI Responses API *and* any
  OpenAI-compatible Chat Completions endpoint (DeepSeek, OpenRouter, …).
- **Custom analyzer** — swap in any program via `STYLUS_ANALYZER_CMD`.
- **Non-invasive** — state lives under `~/.stylus`, never inside your repo, so
  it can never be accidentally committed. The hook is non-blocking and never
  discards a commit.
- **Fully uninstallable** — `stylus uninstall all` cleanly removes the skill,
  hook, and Git config.

## Requirements

- Python **3.11+**
- Git (available on `PATH`)
- Optional: an `openai`-compatible API key for LLM-based analysis

## Installation

Stylus is a pure-Python package with a single runtime dependency
(`openai>=1.40`).

```bash
# from source
git clone https://github.com/jyz0309/stylus.git
cd stylus
pip install .
```

Verify it is on `PATH`:

```bash
stylus --help
```

## Quick start

Set up Stylus once per machine:

```bash
stylus install skill        # install the skill for codex, cursor, zcode, and claude
stylus install hook         # install the global post-commit hook
stylus install config       # point Git's core.hooksPath at the hook
```

Then use it in any repository:

```bash
# 1. An agent finishes editing your working tree (leave changes unstaged).
stylus record --summary "agent changed the helper"

# 2. You review, revise, and commit your correction.
git commit -m "correction"
#    ^ the post-commit hook runs `stylus analyze --commit HEAD` automatically
```

That's it. The next agent session in that repo will read the updated
preferences.

## Commands

```
stylus <command> [options]

Commands:
  init                 Prepare the current Git repository for Stylus
  install skill        Install/update the skill for codex, cursor, zcode, and claude
  install hook         Install the global Stylus Git post-commit hook
  install config       Configure Git to use the global Stylus hook path
  uninstall skill      Remove the skill from codex, cursor, zcode, and/or claude
  uninstall hook       Remove the Stylus block from the global post-commit hook
  uninstall config     Unset Git's core.hooksPath if it points at Stylus
  uninstall all        Remove skill + hook + config in one step
  record               Record the latest agent-produced diff as a baseline
  analyze              Analyze a committed revision against the baseline
```

### `install skill`

Creates or updates the Stylus skill for **codex, cursor, zcode, and claude** at once.
Codex is the single source of truth (the analyzer only updates it); the others
are synced from it on every install.

```
$ stylus install skill
Installed Stylus skill at:
  codex:  /home/you/.codex/skills/stylus
  cursor: /home/you/.cursor/skills/stylus
  zcode:  /home/you/.agents/skills/stylus
  claude: /home/you/.claude/skills/stylus
```

Default target directories:

| target | default path              | override env var                |
| ------ | ------------------------- | ------------------------------- |
| codex  | `~/.codex/skills`         | `CODEX_HOME`                    |
| cursor | `~/.cursor/skills`        | `STYLUS_CURSOR_SKILLS_ROOT`     |
| zcode  | `~/.agents/skills`        | `STYLUS_ZCODE_SKILLS_ROOT`      |
| claude | `~/.claude/skills`        | `STYLUS_CLAUDE_SKILLS_ROOT`     |

Install only specific targets:

```bash
stylus install skill --target cursor --target zcode
```

### `install hook` / `install config`

`install hook` writes the global hook file at
`~/.config/stylus/git-hooks/post-commit`. `install config` sets Git's global
`core.hooksPath` to that directory. The hook is **non-blocking**: analysis
failures print a warning and preserve the commit.

### `record`

Captures the current working-tree diff as the agent baseline for the current
repository + branch. The agent must leave its edits **unstaged** — Stylus
refuses to record if changes are already staged, so the baseline reflects what
the agent actually produced rather than a partial commit.

```bash
stylus record --summary "agent changed the helper"
stylus record --summary "refactor api client" --task "migrate to v2 sdk"
```

### `analyze`

Compares a commit against the latest recorded baseline and updates
preferences. Normally invoked by the hook, but you can run it manually:

```bash
stylus analyze --commit HEAD
stylus analyze --commit HEAD --debug   # show provider, input sizes, and output
```

## Configuration

Stylus is configurable entirely through environment variables — no config file
required.

| variable                    | purpose                                                       | default                          |
| --------------------------- | ------------------------------------------------------------ | -------------------------------- |
| `STYLUS_HOME`               | Root directory for state, diffs, and the evidence log        | `~/.stylus`                      |
| `OPENAI_API_KEY`            | Enable the OpenAI LLM analyzer                               | *(unset → local analyzer)*       |
| `STYLUS_OPENAI_MODEL`       | Model name for the OpenAI analyzer                           | `gpt-5.2`                        |
| `STYLUS_OPENAI_BASE_URL`    | OpenAI-compatible base URL                                   | `https://api.openai.com/v1`      |
| `STYLUS_ANALYZER_CMD`       | External analyzer command (highest priority)                 | *(unset)*                        |
| `STYLUS_MAX_DIFF_BYTES`     | Per-diff byte limit sent to the analyzer                     | `200000`                         |
| `CODEX_HOME`                | Override the codex skills root                               | `~/.codex`                       |
| `STYLUS_CURSOR_SKILLS_ROOT` | Override the cursor skills root                              | `~/.cursor/skills`               |
| `STYLUS_ZCODE_SKILLS_ROOT`  | Override the zcode skills root                               | `~/.agents/skills`               |
| `STYLUS_CLAUDE_SKILLS_ROOT` | Override the claude skills root                              | `~/.claude/skills`               |

## Analyzer

Stylus picks an analyzer automatically based on the environment. Run
`stylus analyze --debug` to see which one was selected.

### 1. Local analyzer (default)

When no `OPENAI_API_KEY` and no `STYLUS_ANALYZER_CMD` are set, Stylus uses a
deterministic local analyzer. The learning loop runs end-to-end with **no
network access** — useful for trying Stylus out or running in air-gapped
environments.

### 2. OpenAI analyzer

Set `OPENAI_API_KEY` to enable LLM-based analysis:

```bash
export OPENAI_API_KEY="sk-..."
export STYLUS_OPENAI_MODEL="gpt-5.2"          # optional
export STYLUS_OPENAI_BASE_URL="https://api.openai.com/v1"  # optional
```

Stylus selects the API endpoint automatically:

- **OpenAI official** (`api.openai.com`) → Responses API (`/responses`) with
  Structured Outputs (`json_schema` strict mode).
- **Any other provider** (DeepSeek, OpenRouter, …) → Chat Completions API
  (`/chat/completions`) with `response_format={"type":"json_object"}`. The full
  JSON schema and an example are embedded in the system prompt, so this works
  with endpoints that only support `json_object` mode (e.g. DeepSeek) and does
  not require Structured Outputs.

### 3. Custom analyzer command

For a fully custom analyzer, set `STYLUS_ANALYZER_CMD`. This takes priority
over `OPENAI_API_KEY`:

```bash
export STYLUS_ANALYZER_CMD='python3 /path/to/custom-stylus-analyzer.py'
```

Stylus sends one JSON object on **stdin**:

```json
{
  "repo_id": "/path/to/repo",
  "branch": "main",
  "commit": "git-sha",
  "baseline_change_id": "baseline-id",
  "baseline_diff": "...",
  "user_diff": "...",
  "current_preferences": "..."
}
```

The command must print analyzer JSON on **stdout**. The built-in OpenAI
analyzer uses this same schema:

```json
{
  "preferences": [
    {
      "topic": "change scope",
      "instruction": "Prefer small, localized changes when correcting agent output.",
      "confidence": "medium",
      "evidence": "User commit narrowed the previous agent diff.",
      "source_commit": "git-sha"
    }
  ],
  "obsolete_preferences": [],
  "notes": []
}
```

### Using the schema from Python

The SDK-facing response constraint is exported from `stylus.analyzer`:

```python
from openai import OpenAI
from stylus.analyzer import ANALYZER_RESPONSE_TEXT_FORMAT

client = OpenAI()
response = client.responses.create(
    model="gpt-5.2",
    input="...",
    text={"format": ANALYZER_RESPONSE_TEXT_FORMAT},
)
```

For Chat Completions callers, use `ANALYZER_CHAT_RESPONSE_FORMAT` as
`response_format`.

## Uninstall

Remove Stylus integration pieces individually or all at once:

```bash
stylus uninstall skill                  # remove skill from codex, cursor, zcode, claude
stylus uninstall skill --target cursor  # remove only one target
stylus uninstall hook                   # remove Stylus block from global post-commit hook
stylus uninstall config                 # unset Git core.hooksPath if it points at Stylus
stylus uninstall all                    # remove skill + hook + config in one step
```

- `uninstall skill` deletes the `stylus/` skill directory from each target
  (or just the ones named with `--target`).
- `uninstall hook` strips the Stylus block from
  `~/.config/stylus/git-hooks/post-commit`, leaving any other hook content
  intact.
- `uninstall config` only unsets `core.hooksPath` if it currently points at
  the Stylus hooks directory, so unrelated Git config is never touched.
- `~/.stylus` state and evidence are **not** removed by uninstall; delete that
  directory manually if you want a full clean slate.

## Development

Stylus is a standard Python package using `src/` layout.

```bash
git clone https://github.com/jyz0309/stylus.git
cd stylus
pip install -e . pytest
pytest                       # run the full test suite (64 tests)
```

## Contributing

Contributions are welcome! Please open an issue first to discuss any change
larger than a typo.

1. Fork the repository and create a feature branch.
2. Run `pytest` and make sure all tests pass.
3. Keep changes focused and add tests for new behavior.
4. Open a pull request with a clear description of the change and the
   motivation behind it.

By contributing, you agree that your contributions will be licensed under the
MIT License.

## License

Released under the [MIT License](LICENSE). © Stylus Contributors.
