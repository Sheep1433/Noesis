# Tasks: subagent 类型分发与任务运行时身份澄清

按 design.md 迁移顺序分四组，每组可独立提交与回滚。

## 1. 注册表与 descriptor 落库（零行为变化）

- [ ] 1.1 新建 `agents/subagents/registry.py`：`SubagentRole`（frozen dataclass：name / description / worker_factory / interrupt_on / model_id 模型绑定）与 `SubagentRegistry`（装配期注册、重名 ValueError、按 name 取 role、类型清单导出、生效模型解析：绑定值或父模型）
- [ ] 1.2 `super_agent.py`：`_bg_worker_factory` 配方原样搬入 `general` spec 并注册（工具集、backend、prompt、interrupt_on 逐项等价）；装配处集中断言所有 spec 的 worker 工具集不含 start_task / check_task / cancel_task / list_tasks / send_message
- [ ] 1.3 `SubagentSessionService.launch()` 增 `subagent_type` 参数，写 child session `extra["subagent"] = {"version": 1, "type": <type>, "model": <生效模型>}`（生效模型 = 类型绑定或父模型，与 `extra.model_id` 同源）；`super_agent._create_child_session` 透传
- [ ] 1.4 单测：重名注册抛异常；descriptor 写入与读取校验（含 model 字段）；模型绑定解析（绑定值 / 沿用父模型）；worker 工具集断言
- [ ] 1.5 验证：`uv run pytest tests/ -q` 全绿 + 起服务跑一轮真实委派，确认 child session 落库带 descriptor

## 2. NoesisSubagentMiddleware（工具面收编 + graph state）

- [ ] 2.1 新建 `agents/middlewares/subagent_tools_middleware.py`：`SubagentTasksState`（`bg_tasks` 合并 reducer）+ `BgTaskIdentity` TypedDict
- [ ] 2.2 五个工具（start / check / cancel / send_message / list_tasks）从 `subagents/tools.py` 迁入 middleware 构造函数，闭包捕获 registry / executor / service 回调；`super_agent.py` 的 `tools.extend(build_background_task_tools(...))` 段退役，middleware 挂入 `build_noesis_middleware` 栈（SUBAGENT profile 不挂）
- [ ] 2.3 `start_task` 增**必填** `subagent_type` 参数（schema 枚举自注册表，缺失即 schema 校验拒绝）；未注册类型返回含可用清单的错误文本且不建 child session
- [ ] 2.4 工具返回值改 `Command(update={...})`：回 ToolMessage（文本与迁移前逐字一致）同时写 `bg_tasks`；`check_task` / `list_tasks` 状态永远实时查执行器（miss 落 DB），不信 state 快照
- [ ] 2.5 `wrap_model_call` 注入类型清单（`- name: description` 逐行）
- [ ] 2.6 契约测试：五个工具返回文本与迁移前逐字对比；state 内出现 identity；state 快照过期时 check_task 返回实时终态；未知类型拒绝且无副作用；`start_task` schema 不含模型参数；压缩不丢 `bg_tasks`（对齐 compaction middleware 测试形态）
- [ ] 2.7 验证：全量测试绿 + 真实会话委派一轮（后台 + 前台等待 + followup + 审批各一），SSE 任务卡行为不回归

## 3. 执行器改名与投影字段

- [ ] 3.1 `BackgroundSubagentExecutor → BackgroundTaskExecutor` 全仓改名（import、日志前缀、docstring 重述双 kind 职责；shell 日志改 `bg task` 前缀）；状态机、`_TaskEntry`、`_arun`/`_arun_shell` 分流不动
- [ ] 3.2 `BackgroundTask` 增 `subagent_type: str = "general"` 字段（shell 任务为 None），进 `to_dict()` 投影与任务卡事件
- [ ] 3.3 全仓 grep 无 `BackgroundSubagentExecutor` 残留；`python3 scripts/change-scope.py` 确认影响面；文档引用同步（docs/engineering 后台任务相关篇目）
- [ ] 3.4 验证：全量测试绿

## 4. 前端与收尾（可选）

- [ ] 4.1 任务卡显示类型标识（`subagent_type` 投影字段，null 不渲染）；触点：BackgroundSubagentCollapse / SubagentCollapse 组件与相关投影 store
- [ ] 4.2 前端验证：`pnpm lint` + `pnpm build`；新旧消息混排显示不回归
