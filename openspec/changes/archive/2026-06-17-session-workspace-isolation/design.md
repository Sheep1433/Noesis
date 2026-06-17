## Context

### 遗留路径（已废弃）

| 模块 | 旧路径 |
|------|--------|
| DeepResearchAgent | `backend/.agent_workspace`（全局，**点在 agent_workspace 上**） |
| FaultOperationAgent | `backend/.agent_workspace/fault_ops` |

### 现行路径

仓库根 **`.data/`**（隐藏目录，`common/paths.DATA_DIR`）：

```
{REPO_ROOT}/
└── .data/                          ← 以 . 开头
    ├── agent_workspace/            ← 子目录名无前导 .
    │   └── users/{user_id}/
    │       └── sessions/{session_id}/
    │           └── workspace/        ← LocalShellBackend 根
    ├── chat_attachments/
    ├── checkpoints/
    └── logs/
```

**不是** `{REPO_ROOT}/.agent_workspace/`（仓库根下带点的工作区目录）。

实现：`agent_workspace_paths._WORKSPACE_ROOT = DATA_DIR / "agent_workspace"`。

### 约束

- 根路径写死在代码中，**不**提供 yaml/env 覆盖。
- 删会话 **始终** 清磁盘，**不**提供 `cleanup_on_session_delete` 开关。
- 不新增环境变量。

## Goals / Non-Goals

**Goals:** 会话级隔离；固定 `.data/agent_workspace/`；删会话清目录。

**Non-Goals:** 可配根路径、可关清理、容器沙箱、`outputs/` 子目录、遗留数据自动迁移。

## Decisions

### D1：根 = `DATA_DIR / "agent_workspace"`

与 `.data/chat_attachments`、`.data/checkpoints` 并列；改路径须改代码并发布，不接受运维侧 yaml 漂移。

### D2：`config/agent_workspace_paths.py`

`get_workspace_dir` / `ensure_workspace_dir` / `delete_session_workspace` / `validate_segment`。

### D3：每请求构建 backend；缺 session/user 则 abort。

### D4：Skills 只读挂载不变。

### D5：软删会话始终 `delete_session_workspace`。

## Migration Plan

1. 新会话写入 `.data/agent_workspace/users/...`。
2. 手动删除遗留 `backend/.agent_workspace/`（若仍存在）。

## Open Questions

- `outputs/` 子目录：**首版否**。
