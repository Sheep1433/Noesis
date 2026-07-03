## Context

- **现状**：Noesis 经 `create_agent_backend()` 构建 `CompositeBackend`：`default` = session workspace（`PrefixBackend` → `AioSandboxBackend` 或 `LocalShellBackend`），`routes` = `/skills/` 索引、`/skills/extensions/`、`/skills/custom/`、`/memory/`。
- **filesystem 工具**：`CompositeBackend` 按 route 剥前缀 → `PrefixBackend._map_in()` 拼容器/host 绝对路径 → AIO `read`/`write`/`ls` 正常。
- **execute 单通道（deepagents 硬约束）**：`CompositeBackend.execute()` **仅委托 default backend**（workspace 链）；extensions / custom / memory 的 `PrefixBackend` **永远不会**收到 `execute`。全部 rewrite **必须**在 workspace 这一条 `PrefixBackend.execute()` 内完成。
- **cwd**：`shell.exec_command(exec_dir="/workspace/sessions/{sid}/workspace")`；Agent Prompt 要求 `/research/...` 虚拟路径。
- **并行 change**：`add-super-agent-user-memory` 规定 `/memory/` → 容器 `/workspace/AGENTS.md|USER.md`（与 rw mount 一致）。本 change 的 execute rewrite **须**与之对齐，不得再写「记忆仅 filesystem、execute 不可读」。
- **参考**：deepagents CLI 使用扁平沙箱 FS + Provider 默认 working dir；Noesis 保留 Composite，在 **workspace PrefixBackend 单点** 补齐 execute 映射。

## Goals / Non-Goals

**Goals:**

- workspace `PrefixBackend.execute()` 内，`execute` 中出现的 Agent 虚拟绝对路径 **SHALL** 与 `read_file`/`write_file` 解析到同一物理位置（含 `/memory/`、workspace 根路径）。
- 修正主 spec 中过时的 `/skills/{name}` 示例。
- local_shell 与 aio 共用 `PathRewriteContext` + 同一 rewrite 函数。
- 首版 token 级 rewrite（非裸 `str.replace` 全串替换），覆盖 happy path 与常见误伤负例。

**Non-Goals:**

- 不重做 Docker 挂载拓扑。
- 不 fork deepagents `CompositeBackend.execute` 做路径路由。
- 不改为 per-session 容器。
- 不在本 change 实现 `refine-tool-outcome-handling` 的 `ToolTimeoutError` 改造。
- 不在本 change 改写 `pwd`/`realpath` 输出（见已知限制）。
- 不在本 change 拦截 `execute("cat /workspace/AGENTS.md")` 物理路径 bypass（见已知限制与协调节）。

## Coordination: `add-super-agent-user-memory`

| 主题 | 权威来源 | 本 change 立场 |
|------|----------|----------------|
| `/memory/AGENTS.md` 磁盘 | `users/{uid}/AGENTS.md` | 与 super-agent D2/D3 一致 |
| AIO 容器内物理路径 | `/workspace/AGENTS.md`（mount 根） | execute rewrite `/memory/` → `/workspace/AGENTS.md` |
| filesystem 实现 | super-agent 将 `UserMemoryBackend` 迁为容器路由或等价 | 本 change 只保证 **rewrite 目标** 与 super-agent 物理路径一致；实现可仍用 host `UserMemoryBackend` 直至 super-agent 落地 |
| 归档顺序 | **先** `unify-sandbox-virtual-paths`，**再** `add-super-agent-user-memory` | 避免主 spec 半成品互相覆盖 |

**物理路径 bypass（已知）**：rw mount 使 `execute("cat /workspace/AGENTS.md")` 今日即可成功，无法仅靠「不重写 `/memory/`」挡住。首版 **不拦截**；Prompt 引导使用 `/memory/` 虚拟路径；后续 hardening 可单列 change。

## Decisions

### D1：仅在 workspace `PrefixBackend.execute()` 做全量 rewrite

**决策**：`build_agent_filesystem_backend()` 仅为 **default workspace** 的 `PrefixBackend` 配置 `PathRewriteContext` 与完整映射表；extensions / custom 的 `PrefixBackend` **不**配置 execute rewrite（因其 `execute()` 不会被 `CompositeBackend` 调用）。

**理由**：deepagents `CompositeBackend.execute()` 只委托 default backend。若在三个 `PrefixBackend` 实例上拆分映射表，extensions/custom 的 rewrite **根本不会生效**。

**实现落点**：

```text
CompositeBackend.execute()
  → workspace PrefixBackend.execute()   ← 唯一 rewrite 入口
       → rewrite_virtual_paths_in_command(ctx, command)
       → inner (AioSandboxBackend | LocalShellBackend).execute()
```

`AioSandboxBackend` 保留 `exec_dir`、mutex、浏览器 env、路径安全校验；**移除** `_rewrite_custom_skill_paths_in_command`（合并进共享 rewrite）。

### D2：`/research/` 仍为 workspace 子目录

保持 `/research/` 为 `.../workspace/research/...` 子目录，不提升为独立 Composite route。rewrite：`/research/foo` → `{workspace_prefix}/research/foo`。

### D3：`/memory/` 与 execute（与 super-agent 对齐）

**决策**：execute rewrite **SHALL** 包含：

| 虚拟路径 | AIO 容器目标 | local_shell host 目标 |
|----------|-------------|----------------------|
| `/memory/AGENTS.md` | `/workspace/AGENTS.md` | `.data/users/{uid}/AGENTS.md` |
| `/memory/USER.md` | `/workspace/USER.md` | `.data/users/{uid}/USER.md` |

与 `add-super-agent-user-memory` D3 一致；**不再**声明「记忆仅经 filesystem、execute 不可读」。

### D4：workspace 根虚拟绝对路径

default workspace 的 filesystem 支持 virtual `/notes.md`、`/summary_offload/...`（`PrefixBackend._map_in` → `{workspace_prefix}/notes.md`）。execute **SHALL** 同样 rewrite：

- **Tier 1**（显式前缀，最长优先）：`/research/`、`/skills/extensions/`、`/skills/custom/`、`/memory/`
- **Tier 2**（workspace 根）：token 形如 `/foo` 或 `/foo/bar`，且 **不**命中系统 denylist（`/usr`、`/etc`、`/tmp`、`/bin`、`/sbin`、`/opt`、`/var`、`/home`、`/dev`、`/proc`、`/sys`、`/skills` 单层索引等）→ `{workspace_prefix}{token}`

**Prompt 补充**：任务产物优先 `/research/...`；workspace 根文件在 execute 中可用相对路径（`notes.md`）或虚拟绝对路径（`/notes.md`）。

### D5：token 级 rewrite（首版最小安全子集）

**决策**：**禁止**对整条 command 做裸 `str.replace`。首版流程：

1. `shlex.split(command)` 得 token 列表（保留 operator token 如 `&&`、`|`、`>` 不 rewrite）
2. 对每个「像绝对路径」的 token（以 `/` 开头，非 `=/` 等赋值右侧可另行扩展）应用 Tier 1 → Tier 2
3. 用 `shlex.join`（或等价）重组命令

**单测须含负例**：`echo "see /research/foo"` 中引号内字符串 **SHALL NOT** 被改写（shlex 分词后引号内为单 token 时仍须识别引号包裹 — 若 shlex 已剥离引号则对纯字符串 token 检测是否整条为 path）。

**风险残留**：heredoc、`$()`、复杂 shell 仍可能漏 rewrite；记入已知限制，后续可扩展。

### D6：`PathRewriteContext` 构造

**决策**：`rewrite_virtual_paths_in_command(command, *, ctx: PathRewriteContext)`，`PathRewriteContext` 由 `build_agent_filesystem_backend(user_id, session_id, ...)` **同源**构造，字段示例：

| 字段 | 用途 |
|------|------|
| `backend_kind` | `aio` \| `local_shell` |
| `user_id`, `session_id` | 解析 custom skills、memory host 路径 |
| `workspace_prefix` | AIO: `/workspace/sessions/{sid}/workspace`；local: `get_workspace_dir()` |
| `extensions_prefix` | AIO: `/skills`；local: `skills_root()` |
| `custom_skills_prefix` | AIO: `/workspace/skills`；local: `get_user_skills_dir(uid)` |
| `memory_agents_path`, `memory_user_path` | 容器或 host 绝对路径 |

**禁止**在 rewrite util 内硬编码路径或重复 `get_workspace_dir` 逻辑。

### D7：local_shell 对齐

workspace `PrefixBackend` 的 rewrite 对 `LocalShellBackend` 同样生效；`ctx` 填入 host `Path` 字符串目标。

### D8：shell 状态与 `cd`

每次 `execute` 为独立 shell 调用（AIO `exec_command` + `exec_dir`）；**跨调用的 `cd` 不持久**。rewrite `cd /research/demo` 为物理路径，但下一次 `execute("make")` 仍在 workspace 根执行。

**Prompt**：路径相关操作 **SHALL** 在同一 command 用 `&&` 链接，例如 `cd /research/demo && make`。

### D9：挂载权限

`ensure_workspace_dir()` **已**含 `_chmod_sandbox_dir`（见 `user_data_paths.py`）。本 change **不**重复添加 chmod；若回归失败再查 runner `ensure_sandbox_mount_readable`。

## Known Limitations（首版接受）

| 限制 | 说明 | 后续 |
|------|------|------|
| `pwd` / `realpath` 输出物理路径 | Agent 可能从输出学到 `/workspace/sessions/...` | 评估 execute 输出侧前缀脱敏；或 Prompt 纪律 |
| 物理路径 bypass 读记忆 | `cat /workspace/AGENTS.md` 可成功 | 可选 execute 拦截 denylist |
| 复杂 shell 语法 | heredoc、`$()` 内路径可能未 rewrite | 扩展 tokenizer 或文档约束 |
| 跨 session 物理路径 | spec 允许 `execute` 读 `/workspace/sessions/s2/...` | 产品策略，非本 change |

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| token rewrite 漏网 | Tier 1/2 + 单测；Prompt 推荐 `&&` 链式命令 |
| 与 super-agent 归档顺序 | tasks 4.3 明确顺序 |
| filesystem `/memory/` 仍走 host `UserMemoryBackend` 直至 super-agent | rewrite 目标与 mount 一致，同文件 |

## Migration Plan

1. 实现 `PathRewriteContext` + token rewrite + workspace `PrefixBackend.execute()`。
2. 移除 `AioSandboxBackend._rewrite_custom_skill_paths_in_command`。
3. 更新 Prompt（cwd、`&&`、`/memory/` 与 super-agent 一致表述）。
4. **归档本 change** → 合并 delta 至 `openspec/specs/agent-sandbox/spec.md`、`agent-runtime-paths/spec.md`。
5. **再归档** `add-super-agent-user-memory`（含 `UserMemoryBackend` → 容器路由迁移）。
6. 无需重建用户容器。

## Open Questions

- execute 输出脱敏 `pwd` — 首版 **不做**（见 Known Limitations）。
- `/skills/` 索引路径出现在 execute — **不** rewrite；Agent 应使用 extensions/custom。
