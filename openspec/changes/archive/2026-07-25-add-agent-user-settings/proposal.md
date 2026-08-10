## Why

侧栏用户区仅有主题与退出，画像/偏好（`USER.md` / `AGENTS.md`）埋在会话上下文文件树中，发现成本高；定时任务与 Telegram 等通讯通道尚无产品配置入口。需要一等公民的「个人与 Agent 设置」壳，把人审阅的记忆、cron 自动化与通道对接收进同一信息架构，并为后续通道扩展留位——**不含** slash（slash 属于会话输入面，不在本设置页配置）。

## What Changes

- 新增前端「个人与 Agent 设置」页（或抽屉），入口为侧栏头像；按 section 导航（概览、画像、记忆与偏好、能力深链、自动化、通讯通道、账号）。
- 画像与记忆以设置页为**主编辑入口**；会话上下文面板可保留文件可见性或跳转，但不再是唯一入口。
- 记忆分层产品化：L0 `USER.md`、L1 `AGENTS.md` 可审阅编辑；L2 日记层（`memory/YYYY-MM-DD.md`）与检索在规格中定义，实现可分阶段。
- 新增用户级 **定时任务（cron）** 的配置与列表 UI，任务持久、可启停、可绑定 Agent/`qa_type`，归档/删除会话时处理绑定关系。
- 新增 **通讯通道** 配置面：首期规格覆盖 Telegram（及可扩展通道模型）；连接凭据与开关**仅人类经设置/API 写入**，Agent 运行时不可改。
- **非目标**：slash 命令注册/配置 UI；Memory Hub 级图谱/健康度大盘（可后续独立 change）；OpenClaw 式多 bootstrap 文件（SOUL/IDENTITY 等）。

## Capabilities

### New Capabilities

- `agent-user-settings`：个人与 Agent 设置壳、导航 IA、画像/记忆编辑面、能力深链、与 cron/通道 section 的挂载契约。
- `agent-scheduled-tasks`：用户级定时任务（cron）的数据模型、API、调度语义、与会话/Agent 的绑定及设置页列表。
- `agent-messaging-channels`：外部通讯通道（Telegram 等）的连接配置、启停、投递边界；设置页「通讯」section；密钥仅人写。

### Modified Capabilities

- `agent-user-memory`：明确设置页为记忆主编辑入口；补充 L2 日记路径与注入/检索边界（与现有 `/memory/` 对齐）。
- `chat-session-context-panel`：上下文面板对 `USER.md`/`AGENTS.md` 的定位改为可选编辑或跳转设置，避免双主入口歧义。

## Impact

- **前端**：新路由/视图（如 `views/settings/`）、侧栏入口；复用现有 workspace/memory 读写 API 或新增用户级 memory/cron/channels API。
- **后端**：`/api/user/...`（或等价前缀）下的 memory、cron、channels **配置** API；调度组件（进程内或独立 worker）；通道**配置与配对**持久化（**不含** webhook/long-poll 真收发，见 `unify-run-delivery`）。
- **数据**：`.data/users/{uid}/` 下记忆文件；cron 作业存储（DB 表或用户目录 JSON，design 定夺）；通道凭据与配对存用户配置（禁止进 Agent 可写记忆文件）。
- **兼容**：不破坏现有 chat/SSE；会话面板 API 保持可用；鉴权与 `user-auth` 一致（Cookie Session + CSRF，**非** JWT）。
- **不含**：composer slash、Skills/MCP 整页搬迁（仅深链）；通道消息运行时 Adapter。
