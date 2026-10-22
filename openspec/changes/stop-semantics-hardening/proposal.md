# 变更：会话停止语义重整

## Why

用户主动停止目前在前端是乐观收尾：点击即置终态、stop API fire-and-forget。这违反了 `platform-chat` 既有要求（「chat 页停止 UI SHALL 等待服务端 run 进入终态，避免本地假完成」）——该行为是 2026-09-02 提交 `755bf352` 在渲染重构中无记录引入的回归。乐观收尾在 API 失败时让 UI 谎报终态且停止按钮已消失、无法重试。同时停止的级联语义缺失：前台等待中的子任务经 `asyncio.shield` 幸存（shield 本为「前台超时转后台」设计，用户停止误入同路），子任务终态还会触发 auto-continue，在用户刚停止的会话里 60 秒后自动冒出新 run。参考 dsh（deepseek-harness）的成熟语义：停止信号同步发出、UI 状态由服务端确认驱动、前台子任务随父级联取消、后台任务不随用户停止级联。悬空 tool_calls 的修复已由既有 `PatchToolCallsMiddleware`（deepagents，每次图执行前补合成 ToolMessage）覆盖，不属本变更范围。

## What Changes

- **前端停止恢复同步**（回归修复）：`stopCurrentRun` 回到「await stop API → 应用返回的终态快照 → 收尾 UI」；API 失败时 UI 保持运行态可重试并提示，不本地假完成。
- **停止终态文案单一来源**：后端停止终态处理对未完成工具的 reconcile 文案统一为「用户已停止生成」，消除停止瞬间与刷新后的文案漂移。
- **前台子任务级联取消**：主 run 用户停止时，取消该 run 前台等待中的 subagent 任务（废除 shield 幸存路径在用户停止场景的行为），子任务走既有乐观终态 + 协作退出 + 部分产出回收。
- **手动停止压制 auto-continue**：用户停止时置会话级停止标记并取消待唤醒定时器，标记有效期内任务终态不再自动创建 continuation run，直到下一条用户消息解除。
- **后台任务不级联**（明确语义，非变更）：`run_in_background=true` 的子任务与后台命令任务属于会话，用户停止主 run 不影响它们；可在任务目录单独停止。

无 API 契约破坏：`POST /api/chat/runs/{run_id}/stop` 路径与响应体（RunSnapshot）不变。

## Capabilities

### New Capabilities

（无——全部为既有能力的规格变更）

### Modified Capabilities

- `platform-chat`: 停止 UI 同步语义补强（既有 SHALL 的失败场景显式化：API 失败保持运行态可重试）；停止终态文案单一来源。
- `agent-background-tasks`: 新增主 run 用户停止与任务的级联关系要求（前台等待任务级联取消、后台任务不级联、手动停止压制 auto-continue）。

## Impact

- 前端：`frontend/src/views/chat/useSSEStream.ts`（stopCurrentRun 同步化：代次守卫、重入守卫、先等 API 后关流）、chat.vue（停止失败提示、onSnapshot 快照应用时幂等派生中断标注）。
- 后端：`services/run_service.py`（停止路径级联钩子）、`agents/subagents/async_tools_middleware.py`（前台等待任务级联取消）、`services/bg_continuation_service.py`（停止时取消待唤醒）、`chat/runs/projection.py`（reconcile 文案）。
- 测试：`backend/tests/`（停止级联、auto-continue 压制）、`frontend/__tests__/`（同步停止与失败重试）。
- 行为变更（非破坏）：用户停止主 run 时前台等待中的子任务从「幸存转后台」变为「级联取消」；停止后不再自动出现 continuation run。
