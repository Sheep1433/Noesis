# Terminal-Bench（Harbor）

Harbor 跑 Terminal-Bench 2.0。提供两条 Agent 路线：

| 脚本 | Agent | 模型 | 场景 |
|------|-------|------|------|
| `run-opencode.sh` | OpenCode | `opencode/deepseek-v4-flash-free` | **推荐**：评 deepseek-v4-flash-free，容器直连 OpenCode Zen |
| `run-noesis.sh` | Noesis SuperAgent | `opencode/deepseek-v4-flash-free`（可覆盖） | Host 侧 Agent + 容器 exec 桥接，对标 OpenCode baseline |
| `run.sh` | Claude Code | `deepseek-v4-flash` | 本机 cc-switch 代理；容器内需 `host.docker.internal` |

## 前置

Docker、`uv tool install harbor`。

- **OpenCode**：无需本机安装 `opencode` CLI（Harbor 在任务容器内安装）
- **Claude Code**：本机 `claude` CLI、`~/.claude/settings.json` 配好 `ANTHROPIC_*`

## 用法

### OpenCode + deepseek-v4-flash-free（推荐，对标 Noesis 前测 baseline）

```bash
cd backend
chmod +x evals/agent/harbor/run-opencode.sh evals/agent/harbor/run-cli-10.sh

# 本地 CLI 精选 10 题（datasets/terminal-bench-cli-10，已预下载）
./evals/agent/harbor/run-cli-10.sh --job-name tbench-cli-10

# 远程 registry 子集 / 全量
HARBOR_DATASET=terminal-bench-sample@2.0 ./evals/agent/harbor/run-opencode.sh --n-tasks 1 --job-name smoke-opencode
./evals/agent/harbor/run-opencode.sh --n-tasks 10 --job-name tbench-opencode-10
```

本地任务目录也可通过 `HARBOR_TASKS_PATH` 指定（相对 `evals/agent/harbor/` 或绝对路径），由 `run-opencode.sh` 走 `-p` 而非 `-d`。

默认 `OPENCODE_API_KEY=public`（OpenCode Zen Free）。`run-cli-10.sh` 默认 `HARBOR_AGENT_TIMEOUT_MULT=4`；全量难题用 `run-opencode.sh` 默认 `6`（单题 agent 超时约 90min）。

```bash
export OPENCODE_API_KEY=sk-... && ./evals/agent/harbor/run-opencode.sh ...
```

### Noesis SuperAgent（Harbor 适配 PoC）

```bash
cd backend
chmod +x evals/agent/harbor/run-noesis.sh

# 单题冒烟（本地 CLI 集）
HARBOR_TASKS_PATH=evals/agent/harbor/datasets/terminal-bench-cli-10 \
  ./evals/agent/harbor/run-noesis.sh \
  --include-task-name fix-git --job-name smoke-noesis

# 与 OpenCode baseline 同模型
export OPENCODE_API_KEY=public
export HARBOR_NOESIS_MODEL=opencode/deepseek-v4-flash-free
```

产物：`results/<job>/*/agent/trajectory.json`（ATIF）、`noesis.txt`（摘要）、`noesis-worker.log`（子进程日志）。查看：`harbor view evals/agent/harbor/results/<job>`。

### Claude Code + cc-switch

```bash
./evals/agent/harbor/run.sh --n-tasks 1 --job-name smoke
./evals/agent/harbor/run.sh --n-tasks 10 --job-name tbench-10 -n 2
```

参数原样传给 `harbor run`；脚本已固定 dataset、输出目录，默认 `--n-tasks 1`、`-n 1`，可在命令行覆盖。

### 本地 CLI 精选 10 题

已下载到 `evals/agent/harbor/datasets/terminal-bench-cli-10/`（同时命中 `~/.cache/harbor/tasks/`）：

`fix-git`、`overfull-hbox`、`regex-log`、`log-summary-date-ranges`、`openssl-selfsigned-cert`、`filter-js-from-html`、`git-leak-recovery`、`nginx-request-logging`、`query-optimize`、`sqlite-with-gcov`

### 首次运行：预下载任务（远程 registry）

Harbor 启动 job 时会从 GitHub 浅克隆 `terminal-bench-2`。网络不稳时易出现 `git clone ... exit status 128`。先预下载到本地缓存，后续 `run.sh` 直接命中 `~/.cache/harbor/tasks/`，不再重复克隆：

```bash
# 全量 89 题（正式 10 题评测前建议执行一次）
harbor datasets download terminal-bench@2.0 --cache

# 或仅 10 题 sample，用于 smoke
harbor datasets download terminal-bench-sample@2.0 --cache
```

## 排错

### OpenCode：`AgentTimeoutError`（链路已通但 0 分）

Harbor 默认 agent 超时 = `task.toml` 的 `agent.timeout_sec`（通常 900s）× `HARBOR_AGENT_TIMEOUT_MULT`。此前 `×2` 仅 30min，`gpt2-codegolf` 等题 OpenCode 仍在解题会被判超时。现已默认 `×6`；仍不够可：

```bash
HARBOR_AGENT_TIMEOUT_MULT=8 ./evals/agent/harbor/run-opencode.sh ...
```

### OpenCode：`FreeUsageLimitError` / Rate limit exceeded

OpenCode Zen Free 有调用频率上限。日志里若 API 已返回该错误（而非 `ConnectionRefused`），说明链路已通，等额度恢复或换付费 `OPENCODE_API_KEY` 后重跑。

验证容器出网：

```bash
docker run --rm curlimages/curl:8.5.0 -sS -m 15 \
  -X POST 'https://opencode.ai/zen/v1/chat/completions?beta=true' \
  -H 'Authorization: Bearer public' \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4-flash-free","messages":[{"role":"user","content":"hi"}],"max_tokens":1}'
```

### Claude Code：容器内 `ConnectionRefused`

`ANTHROPIC_BASE_URL=http://127.0.0.1:15721` 在 Docker 内无效，需改为 `http://host.docker.internal:15721`（见 `run.sh` 或手动 `--ae`）。

### `git clone ... terminal-bench-2.git` exit 128

Harbor 从 registry 拉取任务时对 `https://github.com/laude-institute/terminal-bench-2.git` 做 sparse clone，失败多为 **GitHub 连通性**（国内网络、代理未配置、偶发超时）。

1. 验证连通：`git ls-remote https://github.com/laude-institute/terminal-bench-2.git HEAD`
2. 按上文执行 `harbor datasets download ... --cache` 后重试 `run.sh`
3. 若需代理：`git config --global http.https://github.com.proxy http://127.0.0.1:7890`（端口按本机代理调整）
4. 仍失败：开 VPN 或换网络后重试；`--n-tasks` 较大时单次 clone 要拉更多 sparse 路径，更易超时

## 产物

```
evals/agent/harbor/results/<job-name>/
```

查看：`harbor view evals/agent/harbor/results/<job-name>`
