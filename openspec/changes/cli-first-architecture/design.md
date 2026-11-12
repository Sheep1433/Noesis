# 设计：CLI 先行架构倒置

## 1. 架构总形

```
┌─ Noesis Core（agent 进程）────────────────────────────
│  SuperAgent 装配（factory/栈/skills/子 Agent/记忆/HITL）
│  双档 profile：轻（无工作目录）/ 重（--project 全工具+沙箱）
│  能力开关：KB / web / 记忆（正交，按会话）
│  权限层 + SQLite 状态 + SSE 投影管道
├─ 头协议（M4 冻结）：同一事件词汇，多传输编码 ─────────
│  ① CLI TUI（第一个头，薄客户端纪律）  ② -p headless
│  ③ --web（浏览器头，复用 Vue 前端本地档）
│  ④ 远期：桌面端（Tauri 等）——协议的衍生品
└─ 向上：多智能体 = 从核心派生子会话（M5）
```

组合根从两个（平台 / CLI 各自装配）收敛为一个。形态差异降为 profile 参数（`build_noesis_middleware` 的 profile 机制已在做这件事）；能力差异降为工具面勾选。**核心不感知有几个头**；头不直连 agent 内部（见 §3 纪律）。

参照系：dcode 的实际架构（agent server + textual TUI + headless + ACP 客户端）验证了这一形状在 Python + deepagents 底座上成立；Claude Code 的演化路径（CLI 内核 → daemon → IDE → web）同构。

## 2. 双档 profile 与能力开关

| | 轻档 | 重档 |
|---|---|---|
| 工作目录 / 沙箱 | 无 | `--project` 目录，local_shell 直通（agent 路径 = 宿主机路径） |
| 工具 | KB（可选）/ web（可选）/ 记忆（可选） | 轻档全部 + filesystem + skills + 子 Agent |
| 每轮成本 | 单 LLM 循环 | 沙箱 + 全中间件栈 |
| CLI 入口 | `noesis chat`（现状延续） | `noesis chat --project <dir>` |
| 通道 / 定时 | Telegram 快问快答 | 日报类深任务（生产现状即此档） |

能力开关按会话持久化（勾选即生效），KB 开关复用 `super_agent.py` 现成的 `kb_search_enabled` + `kb_collections` 机制，升级为显式设计。意图路由从三分类退化为档位选择：显式指定 + 兜底规则（带附件 / 提及工作目录 → 重档）。

## 3. 头协议与桌面衍生纪律（本次新增的高要求）

**CLI 按桌面衍生标准设计，即"CLI 本身必须是薄头"**：

- 核心与头之间只有一个会话接口（`HeadSession`：发消息、订阅事件流、请求确认应答、控制停止），CLI 的交互层（textual TUI / rich 渲染）只消费这个接口。任何"CLI 直接 import agent 内部"的走线都是违规——这是未来桌面端（Tauri/Electron 包一个 WebView 头）与 `--web` 复用同一交互层的前提。
- **事件词汇只有一份**：现有 SSE wire 帧词汇表（`run-snapshot` / `text-*` / `tool-input-*` / `hitl-required` / `run.finished` 等，权威在 chat-streaming.md §4.2b）就是头协议的事件层——不另发明词汇。传输层按头编码：进程内（CLI 直连）、HTTP/SSE（web head）、远期 WebSocket / stdio（桌面、外部编辑器）。
- **协议版本化**：头与核心协商版本，词汇表演进按 deprecation 走（与 SSE 契约测试同一套钉法）。
- **ACP 评估（M4）**：Agent Client Protocol 是编辑器/桌面 ⇋ agent 后端的行业标准（Zed 主推，上游 deepagents 已有 `libs/acp` 连接器，dcode 一句命令可作为 ACP server 暴露）。M4 冻结协议前评估：若 Noesis 事件词汇能映射到 ACP 方法集，则桌面/编辑器衍生免费获得生态位；映射不了（multipart parts / 通道语义超出 ACP 范围的风险），保留自有最小协议 + ACP 作为外接适配层。**不预先绑定**。

## 4. 权限层（承接旧草案，吸收 dcode 验证）

两层挂载：路径边界 `ProjectBoundaryBackend`（`SandboxBackendProtocol` 包装，框架无关拦截器，写/删在项目根外可解释拒绝，读放行）；操作分级走 HITL `interrupt_on` 扩展（参数级谓词：execute 命令 / 网络 URL / 写路径模式），全局三档 `manual / auto / yolo`（yolo 需显式确认进入）。授权记忆按「工具 + 参数模式」落 `.noesis/local.db`。与 ReadBeforeWrite（没读过的文件拒绝改）衔接：先边界/分级，再内容门禁。

## 5. 本地持久化（承接旧草案）

`AsyncSqliteSaver`（langgraph-checkpoint-sqlite 新增可选依赖）+ `.noesis/local.db` 四张轻表（session / message / permission_grant / todo_item），不经 ChatService / RunService / pg_manager。生产细节对齐 dcode：aiosqlite busy_timeout、状态目录硬化、会话格式版本化；同项目双 CLI 进程加会话锁。

## 6. 里程碑

- **M0 形态收窄**（平台侧先行减面）：FaultOperationAgent 删除（存量只读映射，同 TEST_CASE_QA 先例）；意图三分类退役；qa_type 两值路由。KB / MCP / web 工具面不动（已聚合在 SuperAgent）。
- **M1 核心能跑**：`HeadSession` 接口 + 核心进程收敛（轻/重两档）+ `--project`（local_shell 直通、路径直通、状态落项目 `.noesis/`）+ CLI 薄头改造。验收：裸仓库零配置跑通。
- **M2 权限就位**（碰真实项目的前提）：§4 全量。验收：项目外必拦、拒绝不卡死。
- **M3 持久化**：§5 全量。验收：重启续聊、多项目隔离。
- **M4 头协议冻结 + web head**：事件词汇传输编码与版本协商冻结（含 ACP 评估结论）；`--web` 本地起服务、复用 SSE 管道与 Vue 前端本地档（无 auth）。验收：终端与浏览器看到同一条会话。
- **M5 多智能体派生**：从核心派生子会话（委派原语 + 交付回调，复用 P0 outbox 的交付语义若已建；单进程下不需要认领/fencing）。
- M2 完成前不进每日自用（TODO P2 顺序约束）。

## 7. 被否方案

- **换 TypeScript**：被否——deepagents 无 JS 实现，切语言等于推翻"底座仍为 langchain/deepagents"的前提，把架构倒置（资产保留）变成全量重写（资产清零）；TUI（textual）/分发（uv + curl 脚本）/启动（懒加载 + 3.13+）的 Python 对应解齐备。
- **fork / 基于 dcode**：被否——dcode 是产品不是底座，其 hooks/extensions/plugins 体系与 Noesis middleware 栈双轨冲突；吸收其验证设计（三档权限、会话锁、SQLite 细节、client/server 分离形状）而非代码。
- **保留三形态**：被否——故障运维明确不用；通用 QA 与 SuperAgent 的真实差异是工具面与工作目录（profile 可表达），不是两个 agent。
- **大爆炸式重写**：被否——M0-M5 每步独立可交付，平台在重构期间持续可用（M0 只减面不动核心）。
- **CLI 内嵌 agent 直连（非薄头）**：被否——桌面衍生要求头可替换；直连把 TUI 焊死在核心上，`--web` 与桌面端都要重造交互层。
- **先做网页形态本地化（app.py SQLite 化）**：被否——156 处 pg_manager 直引无 repository 边界，工程量差 CLI 路线一个数量级。
- **AgentPathBackend 内嵌权限**、**新造中断机制**、**复用 ChatService**：承接旧草案裁决，理由不变。
