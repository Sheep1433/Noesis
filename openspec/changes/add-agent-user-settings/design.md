## Context

Noesis 已具备用户级记忆文件（`USER.md` / `AGENTS.md`）、会话上下文面板读写、Skills/MCP 独立管理页，但侧栏用户区仅主题+退出。本 change 引入「个人与 Agent 设置」产品壳，挂载记忆审阅、定时任务与通讯通道配置；参考 OpenClaw（bootstrap 记忆 + Automations）与 Clowder（SettingsShell + 独立 Memory Hub）的信息架构，按 Noesis 体量收敛。

约束：API → Service → Domain/Agent；配置禁硬编码；记忆与 Agent `/memory/` 同盘；**slash 不在本设计范围**。

## Goals / Non-Goals

**Goals:**

- 设置壳 + section 导航，入口为侧栏头像。
- L0/L1 记忆主编辑入口在设置；面板兼容保留。
- 规格定义 L2 日记层路径与注入边界（实现可分期）。
- 用户级 cron：CRUD、启停、调度、与 `qa_type`/可选 session 绑定。
- 通讯通道配置模型（Telegram 为首个 `channel_type`）；凭据仅人写。

**Non-Goals:**

- Slash 命令配置/注册 UI。
- Memory Hub（图谱、健康度大盘、导入 Codex 记忆）。
- OpenClaw SOUL/IDENTITY/TOOLS/HEARTBEAT 多文件 bootstrap。
- Heartbeat 周期巡检（可后置于 cron 稳定后）。
- 首期必须打通 Telegram 真收发（可配置契约 + stub/feature flag；真投递可跟进任务）。

## Decisions

### D1：设置壳形态

| 选项 | 结论 |
|------|------|
| 独立路由 `/settings` + `?s=` section | **采用**（对齐 Clowder，可深链、可扩展） |
| 仅 Popover 菜单 | 拒绝（装不下 cron/通道） |
| 塞进会话侧栏 | 拒绝（会话级 vs 用户级混淆） |

前端：`frontend/src/views/settings/` + `SettingsShell` / `SettingsNav`；侧栏头像 → 设置（保留退出）。

Section 固定集合（本 change）：`overview` | `profile` | `memory` | `capabilities` | `automation` | `channels` | `account`。  
**SHALL NOT** 出现 slash 相关 section。

### D2：记忆分层与 API

```
L0  USER.md                 每会话注入
L1  AGENTS.md               每会话注入（控长）
L2  memory/YYYY-MM-DD.md    不默认注入；工具/API 检索（分期实现）
```

- 设置页读写走**用户级** API，不依赖 `session_id`：
  - `GET/PUT /api/user/memory/{file}`，`file ∈ {USER.md, AGENTS.md}`
  - L2：`GET/PUT /api/user/memory/daily/{date}`（实现阶段可选）
- 会话面板 PUT 记忆文件 **保留**，与用户 API 写同一磁盘路径。
- Agent 仍经 `/memory/USER.md`、`/memory/AGENTS.md`；L2 映射 `/memory/memory/YYYY-MM-DD.md` 或 `/memory/daily/...`（实现时与 `agent-runtime-paths` 对齐，本 design 推荐磁盘 `users/{uid}/memory/YYYY-MM-DD.md`，虚拟 `/memory/daily/YYYY-MM-DD.md`）。

画像 UI：表单字段（称呼、角色、时区、背景）序列化进 `USER.md` 约定 frontmatter/段落 + 「原文」折叠编辑。L1：Markdown 编辑器 + 「最近修改时间」。

### D3：Cron 存储与调度

| 选项 | 结论 |
|------|------|
| PostgreSQL 表 `user_scheduled_tasks` | **采用**（可查询、启停、与用户外键一致） |
| 仅 `.data/users/{uid}/cron.json` | 备选本地/单机；多实例需 DB |

字段（逻辑）：`id`, `user_id`, `name`, `cron_expr`, `timezone`, `enabled`, `qa_type`, `prompt`, `session_binding`（`none` | `session:{id}`）, `delivery`（`none` | `web_notify` | `channel:{id}` 预留）, `last_run_at`, `next_run_at`, `last_status`。

调度：后端 startup 注册异步调度器（APScheduler 或等效）；**多 worker 时**用 DB advisory lock / `FOR UPDATE SKIP LOCKED` 抢跑，避免重复执行。

执行语义（对齐 OpenClaw 简化版）：

- 默认 **isolated**：新建或复用 `cron:` 前缀逻辑会话跑一轮 Agent，**不**把例行跑次写入用户主聊天时间线（可写独立 run 日志）。
- `session_binding=session:{id}`：若会话已删/归档 → 任务 **自动 disabled** 并记录原因。
- 删除用户 → 级联删任务。

API 前缀：`/api/user/scheduled-tasks`（CRUD + enable/disable + manual run）。

### D4：通讯通道

```
users/{uid}/channels.json   # 或 DB 表 user_messaging_channels
  - channel_id
  - type: telegram | (future: feishu, ...)
  - enabled
  - display_name
  - secrets: 加密或仅服务端可读（env/KMS 优先；禁止写入 USER/AGENTS.md）
  - routing: 入站默认 qa_type；出站绑定（可选 cron delivery）
```

设置「通讯」section：列表、连接向导（Telegram bot token）、启停、测试连通。  
Agent **SHALL NOT** 通过工具改写通道密钥或 `channels` 配置。

首期实现梯度：

1. **P0**：数据模型 + 设置 UI + API（保存/脱敏回显）。
2. **P1**：Telegram webhook/long-poll 入站 → 创建/路由到用户会话（可另 change 切开，但规格本 change 写清）。

### D5：能力深链

`capabilities` section **不**复制 Skills/MCP/知识库整页，仅卡片链到既有路由；文案标明「在完整管理页中编辑」。

### D6：配置人写边界（Clowder 铁律）

| 可写方 | 内容 |
|--------|------|
| 用户（设置/API） | USER/AGENTS、cron 定义、通道配置与密钥 |
| Agent | `/memory/` 下 L0/L1（及未来 L2 日记）；**不可**写 cron/channels 密钥 |
| 平台配置 | 模型密钥、sandbox 等仍走服务端 env/yaml |

### D7：与现有模块关系

```
SideBar ──▶ SettingsShell (?s=)
               ├─ profile/memory ──▶ UserMemoryService ──▶ .data/users/{uid}/
               ├─ automation ────▶ ScheduledTaskService ──▶ DB + Scheduler
               ├─ channels ──────▶ MessagingChannelService ──▶ channels store
               └─ capabilities ──▶ router.push(Skills|MCP|KB)

SessionContextPanel ──▶ 仍可读写 USER/AGENTS（兼容）
SuperAgent MemoryMiddleware ──▶ 不变挂载点；L2 不进 bootstrap sources
```

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| 双入口改记忆导致用户困惑 | 设置标「主入口」；面板记忆节点旁「在设置中打开」 |
| Cron 多实例重复跑 | DB 锁 / 单调度 leader |
| Telegram token 泄漏进记忆文件 | prompt 禁止 + 通道配置隔离 + 审计测试 |
| L2 未实现但规格已写 | tasks 分期：P0 设置+L0/L1+cron 模型；L2/Telegram 投递标后续任务 |
| 设置页过重 | section 懒加载；capabilities 只深链 |

## Migration Plan

1. 部署后端 API + 表迁移（cron/channels）。
2. 前端上线设置路由；侧栏入口；旧面板行为不变。
3. `ensure_user_memory_files` 不变；可选创建空 `memory/` 目录。
4. 回滚：隐藏前端入口即可；DB 表可保留；无强制数据迁移。

## Related Changes

- **`unify-run-delivery`**：RunEvent Fan-out、PersistSink、ChannelAdapter SPI；通道**真收发**依赖该 change，本 change 只做配置/绑定面。
- **`extract-agent-runtime-harness`**：`AgentRunService` / Runtime 边界；Delivery 层消费其事件。

## Open Questions

1. Cron 执行产物是否写入用户可见「运行历史」表（建议要，最小 last_status 不够产品化）？
2. Telegram 入站是否强制绑定已有 web session，还是为通道创建隐式 session？
3. L2 日记是否由独立 middleware 在 session end 自动追加，还是仅 Agent 工具写入？（建议后者先，自动摘要另 change）
