# Tasks: 子代理工具能力升级

## 1. 子代理角色体系

- [ ] 1.1 `agents/background/subagent/roles.py`：`SubagentRole` 扩展 `tool_allowlist: tuple[str, ...] | None`（None=继承 general 全集）、`prompt_profile: str | None`；不设 memory_read_only 字段——后台角色记忆恒只读是平台不变式（见 1.4），恒 True 的 bool 是死配置；`general` 角色声明不变（行为零变化）
- [ ] 1.2 内置 `read-only` 角色：非文件系统工具白名单 = {web_search, web_fetch, search_memory（文件变体，loop 安全）, search_sessions, search_history（依赖 5.3 桥接解锁）}；文件系统工具（read_file/ls/glob/grep/write_file/edit_file/execute）由 FilesystemMiddleware 生成、不在 `worker_tools` 清单里——read-only 角色的文件面裁剪走中间件级过滤（`_bg_worker_factory` 的 filesystem hook 新增按角色剔除 write_file/edit_file/execute，参照既有 `guard_worker_filesystem_tools` 的工具定位方式；5.3 未落地前白名单先不含两个检索工具）
- [ ] 1.3 角色装配：`_bg_worker_factory` 消费角色声明（tool_allowlist 过滤非 fs 工具 + prompt_profile 选 prompt + fs 面按 1.2 剔除）；`subagent_type` 参数与 registry 分发已存在，仅核对未知类型的错误文案
- [ ] 1.4 不变式钉住：后台角色一律 `create_agent_backend(memory_read_only=True)`——记忆可写角色不存在（无 HITL 通道 = 无审批 = 不得写）；单测断言任何角色派单的 /memory 路由只读
- [ ] 1.5 单测：read-only 派单的工具面断言（无 execute / write_file / edit_file / task 族；有 search_memory / search_sessions——与 1.2 白名单及 5.3 进度一致）；未知 subagent_type 拒绝；general 行为零变化回归

## 2. Steering（运行中引导）

- [ ] 2.1 投递模式由消费端判定：命令消费 `bg_task_deliver` 时按执行器实况分流（steered / queued / resumed）；`accept_message` 受理端维持返回 accepted，不做跨实例状态猜测
- [ ] 2.2 **新建注入通道**：worker 栈新增收件箱中间件（注意：worker 编译不含 `AsyncSubagentToolsMiddleware`——那属于父 Agent 且 worker 禁递归），其 `wrap_model_call` 在每次模型调用前检查该任务收件箱（无消息即零开销）；命令消费侧把 steered 消息经跨 loop 投递桥（`loop.call_soon_threadsafe` 类语义）写入收件箱——executor 在隔离 loop、命令消费在其宿主 loop，注入必须跨 loop
- [ ] 2.3 边界语义：注入消息只影响当前 turn 之后的模型上下文，不得写入已完成 checkpoint 的投影；turn 恰好结束 → 降级 queued（消息进 pending 行，下一轮照常）；通知中间件对 steered 消息不产生重复注入
- [ ] 2.4 steered 消息的转录表示：受理时写入的 pending 行在注入成功时翻转为正式 user 消息行（子会话详情流可见，时序标注注入发生在第 N 步后）——用户在详情页必须能看到自己那条纠偏消息，而不是它消失只留下模型行为变化
- [ ] 2.5 单测：注入时机与投影边界；turn 结束竞态降级 queued；跨 loop 投递（隔离 loop ↔ 宿主 loop）的到达性与顺序；resumed 路径回归

## 3. 删除同步子 Agent 调试残留

- [ ] 3.1 `super_agent.py`：删除 `sync_subagents` 清单与 `create_noesis_agent(subagents=...)` 挂载（super_agent 场景的同步 task 是早期调试通道；实际派单唯一入口是 `start_async_task`，其 `run_in_background=False` 前台模式已内建 shield 超时自动转后台，能力覆盖 task 工具且多出子会话/投影/通知三样）——`sync_subagent_tools` 与编译配置一并清理；注意 `sync_subagent_model` 兼作主 Agent 的 `model=` 参数，删除后回退 `get_llm(model_id)`（等价）；`fault_operation.py` 的同步 general-purpose 是正式功能，**保留**
- [ ] 3.2 配套清理：`services/mention_resolve_service.py` 的 SUPER_AGENT_QA mention 指引仍在引导模型"优先使用 `task` 且 subagent_type=task-worker"——删除 task 后成幽灵指引，改为引导 `start_async_task`（顺带修正过时的 `task-worker` 角色名为现行注册角色）；无测试钉住同步挂载（test_noesis_stack_assembly 用自建 fixture 测 stack 通用能力，且 `fault_operation.py:132` 仍用 subagents 参数，stack 的 subagents 支持不删），无需测试更新
## 4. 文档与验收

- [ ] 4.1 `docs/engineering/subagent-sessions.md` 补三能力说明；工具描述文案与实现同步
- [ ] 4.2 端到端验证：read-only 派单跑调研任务、steered 纠偏一次、同步超时转后台——全链路（契约测试不替代验证）

## 5. 会话检索增强（时间过滤 + worker 桥接）

- [ ] 5.1 `repositories/history_search.py`：`search_session_history` / `search_user_sessions` 增加 `created_at_from` / `created_at_to`（ms 时间戳，闭区间，None=不限）参数与 SQL 范围条件；检索形态与滚动形态均生效
- [ ] 5.2 `agents/tools/history_search_tool.py`：工具参数新增 `created_at_from` / `created_at_to`（ISO 8601 日期字符串，模型友好），工具层转 ms 后下发；解析失败返回明确错误而非静默忽略
- [ ] 5.3 worker 桥接：两个检索工具的 DB 协程经 `run_on_main_loop` 桥到主 loop（主 loop 未注册时回退当前 loop 直连，同 `_db_on_main_loop` 形态）；`super_agent.py` 的 `_loop_bound_tools` 移除 search_history / search_sessions（收敛为只剩 ask_user）；`worker_tools` 装配恢复两个检索工具
- [ ] 5.4 单测：时间过滤命中/边界/时区转换；桥接路径在无主 loop 时回退直连；worker 工具面断言更新（read-only 与 general 均含两个检索工具）
