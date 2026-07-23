# agent-runtime Specification

## Purpose

本能力规定 Agent **运行时文件系统与沙箱**：宿主机 `.data/users/` 布局、Agent/Shell 共用的绝对路径坐标系（`/workspace`、`/skills/public|personal`、`/memory`）、backend 工厂（docker / local_shell）、Skills 只读挂载与用户 ZIP、用户记忆、以及 web_search / web_fetch。代码锚点：`config/user_data_paths.py`、`agent/backends/{paths,agent_path,memory,factory,docker_exec,local_shell}.py`。

## 路径命名

| 路径 | 含义 |
|------|------|
| `{REPO_ROOT}/.data/users/{user_id}/` | 用户数据根 |
| `.../sessions/{session_id}/workspace/` | 会话工作区（宿主机） |
| `.../sessions/{session_id}/uploads/` | 附件原文件 |
| `.../sessions/{session_id}/attachments/` | 附件 Markdown |
| `.../skills/` | 用户 Skills（跨会话） |
| `.../AGENTS.md` / `USER.md` | 用户记忆（跨会话） |
| 容器 `/workspace` | session workspace rw |
| 容器 `/skills/public` | 平台 Skills ro |
| 容器 `/skills/personal` | 用户 Skills ro |
| Agent `/memory/` | 记忆路由（**不**经沙箱默认挂载） |

**SHALL NOT** 再使用 filesystem 虚拟根 ``/notes.md`` 坐标系。

## Requirements

### Requirement: 用户数据根与会话子树

路径模块 SHALL 将用户根定为 `DATA_DIR / "users"`；`user_id` / `session_id` 拼入路径前 SHALL 校验段字符。系统 **SHALL NOT** 用配置覆盖 `.data/users` 根。

会话子树 SHALL 含 `workspace/`、`uploads/`、`attachments/`。`delete_session_data` SHALL 删除整棵会话子树且幂等，**SHALL NOT** 删除用户级 `skills/`、`AGENTS.md`、`USER.md`。

#### Scenario: 工作区路径

- **WHEN** `get_workspace_dir("42", "sess-abc")`
- **THEN** 返回 `{REPO_ROOT}/.data/users/42/sessions/sess-abc/workspace`

#### Scenario: 非法 user_id

- **WHEN** `user_id` 含 `..` 或 `/`
- **THEN** SHALL 抛出 `ValueError`

### Requirement: Agent 路径唯一坐标系

Agent 文件工具与 Shell **SHALL** 使用同一绝对路径：`/workspace/...`、`/skills/public|personal/...`、`/memory/...`。

`paths.canonicalize_agent_path` SHALL：

- 将裸路径 / ``/notes.md`` 归一为 ``/workspace/notes.md``
- 将 UI ``sessions/{sid}/workspace/...`` 映射为 ``/workspace/...``
- 折叠多余 ``/workspace/workspace/...``
- 保持 ``/skills/...``、``/memory/...``

local 模式：`AgentPathBackend(strip_root=/workspace)` 对接宿主机 FilesystemBackend。docker 模式：default backend 为沙箱（skills 已在容器挂载），另挂 `/memory/` route。

**SHALL NOT** 对 `execute` 做 shlex 整命令路径 rewrite；**SHALL NOT** 改第三方 Skill 文案纠路径。

#### Scenario: 裸路径归一

- **WHEN** Agent `write_file("/notes.md", "x")`
- **THEN** 写入落在当前 session 宿主机 `workspace/notes.md`

#### Scenario: UI 路径注入映射

- **WHEN** mention 解析得到 `sessions/s1/workspace/a.md`
- **THEN** Agent 可见路径 SHALL 为 `/workspace/a.md`

### Requirement: 沙箱 backend 与挂载面

`sandbox.backend` SHALL 仅支持 `docker`（生产）与 `local_shell`（开发/测试）。**SHALL NOT** 支持已移除的 `aio`。

docker：每 `(user_id, session_id)` 容器；挂载仅为：

- session workspace → `/workspace`（rw）
- 公共 skills → `/skills/public`（ro）
- 个人 skills → `/skills/personal`（ro）

**SHALL NOT** 将整个 `users/{uid}/` rw 挂入容器。删 session **SHALL** 清理会话磁盘，容器生命周期由 runner idle / 显式回收管理；**SHALL NOT** 因删 session 必须立刻销毁无关用户资源以外的全局单例（无 user 级长驻 AIO）。

沙箱 env **SHALL NOT** 含业务 API 密钥（MAY 含 scoped `GH_TOKEN`）。

#### Scenario: Skills 只读

- **WHEN** `execute("echo x > /skills/personal/foo/SKILL.md")`
- **THEN** SHALL 失败；宿主机 personal Skills **SHALL NOT** 被改

#### Scenario: aio 配置拒绝

- **WHEN** `SANDBOX_BACKEND=aio`
- **THEN** 工厂 SHALL 抛出明确错误

### Requirement: execute 保留 Shell 语义

对 `execute` **SHALL NOT** 使用会破坏 `>`、`|`、`&&` 等的 shlex split/join 回写。

#### Scenario: 重定向

- **WHEN** `execute("printf done > /workspace/out.txt")`
- **THEN** 宿主机当前 session workspace 出现 `out.txt` 内容 `done`

### Requirement: handle 缓存失效重建

SandboxService 缓存仅为优化；runner 返回容器不存在时 backend SHALL 清缓存、ensure 并重试至少一次。

#### Scenario: idle 后恢复

- **WHEN** 容器已被 TTL 回收且仍有旧 handle，随后 `execute`
- **THEN** SHALL 重建沙箱并执行（或返回明确不可恢复错误）

### Requirement: Skills 文件系统 API

平台 Skills 根与用户 Skills ZIP 上传/树 API SHALL 由 skills 服务提供；Agent 侧权威路由为 `/skills/public/`、`/skills/personal/`（同名时 personal 优先）。**SHALL NOT** 再提供 `/skills/extensions`、`/skills/custom` 作为权威别名。

#### Scenario: 列表个人 skill

- **WHEN** 用户 ZIP 安装 skill 包后调用个人 skills 树 API
- **THEN** 响应 SHALL 含该包且磁盘位于 `users/{uid}/skills/`

### Requirement: 用户记忆 `/memory/`

`ensure_user_memory_files` SHALL seed `AGENTS.md` 与 `USER.md`。Agent 经 `/memory/` route（`memory.UserMemoryBackend`）读写；**SHALL NOT** 假设记忆文件出现在沙箱 `/workspace` 挂载中。

SuperAgent 装配 SHALL 注入记忆相关中间件/提示（见 `agent-profiles`）。

#### Scenario: 记忆不在 workspace ls

- **WHEN** docker 模式下 `execute("ls /workspace")`
- **THEN** **SHALL NOT** 默认列出用户根 `AGENTS.md` 作为 workspace 条目

#### Scenario: 经工具写 AGENTS.md

- **WHEN** Agent 写入 `/memory/AGENTS.md`（经 HITL 策略允许或审批后）
- **THEN** 宿主机 `users/{uid}/AGENTS.md` SHALL 更新

### Requirement: Web 工具

系统 SHALL 提供 `web_search` / `web_fetch`（Provider 可配置，如 Tavily / 本地 fetch）；密钥仅经配置注入应用侧，**SHALL NOT** 注入沙箱 env。

#### Scenario: 无 Key 时明确失败

- **WHEN** 未配置搜索 Provider 密钥且调用 web_search
- **THEN** SHALL 返回可理解的工具错误，而非空成功
