# 提案：CLI 先行架构倒置——SuperAgent 核心进程 + 多头客户端

## Why

Noesis 当前以网页平台为主产品、CLI 为卫星，两套组合根靠纪律防分裂；SuperAgent 表面呈现为"浏览器里的 code agent"，而 owner 的日常工具是更好的 code agent（dogfood 悖论）。产品裁决：**架构倒置**——agent 运行时收敛为单一核心进程，CLI / 浏览器 / headless / 未来的桌面端都是这个核心的头（head）；**形态收窄**——FAULT_OPERATION_QA 删除，COMMON_QA 与 SUPER_AGENT 合并为同一核心的轻/重两档 profile；KB / web / 记忆为与形态正交的能力开关。底座不变（Python + deepagents SDK，参照 dcode 已验证的同形状架构：agent server + TUI/headless/ACP 客户端）。

代码核验支撑：SuperAgent 已聚合 KB / MCP / web 工具（形态收窄零能力损失）；通道与定时任务本就只跑 SUPER_AGENT_QA（自动化不受影响）；SSE 投影管道（bridge → mapper → builder）是 core 侧事件管道、不依赖 PG（web head 可原样复用）；`noesis-cli` 组合根已验证不碰 Postgres。

## What Changes

- **核心进程（Noesis Core）**：SuperAgent 装配（factory/栈/skills/子 Agent/记忆/权限）+ SQLite 本地状态 + SSE 投影管道，收敛为唯一组合根。
- **双档 profile**：轻档（无工作目录，检索 + 记忆；`noesis chat`）与重档（`--project <dir>` 全工具 + 沙箱 + 工作目录）。能力开关（KB / web / 记忆）与档位正交，两会话按勾选生效。
- **头（heads）**：CLI TUI（第一个头，按"薄客户端"纪律构建——只经头协议与会话接口对话，不直连 agent 内部）、`-p` headless、`--web` 浏览器头（复用 SSE 事件词汇 + 现有 Vue 前端本地档）、远期桌面端（Tauri 等）——全部说同一种协议。
- **头协议（M4 冻结为稳定边界）**：同一事件词汇（现有 SSE wire 帧词汇表）、多传输编码（本地 stdio/进程内、HTTP/SSE、远期 WebSocket）。桌面衍生能力 = 协议纪律的直接产物；ACP（Agent Client Protocol，上游 deepagents 已有 `libs/acp`，dcode 支持 `--acp` 暴露）作为协议对齐候选在 M4 评估。
- **形态收窄（M0）**：FAULT_OPERATION_QA 删除（存量会话只读映射，同 TEST_CASE_QA 先例）；qa_type 路由收敛为轻/重两值；GeneralQAAgent 与 SuperAgent 构建器合并为一个核心（`COMMON_QA` 语义映射到轻档）。
- **平台命运**：网页多用户平台降级为可选 profile，随 M0 收窄后按使用意愿决定投入；通道交付与定时任务继续服务重档（生产在用）。

## Capabilities

### New Capabilities

- `agent-heads`：核心/头分离契约——头协议（事件词汇 + 传输编码 + 版本化）、双档 profile、能力开关正交模型、桌面衍生纪律。
- `local-project-mode`：本地会话的目录边界、权限分级与授权记忆、SQLite 持久化。

### Modified Capabilities

- `agent-profiles`：qa_type 路由表从三形态收敛为两档；FAULT_OPERATION_QA 按废弃类型处置；共享工厂条目改为"一个核心构建器 + 档位参数"。
- `platform-chat`：qa_type 路由两值化；可靠 Web Agent Run 适用范围剔除 FAULT_OPERATION_QA。
- `agent-harness` / `agent-tool-failure-handling` / `agent-delivery` / `user-platform`：适用范围与矩阵中的 FAULT_OPERATION_QA 引用同步收窄。

## Impact

- 新增：头协议层（事件词汇的传输编码与版本声明）、`ProjectBoundaryBackend`、权限三档（manual/auto/yolo）、`.noesis/local.db` + SQLite checkpointer、CLI `--project` / `--web` / serve 模式。
- 删除（M0）：FaultOperationAgent 及其编排分支、意图三分类、形态选择 UI；平台 qa_type 收窄。
- 依赖：新增 `langgraph-checkpoint-sqlite`（可选组）。
- 兼容：`noesis chat` 无参数行为不变；`COMMON_QA` 存量会话只读映射到轻档；平台存量数据不动。
- 验收：裸仓库零配置跑通（M1）；项目外写必拦、拒绝后会话不卡死（M2）；重启续聊、多项目隔离（M3）；浏览器与终端看到同一条会话（M4）；最终标准"我愿意拿它处理真实文件"。
