# Proposal: 子代理工具能力升级（对照 ZCode 源码差距分析）

## Why

对本地 ZCode 源码（`apps/zcode-cli/packages/core` 工具面，26 个模型工具）做了一次对照分析：Noesis 的异步子代理链路（`start_async_task` 受理 → 命令表 → executor 消费 → 投影落库 → `BgNotifyMiddleware` 通知注入）与 ZCode 的 `Agent` + 通知注入架构**基本同构**，但存在三处能力差距。这些差距都有真实场景支撑，且 Noesis 的既有基础设施（`SubagentRegistry` 空壳、命令表投递通道）已经为它们预留了位置——属于"把预留的口子填上"，不是引入新架构。

对齐过程中明确的**设计约束**（来自 ZCode 的教训，见 design.md）：

- **通知优先，不做阻塞式取输出**。ZCode 已废弃 `TaskOutput`（`block=true` 最长阻塞 600s）：通知机制使阻塞冗余；对 local_agent 任务其 `.output` 文件是完整子代理会话转录（JSONL），读取即撑爆父上下文。Noesis 的投影落库 + 通知注入已是正确形态，禁止引入阻塞式取输出工具。
- **输出通道以投影落库为准**，不学 ZCode 的输出文件 + Read 模式（该模式服务其单进程文件生态；Noesis 的多实例权威存储是 PG）。

## What Changes

三能力按依赖排序：

- **子代理角色体系**（差距最大）：`SubagentRegistry` 现为多角色设计但只注册 `general`。补齐：`read-only` 内置角色（工具白名单 = web/文件读，无任何写与 execute——对应 ZCode 的 Explore，用于调研类派单）；角色声明扩展 `tool_allowlist` / `prompt_profile` 字段。**约束**：worker 跑在隔离 loop，绑定主 loop 连接池的 DB 工具不可用——注意约束是**变体级**而非工具级：`_loop_bound_tools` 按名字过滤后 `build_memory_tools(user_id)` 会重加文件变体的 `search_memory`（纯文件检索，不碰 pg_manager，现役 worker 工具），真正不可用的是 DB 查询类（search_sessions / search_history）；**不变式**：后台角色无 HITL 通道（不挂 interrupt_on），因此记忆对一切后台角色恒只读，不存在"可写记忆的角色"。`start_async_task` 的 `subagent_type` 分发管道已存在（registry.get 分发 + 类型清单注入 system prompt），本变更只做角色注册与按角色装配，不动参数面。用户自定义角色（ZCode 的 Markdown profile 对应物）列为后续，不在本期。
- **Steering（运行中引导）**：`send_message` 从单态排队升级为 ZCode 三态语义——`steered`（子代理 turn 进行中把消息注入当前模型调用，用户"边跑边纠偏"）、`queued`（既有行为）、`resumed`（可续终态任务冷恢复，已有）。投递模式由**消费端判定**（命令消费时按执行器实况分流；受理端无法跨实例可靠知道 turn 状态，维持返回 accepted 不变）。需要**新建注入通道**（非现成设施）：worker 的模型调用包装器在每次模型调用前检查任务收件箱，命令消费侧经跨 loop 投递桥写入收件箱；注入失败（turn 恰好结束）降级为排队语义。
- **同步子任务自动转后台**：同步 `task` 工具阻塞超过阈值（`autoBackgroundMs`，默认 60s）时**中止同步执行**（子代理跑在父 run 的 graph 内，无中途迁出路径，已跑的部分进度丢弃）并以同 prompt 重新异步受理（子会话 + 命令照常建立），向模型返回 agentId 与"后续经 check_async_task 跟进"——消除"同步子任务卡住父 Agent 整个 turn"的问题。阈值权衡：越小浪费越少但误转越多（60s 内可完成的任务被强拆）。

**明确不对齐**（理念分歧，记录防止反复）：per-call `model_id` 覆盖（ZCode 刻意移除，Noesis 保留但仅 SuperAgent 派单可用）；并行数上限（Noesis 防御性限流保留，ZCode 交用户）；输出文件模式（见设计约束）。

## Impact

- **后端**：`agents/background/subagent/roles.py`（角色字段扩展）、`tools.py`（按角色装配 worker 工具面）、`executor.py`（steering 消费 + 注入通道 + 同步转后台）、`services/subagent_session_service.py`（accept_message 消费模式透传）。
- **前端**：任务面板条目展示 steered/queued 投递状态（可选）。
- **数据库**：无 schema 变更（pending 行与命令表现有字段够用；steering 若需持久化注入记录，复用 `bg_task_deliver` 命令 payload）。
- **与 worker-role-split 的关系**：无重叠（该变更管执行面基础设施；本变更管工具面语义），steering 的 executor 消费端实现需基于其 Phase 1 后的代码合入。
- **风险**：steering 的 turn 注入点在隔离 loop 内，注入时序与 checkpoint 边界的交互需要测试覆盖（注入消息不得落进已完成 checkpoint 的投影）。
