# Tasks: subagent 类型分发与任务运行时身份澄清

按 design.md 迁移顺序分四组，每组可独立提交与回滚。

## 1. 注册表与 descriptor 落库（零行为变化）

- [x] 1.1 新建 `agents/subagents/registry.py`：`SubagentRole`（frozen dataclass：name / description / worker_factory / interrupt_on / model_id 模型绑定）与 `SubagentRegistry`（装配期注册、重名 ValueError、按 name 取 role、类型清单导出、生效模型解析：绑定值或父模型）
- [x] 1.2 `super_agent.py`：`_bg_worker_factory` 配方原样搬入 `general` role 并注册（工具集、backend、prompt、interrupt_on 逐项等价）；装配处集中断言所有 spec 的 worker 工具集不含 start_task / check_task / cancel_task / list_tasks / send_message
- [x] 1.3 `SubagentSessionService.launch()` 增 `subagent_type` 参数，写 child session `extra["subagent"] = {"version": 1, "type": <type>, "model": <生效模型>}`（生效模型 = 类型绑定或父模型，与 `extra.model_id` 同源）；`super_agent._create_child_session` 透传
- [x] 1.4 单测：重名注册抛异常；descriptor 写入与读取校验（含 model 字段）；模型绑定解析（绑定值 / 沿用父模型）；worker 工具集断言（`tests/test_subagent_type_dispatch.py`）
- [x] 1.5 验证：`uv run pytest tests/ -q` 全绿 + child session 落库带 descriptor。执行注：全量单测 1494 绿（1 失败为在途 TTFT 改动的 core 边界问题，非本变更）；descriptor 落库经真实 DB 直连探针证实（launch 写入 `subagent: {version, type, model}`）。「起服务跑一轮真实委派」被 LeaderElector 单实例机制阻断（既有 dev server 持锁，第二实例 fail-fast）——descriptor 与中间件链路由单测 + 直连探针覆盖，真实 LLM 委派轮待 dev server 重启后补跑（见 2.7 注）

## 2. NoesisSubagentMiddleware（工具面收编 + graph state）

- [x] 2.1 新建 `agents/subagents/tools_middleware.py`（实现注：放 subagents 包与 registry/executor/notify 内聚，不进通用 middlewares/——该目录是全 profile 共享栈，本中间件仅主 Agent 挂载）：`SubagentTasksState`（`bg_tasks` 合并 reducer）+ `BgTaskIdentity` TypedDict
- [x] 2.2 五个工具（start / check / cancel / send_message / list_tasks）从 `subagents/tools.py` 迁入 middleware 构造函数，闭包捕获 registry / executor / service 回调；`super_agent.py` 的 `tools.extend(build_background_task_tools(...))` 段退役，middleware 经 `middleware=` 参数挂载（与 `BgNotifyMiddleware` 同通道，SUBAGENT profile 不挂）；`tools.py` 删除
- [x] 2.3 `start_task` 增**必填** `subagent_type` 参数（schema 枚举自注册表，缺失即 schema 校验拒绝）；未注册类型返回含可用清单的错误文本且不建 child session
- [x] 2.4 工具返回值改 `Command(update={...})`：回 ToolMessage（文本与迁移前逐字一致）同时写 `bg_tasks`（仅 start_task——身份写入点；check/cancel/send/list 维持纯文本返回，状态永远实时查执行器 miss 落 DB，不信 state 快照）；middleware 工具补 `noesis_provider_key` 标注（不经 `tools=` 通道，统计归因不退化）
- [x] 2.5 `wrap_model_call` 注入类型清单（`- name: description` 逐行）
- [x] 2.6 契约测试：五个工具返回文本与迁移前逐字对比（executor 套件 62 条全绿）；state 内出现 identity；state 快照过期时 check_task 返回实时终态；未知类型拒绝且无副作用；`start_task` schema 不含模型参数；真实 create_agent 图内 Command → `bg_tasks` 落 checkpoint 且跨轮存活（`tests/test_subagent_type_dispatch.py` 12 条）
- [x] 2.7 验证：全量测试绿 + api_contract 21 条绿 + 集成快速轮（不触 LLM）39 条绿。执行注：真实会话委派一轮（后台 + 前台等待 + followup + 审批各一）被 LeaderElector 单实例机制阻断，待 dev server 以新代码重启后补跑；测试期间发现预置回归（children 摘要缺 `kind`，`aaad3a09` 引入）已记 `docs/bug/children-summary-missing-kind.md`

## 3. 执行器改名与投影字段

- [x] 3.1 `BackgroundSubagentExecutor → BackgroundTaskExecutor` 全仓改名（import、docstring 重述双 kind 职责）；状态机、`_TaskEntry`、`_arun`/`_arun_shell` 分流不动（日志前缀维持 `bg subagent`/`bg task` 原状，不改）
- [x] 3.2 `BackgroundTask` 增 `subagent_type: str = "general"` 字段（shell 任务为 None），进 `to_dict()` 投影与任务卡事件
- [x] 3.3 全仓 grep 无 `BackgroundSubagentExecutor` 残留（主规格旧名由归档时 delta 落地，属预期保留）；`python3 scripts/change-scope.py` 确认影响面；docs/engineering 无该类名引用
- [x] 3.4 验证：全量测试绿

## 4. 前端与收尾（可选）

- [ ] 4.1 任务卡显示类型标识（`subagent_type` 投影字段，null 不渲染）；触点：BackgroundSubagentCollapse / SubagentCollapse 组件与相关投影 store
- [ ] 4.2 前端验证：`pnpm lint` + `pnpm build`；新旧消息混排显示不回归
