# Tasks: 会话停止语义重整

## 1. 后端停止收口（文案与压制）

- [ ] 1.1 `chat/runs/projection.py`：`RunAborted` 收口对未完成工具的 reconcile 文案由「本次工具执行已停止」改为「用户已停止生成」（`RunCompleted` / `HitlRequired` 的 reconcile 语境不同，不改）；`_force_finalize_stopped` 兜底路径弃用 `run_recovery_service.mark_running_tools_unknown`，改用 builder 的 `reconcile_nonterminal_tools(CANCELLED, "用户已停止生成")`（与正常路径同款），server_restart 恢复路径维持原函数
- [ ] 1.2 `chat/event_mapping/failure_notice.py`：删除无生产调用方的 `append_user_stop_notice_to_content` 与 `append_disconnect_partial_content`（已复核仅测试引用）及其测试用例；`append_stream_failure_notice_to_content` 有生产调用方（`services/qa/helpers.py`），保留
- [ ] 1.3 `services/bg_continuation_service.py`：新增会话级停止标记（置位点 `RunService.stop`——用户停止 API 唯一入口，不得下沉 `run_manager.stop`；置位同时取消 pending wake；检查点在 `maybe_continue` 入口覆盖 debounce=0 直调路径；`note_user_activity` 清除；`reset_for_tests` 一并重置）
- [ ] 1.4 单测：停止后任务终态不创建 continuation run；用户消息解除压制；无标记时行为不变；`_finalize_start_failure` 路径不置标记；兜底收口文案与正常路径一致

## 2. 前台子任务级联取消

- [ ] 2.1 `agents/subagents/async_tools_middleware.py`：在既有 `except asyncio.CancelledError` 块（`async_tools_middleware.py:360`）的**外层取消分支**（非 `future.cancelled()`）`raise` 之前插入 `executor.cancel(task_id)`——`ValueError`（任务已终态竞态）仅告警；内层取消降级文本与 `TimeoutError` 转后台路径保持不变
- [ ] 2.2 单测（`tests/test_bg_subagent_executor.py`）：外层取消 → 任务落 cancelled 且取消向上传播；内层取消（future.cancelled）仍走降级文本；超时路径不取消；后台分支不受影响

## 3. 前端同步停止

- [ ] 3.1 `frontend/src/views/chat/useSSEStream.ts`：`stopCurrentRun` 重排为——捕获 `streamGeneration` → 本地 `stopping` 标志挡重入（await 期间按钮仍可点）→ `await stopAgentRun()`（期间不预断流、不预置 `userAborted`，帧照常渲染）→ `isCurrentStream(generation)` 守卫（防 await 窗口切会话后迟到 settle 误杀新流）→ `handleRunSnapshot` 应用终态快照并 settle → 事后置 `userAborted` + `abortController.abort()`；API 失败时状态全不动、错误抛给调用方
- [ ] 3.2 `frontend/src/views/chat.vue`：`stopChatStream` 捕获失败 → toast「停止请求失败，请稍后重试」；`onSnapshot` 在 `snapshot.finish_reason === 'stopped'` 时对替换后 parts 幂等追加中断标注（`appendUserStopNotice`，与 `onFinish` 路径双路幂等，覆盖快照后到/断线重连已停止 run 场景）；确认停止按钮在失败后仍可点击（接口触点：`POST /api/chat/runs/{run_id}/stop`，UI 触点：输入区单按钮停止态）
- [ ] 3.3 前端单测（`__tests__/useSSEStream.test.ts`）：成功路径应用快照后 settle('stopped') 且事后才拦帧；失败路径保持运行态、不追加中断标注、可再次调用；await 期间切换会话时迟到快照不落新流；重复点击不并发两次 stop 请求

## 4. 回归与验收

- [ ] 4.1 后端：`uv run pytest tests/ -q`（含新增用例全绿）；`uv run pytest tests/api_contract -q` 契约门禁
- [ ] 4.2 前端：`pnpm test` + `pnpm lint`
- [ ] 4.3 真实链路手动验证（`uv run app.py` 起服务）：a) 工具执行中停止 → 刷新后文案一致；b) 前台子任务等待中停止 → 任务卡变已取消；c) 停止后等任务终态 60s+ → 无新 run 出现；d) 停止后再发消息 → 正常执行（悬空 tool_calls 由既有 PatchToolCallsMiddleware 在下一轮补齐，顺带验证）
- [ ] 4.4 文档：`docs/engineering/platform/chat-streaming.md` 停止节同步目标语义；本变更归档时同步 `openspec/specs/` 主规格
