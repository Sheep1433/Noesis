# Terminal-Bench（Harbor）

只保留两条路线，均使用本地精选 10 题：

```bash
cd backend

# 首次运行一次，创建独立评测环境
./evals/agent/harbor/setup.sh

# 单题冒烟（默认）
./evals/agent/harbor/run-noesis.sh
./evals/agent/harbor/run-opencode.sh

# 完整 10 题
./evals/agent/harbor/run-noesis.sh cli-10
./evals/agent/harbor/run-opencode.sh cli-10
```

- `run-noesis.sh`：Noesis SuperAgent。
- `run-opencode.sh`：OpenCode 对照组。
- 默认模型均为 `opencode/deepseek-v4-flash-free`，默认 `OPENCODE_API_KEY=public`。
- 两条路线共用 `evals/agent/harbor/.venv`，运行时不会重复安装依赖。

结果：

```text
evals/agent/harbor/results/noesis-smoke/
evals/agent/harbor/results/noesis-cli-10/
evals/agent/harbor/results/opencode-smoke/
evals/agent/harbor/results/opencode-cli-10/
```

查看：

```bash
evals/agent/harbor/.venv/bin/harbor view evals/agent/harbor/results/<job-name>
```

前置：Docker、uv。
