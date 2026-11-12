# Tasks: CLI 先行架构倒置

## M0 形态收窄（平台减面，先行）

- [ ] 0.1 删除 FaultOperationAgent 及编排分支：qa_service 路由、意图分类三值、`FAULT_OPERATION_QA` 构建入口；存量会话只读映射（同 TEST_CASE_QA 处置），契约测试同步
- [ ] 0.2 qa_type 收窄两值：`COMMON_QA` 语义映射轻档、`SUPER_AGENT_QA` 重档；平台前端形态选择 UI 简化为档位 + 兜底规则（带附件/提及工作目录 → 重档）；通道配置面 `_ALLOWED_QA`（channel_run_service）同步剔除废弃值
- [ ] 0.3 平台回归：`uv run pytest tests/ -q` + `api_contract` 全绿；通道与定时任务行为不变（本就只跑 SUPER_AGENT_QA）
- [ ] 0.4 常驻文档同步：`AGENTS.md` qa_type 表与 Agent 表（删 FaultOperationAgent 行）、`README.md` 故障运维场景描述；`docs/engineering/` 中涉及三形态的表述

## M1 核心能跑（HeadSession + 双档 + --project）

- [ ] 1.1 `HeadSession` 会话接口：发消息、订阅事件流（SSE 词汇）、确认应答、停止——核心对头的唯一出口，CLI 交互层只消费此接口
- [ ] 1.2 核心进程收敛：轻/重两档 profile 装配（GeneralQAAgent 与 SuperAgent 构建器合并为一个核心 + 档位参数）；能力开关（KB/web/记忆）按会话传入
- [ ] 1.3 `--project` 参数：`local_shell` 后端 + `AgentPathBackend(strip_root=None, canonicalize=False)` 路径直通；`NOESIS_DATA_DIR=<project>/.noesis`；无 `--project` 时维持轻档现状
- [ ] 1.4 M1 验收：裸 git 仓库零配置跑通对话 + 读写 + execute（一次性测试仓库，读写尚无边界）

## M2 权限就位（碰真实项目的前提）

- [ ] 2.1 `ProjectBoundaryBackend`（框架无关拦截器，参照 dcode RepositoryBounds）：写/删项目外可解释拒绝、读放行；单测覆盖 `..` / 符号链接 / 绝对路径逃逸
- [ ] 2.2 HITL 参数谓词扩展 + 全局三档 `manual / auto / yolo`（yolo 显式确认进入）；缺省 = 项目外/执行/网络询问
- [ ] 2.3 CLI 确认 UI（终端三选：本次/永久/拒绝）；拒绝后返回可解释错误、会话不中断
- [ ] 2.4 授权记忆：`.noesis/local.db` permission_grant 表；`--yes-all` 与配置文件预置放行集
- [ ] 2.5 M2 验收：改真实仓库时项目外必拦、拒绝后继续工作不卡死

## M3 持久化（用得久）

- [ ] 3.1 `langgraph-checkpoint-sqlite` 可选依赖 + `AsyncSqliteSaver`（`.noesis/checkpoint.db`）；aiosqlite busy_timeout、状态目录硬化、会话格式版本化（dcode 验证细节）
- [ ] 3.2 `local.db` 轻存储：session / message / todo_item 表 + CLI repository（终态先比较再写入）；会话列表 / 历史加载 / `--session` 续聊
- [ ] 3.3 会话锁：同项目第二个 CLI 进程可解释报错（文件锁）
- [ ] 3.4 记忆按项目隔离：memory store 落项目内 `.noesis/`，多项目不串扰回归
- [ ] 3.5 M3 验收：重启续聊（checkpoint + 消息一致）；两项目并行不串

## M4 头协议冻结 + web head

- [ ] 4.1 头协议冻结：事件词汇（SSE 词汇表）× 传输编码（进程内 / HTTP-SSE）× 版本协商；契约测试钉住（与 SSE 契约同一套钉法）
- [ ] 4.2 ACP 评估结论：Noesis 事件词汇 ↔ ACP 方法集映射可行性；可行则加 ACP 适配层（桌面/编辑器生态位），不可行则记录差异并保留自有协议
- [ ] 4.3 `--web` 头：本地起服务（无 auth）+ 复用 SSE 投影管道与 Vue 前端本地档；前端本地适配层须点名处理：authHttp 的 CSRF/401 跳转、`@/api/mcp` 与 `@/api/skills` 依赖、chat.vue 的平台端点面（M4 最大实工量）
- [ ] 4.4 M4 验收：终端与浏览器打开同一条会话，事件流一致

## M5 多智能体派生

- [ ] 5.1 委派原语：从核心会话派生子会话（工具面挂核心、交付回调进父会话）；单进程交付语义（复用 P0 outbox 语义若已建，否则轻量回调 + 通知）
- [ ] 5.2 M5 验收：重档会话中派生子任务并行执行、终态通知回流父会话

## 收尾

- [ ] 6.1 平台零影响回归（M0 后每里程碑）；openspec 归档时把 design 判断依据提炼为决策记录（含本提案全部被否方案）
- [ ] 6.2 自用素材：M2 后开始每日自用（权限就位是碰真实项目的前提），bug 记 `docs/bug/`；每周回顾一次「哪个瞬间想退回 dcode / Claude Code」，那个瞬间即下一个修复点
