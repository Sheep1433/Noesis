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

## 产物

```
evals/agent/harbor/results/<job-name>/
```

查看：`harbor view evals/agent/harbor/results/<job-name>`
