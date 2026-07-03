## Why

Noesis AIO 沙箱在 `CompositeBackend` + `PrefixBackend` 上为 Agent 暴露了 `/research/`、`/skills/extensions/`、`/skills/custom/`、`/memory/` 等虚拟路径，但 **filesystem 工具与 `execute` 的路径语义分裂**：`read_file` 经路由映射成功，而 `execute("cat /research/...")` 在容器内找不到路径；`pwd` 返回物理 `/workspace/sessions/{sid}/workspace`，与 Prompt 中的虚拟路径不一致，导致 Agent 频繁读文件失败、误判当前目录。deepagents `CompositeBackend.execute()` **仅委托 default backend**（单通道），须在 **workspace `PrefixBackend.execute()`** 一处补齐与 filesystem 一致的 rewrite，并与 `add-super-agent-user-memory` 的 `/memory/` 物理模型对齐。

## What Changes

- **`execute` 虚拟路径 rewrite（workspace 单点）**：仅在 default workspace 的 `PrefixBackend.execute()` 内，经 `PathRewriteContext` + **token 级** rewrite，映射 `/research/`、`/skills/extensions/`、`/skills/custom/`、`/memory/` 及 workspace 根虚拟路径（`/notes.md`、`/summary_offload/...`）；extensions/custom 的 `PrefixBackend` **不**承担 execute rewrite。
- **与 super-agent 协调 `/memory/`**：execute rewrite `/memory/AGENTS.md` → 容器 `/workspace/AGENTS.md`（与 rw mount 及 `add-super-agent-user-memory` D3 一致）；归档顺序：本 change **先于** super-agent。
- **cwd 与 shell 纪律**：保持 `exec_dir = /workspace/sessions/{sid}/workspace`；Prompt 说明相对路径、`&&` 链式 `cd`；`pwd` 物理输出列为已知限制。
- **修正 Skills 路径 spec**：`/skills/baoyu-...` → `/skills/extensions/...` / `/skills/custom/...`。
- **local_shell 对齐**：同一 `PathRewriteContext` 构造，host 绝对路径为目标。
- **回归测试**：happy path + token 误伤负例 + `/memory/` 与 `read_file` 一致；**不**依赖「cat /memory/ 必失败」负例。

## Capabilities

### New Capabilities

（无 — 行为归入既有 `agent-sandbox` 与 `agent-runtime-paths`。）

### Modified Capabilities

- `agent-sandbox`：execute 单通道 rewrite、全虚拟前缀、与 super-agent 对齐的 `/memory/`、workspace 根路径、已知限制。
- `agent-runtime-paths`：虚拟路径双通道表；`/memory/` execute 目标；workspace 根路径 execute 策略。

## Impact

| 区域 | 路径 |
|------|------|
| Backend 路径映射 | `backend/agent/backends/agent_filesystem.py`（**仅 workspace** `PrefixBackend.execute`） |
| 共享 rewrite | `backend/agent/backends/path_rewrite.py`（或等价 util + `PathRewriteContext`） |
| AIO 执行 | `backend/agent/backends/aio_sandbox.py`（移除重复 rewrite） |
| Prompt | `backend/agent/prompts/execution.py`、`super_agent.py` |
| 单测 | `test_agent_filesystem.py`、`test_aio_sandbox_backend.py`、`test_sandbox_backend_factory.py` |
| 协调 | `openspec/changes/add-super-agent-user-memory`（归档顺序） |

无 API / SSE 破坏性变更。
