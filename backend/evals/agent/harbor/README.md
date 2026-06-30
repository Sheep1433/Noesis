# Terminal-Bench（Harbor + Claude Code）

Harbor 跑 Terminal-Bench 2.0，仅评测 Claude Code。

## 前置

Docker、`uv tool install harbor`、本机 `claude` CLI、`~/.claude/settings.json` 配好 `ANTHROPIC_*`。

## 用法

```bash
cd backend
./evals/agent/harbor/run.sh --n-tasks 1 --job-name smoke
./evals/agent/harbor/run.sh --n-tasks 10 --job-name tbench-10 -n 2
```

参数原样传给 `harbor run`；脚本已固定 dataset、agent、输出目录，默认 `--n-tasks 1`、`-n 1`，可在命令行覆盖。

### 首次运行：预下载任务（推荐）

Harbor 启动 job 时会从 GitHub 浅克隆 `terminal-bench-2`。网络不稳时易出现 `git clone ... exit status 128`。先预下载到本地缓存，后续 `run.sh` 直接命中 `~/.cache/harbor/tasks/`，不再重复克隆：

```bash
# 全量 89 题（正式 10 题评测前建议执行一次）
harbor datasets download terminal-bench@2.0 --cache

# 或仅 10 题 sample，用于 smoke
harbor datasets download terminal-bench-sample@2.0 --cache
```

## 排错

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
