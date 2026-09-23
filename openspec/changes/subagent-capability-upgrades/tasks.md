# Tasks: 子代理工具能力升级

## 1. 子代理角色体系

- [ ] 1.1 `agents/background/subagent/roles.py`：`SubagentRole` 扩展 `tool_allowlist: tuple[str, ...] | None`（None=继承 general 全集）、`prompt_profile: str | None`、`memory_read_only: bool = True`；`general` 角色声明不变（行为零变化）
- [ ] 1.2 内置 `read-only` 角色：工具白名单 = {web_search, web_fetch, search_memory（文件变体，`build_memory_tools(user_id)` 即是，loop 安全）, read_file, ls, glob, grep}——**不含** search_sessions / search_history（DB 查询，loop-bound）与一切写/execute/task 族；`_bg_worker_factory` 按角色过滤 `worker_tools` 并跳过 `replace_execute_tool`
- [ ] 1.3 角色装配：`_bg_worker_factory` 消费角色声明（tool_allowlist 过滤 + prompt_profile 选 prompt）；`subagent_type` 参数与 registry 分发已存在，仅核对未知类型的错误文案
- [ ] 1.4 不变式钉住：后台角色一律 `create_agent_backend(memory_read_only=True)`——记忆可写角色不存在（无 HITL 通道 = 无审批 = 不得写）；单测断言任何角色派单的 /memory 路由只读
- [ ] 1.5 单测：read-only 派单的工具面断言（无 execute/write_file/search_memory）；未知 subagent_type 拒绝；general 行为零变化回归

## 2. Steering（运行中引导）

- [ ] 2.1 投递模式由消费端判定：命令消费 `bg_task_deliver` 时按执行器实况分流（steered / queued / resumed）；`accept_message` 受理端维持返回 accepted，不做跨实例状态猜测
- [ ] 2.2 **新建注入通道**：worker 模型调用包装器（`AsyncSubagentToolsMiddleware` 的 `wrap_model_call`）每次模型调用前检查该任务收件箱（无消息即零开销）；命令消费侧把 steered 消息经跨 loop 投递桥（`loop.call_soon_threadsafe` 类语义）写入收件箱——executor 在隔离 loop、命令消费在其宿主 loop，注入必须跨 loop
- [ ] 2.3 边界语义：注入消息只影响当前 turn 之后的模型上下文，不得写入已完成 checkpoint 的投影；turn 恰好结束 → 降级 queued（消息进 pending 行，下一轮照常）；通知中间件对 steered 消息不产生重复注入
- [ ] 2.4 单测：注入时机与投影边界；turn 结束竞态降级 queued；跨 loop 投递（隔离 loop ↔ 宿主 loop）的到达性与顺序；resumed 路径回归

## 3. 同步子任务自动转后台

- [ ] 3.1 同步 `task` 工具包装超时守卫（`autoBackgroundMs` 默认 60_000，config 化）：超时即**中止同步执行**（graph 内子代理无法迁出，部分进度丢弃——如实在返回体告知模型）并以同 prompt 走 `SubagentSessionService.launch` 异步受理，返回 agentId + description + 「已转后台，经 check_async_task 跟进」
- [ ] 3.2 竞态守卫：中止与异步受理的窗口内子代理可能恰好完成——以子会话/run 存在性幂等，完成后转后台返回体如实报「已完成，直接取结果」
- [ ] 3.3 单测：阈值触发转后台（中止发生、异步子会话建立）、返回体形状含进度丢弃说明、窗口内完成的不重复执行；阈值内正常完成路径回归

## 4. 文档与验收

- [ ] 4.1 `docs/engineering/subagent-sessions.md` 补三能力说明；工具描述文案与实现同步
- [ ] 4.2 端到端验证：read-only 派单跑调研任务、steered 纠偏一次、同步超时转后台——全链路（契约测试不替代验证）
