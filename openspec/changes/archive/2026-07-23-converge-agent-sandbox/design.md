## Context

当前沙箱为「每用户一个容器 + 整棵 `users/{uid}` rw 挂载 + 虚拟路径 token rewrite + docker|aio|local_shell 三套 backend」。排查结论（2026-07）：

| 问题 | 现象 |
|------|------|
| P0 `shlex.split` → `shlex.join` | `>`、`\|`、`&&` 变成普通 argv，重定向写文件失败 |
| P0 Compose bind 路径 | runner 把容器内 `/data/noesis` 交给宿主机 daemon → 存储分裂 |
| P0 Skills「只读」 | 个人 skills 在 `/workspace/skills`，Shell 可写绕过工具层 |
| P1 stale handle | runner 回收后 backend 缓存不失效，后续写入持续 404 |
| P1 AIO 链 | 无产品收益，chmod 破坏可执行位 |
| P1 root 运行 | slim 无 USER，裸机生成 root-owned 文件 |
| P2 Skills 元数据 | 上传后当前 session 不重扫 |
| P2 规格冲突 | `/user-skills/` vs `/skills/custom/` 双轨 |

约束：磁盘布局 `.data/users/{uid}/...` 仍由 `agent-runtime-paths` 权威；本变更改的是**容器挂载与 Agent 可见面**，不是换存储根。

## Goals / Non-Goals

**Goals:**

1. 恢复 `execute` 中 Shell 操作符语义；workspace 重定向写入对 backend/前端可见。
2. Compose 与裸机共用同一「宿主机路径 → daemon」语义，消除双存储。
3. 个人/公共 Skills 真正 ro mount；Shell 无法改写。
4. handle 缓存可失效并自动重建。
5. 删除 AIO；只保留 `docker`（生产）与 `local_shell`（开发/测试）。
6. 挂载收敛为三挂载：session workspace rw + public/personal skills ro。
7. 统一 OpenSpec 路径命名，删除冲突旧轨。

**Non-Goals:**

- 不重做用户记忆产品（仍走 MemoryMiddleware / 面板 API）；本变更只保证记忆**不**默认暴露给任意 Shell。
- 不在本变更内重建 AIO 浏览器 / CDP Skills（删除后若仍需，另开变更）。
- 不改聊天 SSE 协议、不改 Skills 上传 HTTP 路径前缀。
- 不做完整 gVisor / seccomp 加固（可后续）；本变更至少 non-root + 基础资源限制。
- 不把「每 session 一容器」做成强制产品承诺的长期唯一模型以外的多租户调度优化（先按 session 隔离挂载；容器复用策略见 Decisions）。

## Decisions

### D1：目标挂载表（权威）

| 宿主机 | 容器 | 模式 |
|--------|------|------|
| `{HOST_DATA}/users/{uid}/sessions/{sid}/workspace` | `/workspace` | rw |
| `{HOST_DATA 或 compose 约定}/extensions/skills`（或仓库 `extensions/skills` 的宿主机路径） | `/skills/public` | ro |
| `{HOST_DATA}/users/{uid}/skills` | `/skills/personal` | ro |

- `SkillsMiddleware.sources = [/skills/public, /skills/personal]`，personal 在后，同名覆盖。
- Shell `cwd` = `/workspace`。
- Agent 产物优先相对路径（`notes.md`、`research/...`）；filesystem 工具仍可接受以 `/` 开头的 workspace 根路径，但物理落点就是 `/workspace/...`，**不再**嵌套 `/workspace/sessions/...`。

**备选（否决）**：继续整用户目录 rw + 工具层只读 —— 已被 Shell 绕过证伪。

### D2：容器生命周期 — session 级挂载，容器键可仍为 user

- **挂载**必须 per-session（只挂当前 workspace）。
- **容器实例**：优先 **per-session 容器**（隔离最强，匹配「避免挂载整个用户目录」）。若资源紧张，允许 **per-user 容器 + 每次 ensure 时 remount/ recreate 当前 session 的 volume 集**，但 **SHALL NOT** 同时挂载其它 session 目录。
- 默认实现选型：**per-session 容器**（`sandbox-{hash(uid,sid)}` 或等价），idle 回收按 session；删 session 可 destroy 该 session 容器。

**备选（否决）**：维持整用户树 rw 的「单用户单容器」—— 与真正只读 skills / 记忆隔离冲突。

### D3：删除 execute 虚拟路径 rewrite（Skills 与绝对虚拟路径）

根因：`shlex.split`/`join` 无法保留操作符，且虚拟→物理表与挂载模型耦合过深。

策略：

1. **短期 P0（必须先做）**：若仍保留任何 rewrite，**禁止** `shlex.join` 回写整命令；仅对「确认为路径的 token」做替换，或改用不动操作符的策略（例如只 rewrite 已知前缀的独立 argv，且用手工拼接保留原分隔符）。更干净的做法是：**去掉 Skills/绝对虚拟路径的 Shell rewrite**，要求 Agent 用相对路径或 `/workspace/...` / `/skills/public/...`。
2. **本变更目标态**：删除 `path_rewrite` 对 `execute` 的依赖；filesystem 工具路由表单独保留（Python 层，不经 shell）。
3. 用户记忆 **不** 通过 Shell 路径提供；`execute("cat /memory/...")` **SHALL NOT** 作为规范能力。

**备选（否决）**：继续维护 Tier1/Tier2 rewrite 表 —— 与「简化挂载」目标相反。

### D4：Compose 宿主机路径

- runner **SHALL** 只使用 `NOESIS_HOST_DATA_DIR`（及 skills 的 `NOESIS_HOST_SKILLS_DIR` 或同等）等**宿主机绝对路径**创建 bind。
- Compose 中 backend 与 sandbox **SHALL** 指向同一 host 目录（bind mount 同一 path，或明确 documented 的共享 volume 宿主机源）。
- **禁止**把 runner 容器内路径（如 `/data/noesis`）直接传给 Docker daemon，除非该路径在宿主机上就是同一 inode（罕见，不作为设计）。

### D5：Handle 缓存

- `_HANDLE_CACHE` 仅优化；`ensure` / `execute` 遇 runner **404 / container missing** → 清缓存 → 重建 → 重试一次。
- in-flight 期间 runner 误回收仍靠既有 in-flight 计数；但缓存不得假装容器永存。

### D6：删除 AIO

- 配置 `sandbox.backend` 仅 `docker` | `local_shell`。
- 删除 `AioSandboxBackend`、agent-sandbox 依赖、AIO 镜像拉取文档、UID=1000 递归 chmod。
- slim 镜像声明非 root `USER`；目录权限用创建时 chown 到该 UID，**禁止**递归砍掉可执行位。

### D7：规格命名统一

| 旧 | 新 |
|----|----|
| `/skills/extensions/`、`/skills/`（平台） | `/skills/public/` |
| `/skills/custom/`、`/user-skills/` | `/skills/personal/` |
| 容器 `/skills` 单挂载 | `/skills/public` + `/skills/personal` |

删除 skills 静态索引 backend（若代码仍有）。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 每 session 容器增多，资源上升 | idle TTL + 全局 max replicas；文档说明并发上限 |
| Prompt/Skill 仍写旧路径 `/skills/custom` | 过渡期 filesystem 可接受别名 remap（仅工具层），Shell 不提供；tasks 含迁移与测试 |
| 去掉 rewrite 后旧 checkpoint/Agent 习惯绝对虚拟路径 | Prompt 与 Skills 文档更新；filesystem 仍支持旧虚拟路径一版兼容表（可选，有时限） |
| 依赖 AIO CDP 的 baoyu Skills 暂时不可用 | Non-Goal；产品说明或后续专用 browser sandbox |
| 现有 48 个单测大量假设整用户挂载 / AIO | tasks 分批改测；新增操作符与 Compose 集成测 |

## Migration Plan

1. 实现 P0（操作符 + Compose 路径）可先于整挂载收敛合入 `dev`，避免生产「突然不能写」。
2. 挂载模型与删 AIO 同 PR 或紧随其后；部署时重建所有 sandbox 容器（旧 mount 无效）。
3. 滚动：`NOESIS_HOST_DATA_DIR` / skills host 路径写入 compose 与 `.env.docker`；验证 backend 与 sandbox 同 inode。
4. Rollback：保留 git revert；若仅 P0 已上线，回滚不丢数据；若已改挂载，回滚需再次重建容器。

## Open Questions

1. filesystem 工具层是否保留一版 `/skills/custom` → `/skills/personal` 别名（建议：是，一个小版本后删除）？
2. 用户记忆是否允许经专用只读工具暴露，还是完全禁止 Shell/工具外的访问（建议：仅 MemoryMiddleware + API）？
3. per-session 容器 vs per-user remount：默认 per-session；若 runner 实现成本过高可降级为 per-user + 动态 mounts——实现前在 tasks 中二选一落地并写进 runner API。
