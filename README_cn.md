# Stylus

[English](README.md) | [简体中文](README_cn.md)

> 从 Git 提交中学习你的编码风格，并在 Agent 之间共享。

Stylus 会观察你如何修改、收窄或纠正 Agent 生成的代码。每次提交后，它会将你的改动与同一分支上最近记录的 Agent 变更进行对比，提炼出代码风格偏好，并写入一个本地 **skill** 中，供 Agent 读取。

效果是：你常用的 Agent —— **Codex**、**Cursor**、**ZCode**、**Claude** —— 会逐渐在你要纠正之前，就写出更接近你风格的代码。

---

## 目录

- [工作原理](#工作原理)
- [特性](#特性)
- [环境要求](#环境要求)
- [安装](#安装)
- [快速开始](#快速开始)
- [命令一览](#命令一览)
- [配置项](#配置项)
- [Analyzer提供方](#Analyzer提供方)
- [卸载](#卸载)
- [开发指南](#开发指南)
- [贡献指南](#贡献指南)
- [许可证](#许可证)

## 工作原理

```
  Agent编辑代码 ──▶ stylus record（捕获基线 diff）
                        │
                        ▼
              你审查并修改工作区
                        │
                        ▼
                git commit（你的修正）
                        │
                        ▼
        post-commit hook ──▶ stylus analyze
                        │
                        ▼
   对比你的提交与已记录的 Agent 变更
                        │
                        ▼
      更新 ~/.stylus 中的 skill 偏好（共享）
                        │
                        ▼
   下一次Agent会话读取 skill 并据此调整
```

1. Agent 完成变更后，Stylus 会自动运行 `stylus record`。Stylus 把当前工作区
   diff 捕获为该分支的**基线**。
2. 你审查、修改，然后 `git commit`。一个非阻塞的 `post-commit` 钩子触发
   `stylus analyze --commit HEAD`。
3. Stylus 将你的提交与已记录的基线对比，让已配置的 analyzer 提炼出可复用的
   偏好，并合并进已安装的 agent 下的 stylus skill。

如果你的提交原样复刻了 Agent 基线，Stylus 会跳过分析（无可学习内容）并记录
`skipped` 结果。

## 特性

- **从修正中学习，而非手写规则** —— 无需手动编写规则。
- **一个 skill，四个 Agent** —— 单一事实来源同步到 Codex、Cursor、ZCode、Claude。
- **OpenAI 兼容** —— 同时支持官方 OpenAI Responses API 和任意
  OpenAI 兼容的 Chat Completions 端点（DeepSeek、OpenRouter 等）。
- **自定义Analyzer** —— 通过 `STYLUS_ANALYZER_CMD` 可替换为任意外部程序。
- **忽略噪声文件** -- 排除密钥、lock 文件、生成产物等，不参与风格学习；直接复用仓库
  的 `.gitignore`，另可选全局正则文件补充。
- **非侵入式** —— 状态保存在 `~/.stylus` 下，绝不在仓库内部，因此不可能
  被误提交。hook 是非阻塞的，永远不会丢弃你的提交。
- **可完全卸载** —— `stylus uninstall all` 一键清除 skill、hook 和 Git 配置。

## 环境要求

- Python **3.11+**
- Git（在 `PATH` 中可用）
- 可选：一个 `openai` 兼容的 API key，用于基于 LLM 的分析

## 安装

Stylus 是一个纯 Python 包，仅有一个运行时依赖（`openai>=1.40`）。

```bash
# 从源码安装
git clone https://github.com/jyz0309/stylus.git
cd stylus
pip install .
```

验证已加入 `PATH`：

```bash
stylus --help
```

## 快速开始

每台机器只需配置一次：

```bash
stylus install skill        # 为 codex、cursor、zcode、claude 安装 skill
stylus install hook         # 安装全局 post-commit 钩子
stylus install config       # 将 Git 的 core.hooksPath 指向该钩子
```

然后在任意仓库中使用：

```bash
# 1. Agent 完成工作区编辑（保留改动未暂存状态）。
stylus record --summary "Agent 修改了 helper"

# 2. 你审查、修改并提交你的修正。
git commit -m "修正"
#    ^ post-commit 钩子会自动执行 `stylus analyze --commit HEAD`
```

完成。下一次该仓库中的 Agent 会话就会读取更新后的偏好。

## 命令一览

```
stylus <命令> [选项]

命令：
  init                 为当前 Git 仓库初始化 Stylus
  install skill        为 codex、cursor、zcode、claude code 安装/更新 skill
  install hook         安装全局 Stylus Git post-commit 钩子
  install config       配置 Git 使用全局 Stylus 钩子路径
  uninstall skill      从 codex、cursor、zcode 和/或 claude 移除 skill
  uninstall hook       从全局 post-commit 钩子中移除 Stylus 代码块
  uninstall config     若 Git core.hooksPath 指向 Stylus 则取消设置
  uninstall all        一步移除 skill + hook + 配置
  record               将最近的 Agent 产出 diff 记录为基线
  analyze              分析已提交的修订版本与基线的差异
```

### `install skill`

一次性为 **codex、cursor、zcode、claude code** 创建或更新 Stylus skill。Codex 是单一
事实来源（Analyzer只更新它），其余目标在每次安装时从它同步。

```
$ stylus install skill
Installed Stylus skill at:
  codex:  /home/you/.codex/skills/stylus
  cursor: /home/you/.cursor/skills/stylus
  zcode:  /home/you/.agents/skills/stylus
  claude: /home/you/.claude/skills/stylus
```

默认目标目录：

| 目标   | 默认路径                  | 覆盖环境变量                     |
| ------ | ------------------------- | -------------------------------- |
| codex  | `~/.codex/skills`         | `CODEX_HOME`                     |
| cursor | `~/.cursor/skills`        | `STYLUS_CURSOR_SKILLS_ROOT`      |
| zcode  | `~/.agents/skills`        | `STYLUS_ZCODE_SKILLS_ROOT`       |
| claude | `~/.claude/skills`        | `STYLUS_CLAUDE_SKILLS_ROOT`      |

仅安装指定目标：

```bash
stylus install skill --target cursor --target zcode
```

### `install hook` / `install config`

`install hook` 在 `~/.config/stylus/git-hooks/post-commit` 写入全局钩子文件。
`install config` 将 Git 的全局 `core.hooksPath` 指向该目录。钩子是
**非阻塞**的：分析失败只会打印警告，提交照常保留。

### `record`

把当前工作区 diff 捕获为当前仓库 + 分支的Agent基线。Agent必须把编辑保留为
**未暂存**状态 —— 如果改动已暂存，Stylus 会拒绝记录，这样基线反映的是Agent
实际产出的内容，而不是部分提交。

```bash
stylus record --summary "Agent修改了 helper"
stylus record --summary "重构 api 客户端" --task "迁移到 v2 sdk"
```

### `analyze`

将某次提交与最近记录的基线对比并更新偏好。通常由钩子调用，也可手动运行：

```bash
stylus analyze --commit HEAD
stylus analyze --commit HEAD --debug   # 显示提供方、输入大小和输出
```

## 配置项

Stylus 完全通过环境变量配置 —— 无需配置文件。

| 变量                        | 用途                                           | 默认值                           |
| --------------------------- | ---------------------------------------------- | -------------------------------- |
| `STYLUS_HOME`               | 状态、diff 和证据日志的根目录                  | `~/.stylus`                      |
| `OPENAI_API_KEY`            | 启用 OpenAI LLM Analyzer                         | *(未设置 → 本地Analyzer)*          |
| `STYLUS_OPENAI_MODEL`       | OpenAI Analyzer使用的模型名                      | `gpt-5.2`                        |
| `STYLUS_OPENAI_BASE_URL`    | OpenAI 兼容的 base URL                         | `https://api.openai.com/v1`      |
| `STYLUS_ANALYZER_CMD`       | 外部Analyzer命令（最高优先级）                   | *(未设置)*                       |
| `STYLUS_MAX_DIFF_BYTES`     | 发送给Analyzer的单个 diff 字节上限               | `200000`                         |
| `CODEX_HOME`                | 覆盖 codex 的 skill 根目录                     | `~/.codex`                       |
| `STYLUS_CURSOR_SKILLS_ROOT` | 覆盖 cursor 的 skill 根目录                    | `~/.cursor/skills`               |
| `STYLUS_ZCODE_SKILLS_ROOT`  | 覆盖 zcode 的 skill 根目录                     | `~/.agents/skills`               |
| `STYLUS_CLAUDE_SKILLS_ROOT` | 覆盖 claude 的 skill 根目录                    | `~/.claude/skills`               |

### 忽略文件

有些文件不应参与 Stylus 的风格学习--密钥、lock 文件、生成产物、第三方代码。
Stylus 合并两处忽略规则，并**同时**作用于记录的 agent 基线（`stylus record`）
与分析的提交（`stylus analyze`），保证两侧对比口径一致。

- **`~/.stylus/ignore`**（全局，跨仓库）：每行是一个 **Python 正则表达式**
  （用 `re.search` 匹配），同时匹配相对于仓库根的路径（`src/pkg/mod.py`）与
  裸文件名（`mod.py`）。用于跨仓库的通用规则（如 `\.env$`、`\.pem$`）。
- **仓库自带的 `.gitignore`**：由 Git 本身通过 `git check-ignore --no-index`
  匹配，**无需额外配置**，直接复用你已有的忽略规则。由于使用了 `--no-index`，
  规则对**已跟踪文件同样生效**（Git 默认对已跟踪文件忽略 `.gitignore`，否则像
  已提交的 `app.log` 即便有 `*.log` 规则也会漏进 Stylus 的 diff）。

命中任一来源即排除。`~/.stylus/ignore` 中的非法正则会被跳过并打印警告，不会中断
运行。当既无全局忽略文件、仓库也无 `.gitignore` 时行为不变--所有文件都会被捕获。

`~/.stylus/ignore` 示例（用于 `.gitignore` 未覆盖的规则）：

```
# .gitignore 未覆盖的密钥
\.pem$

# 按文件名匹配任意位置
minified\.js$
```

## Analyzer 

Stylus 根据环境自动选择 Analyzer。运行 `stylus analyze --debug` 可查看选中
的是哪一个。

### 1. 本地Analyzer（默认）

当未设置 `OPENAI_API_KEY` 且未设置 `STYLUS_ANALYZER_CMD` 时，Stylus 使用
确定性的本地Analyzer。学习回路可端到端运行而**无需网络访问** —— 适合试用
Stylus 或在离线环境中运行。

### 2. OpenAI Analyzer

设置 `OPENAI_API_KEY` 即可启用基于 LLM 的分析：

```bash
export OPENAI_API_KEY="sk-..."
export STYLUS_OPENAI_MODEL="gpt-5.2"          # 可选
export STYLUS_OPENAI_BASE_URL="https://api.openai.com/v1"  # 可选
```

Stylus 会自动选择 API 端点：

- **OpenAI 官方**（`api.openai.com`）→ Responses API（`/responses`），使用
  Structured Outputs（`json_schema` 严格模式）。
- **其他任意 Provider**（DeepSeek、OpenRouter 等）→ Chat Completions API
  （`/chat/completions`），使用
  `response_format={"type":"json_object"}`。完整的 JSON schema 和示例会
  嵌入系统提示，因此兼容仅支持 `json_object` 模式的端点（如 DeepSeek），
  不需要 Structured Outputs。

### 3. 自定义 Analyzer 命令

如需完全自定义的Analyzer，设置 `STYLUS_ANALYZER_CMD`。它的优先级高于
`OPENAI_API_KEY`：

```bash
export STYLUS_ANALYZER_CMD='python3 /path/to/custom-stylus-analyzer.py'
```

Stylus 通过 **stdin** 发送一个 JSON 对象：

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

该命令必须在 **stdout** 输出Analyzer JSON。内置的 OpenAI Analyzer使用相同的
schema：

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

### 从 Python 中使用该 schema

面向 SDK 的响应约束已从 `stylus.analyzer` 导出：

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

Chat Completions 调用方可用 `ANALYZER_CHAT_RESPONSE_FORMAT` 作为
`response_format`。

## 卸载

可单独或一次性移除 Stylus 的各集成组件：

```bash
stylus uninstall skill                  # 从 codex、cursor、zcode、claude 移除 skill
stylus uninstall skill --target cursor  # 仅移除某一个目标
stylus uninstall hook                   # 从全局 post-commit 钩子移除 Stylus 代码块
stylus uninstall config                 # 若 Git core.hooksPath 指向 Stylus 则取消设置
stylus uninstall all                    # 一步移除 skill + 钩子 + 配置
```

- `uninstall skill` 从每个目标删除 `stylus/` skill 目录（或仅删除
  `--target` 指定的目标）。
- `uninstall hook` 从 `~/.config/stylus/git-hooks/post-commit` 中剥离
  Stylus 代码块，保留其他钩子内容不变。
- `uninstall config` 仅在 `core.hooksPath` 当前指向 Stylus 钩子目录时才
  取消设置，绝不会动用户无关的 Git 配置。
- `~/.stylus` 下的状态和证据**不会**被卸载删除；如需彻底清理，请手动删除
  该目录。

## 开发指南

Stylus 是采用 `src/` 布局的标准 Python 包。

```bash
git clone https://github.com/jyz0309/stylus.git
cd stylus
pip install -e . pytest
pytest                       # 运行完整测试套件（64 个测试）
```

## 贡献指南

欢迎贡献！大于错别字修复的改动，请先开 issue 讨论。

1. Fork 仓库并创建功能分支。
2. 运行 `pytest` 确保全部测试通过。
3. 保持改动聚焦，并为新行为补充测试。
4. 提交 Pull Request，清晰描述改动及其动机。

提交贡献即表示你同意将该贡献以 MIT 许可证授权。

## 许可证

基于 [MIT License](LICENSE) 发布。© Stylus Contributors。
