# 设计：会话停止语义重整

## Context

主 run 停止链路现状：前端 `useSSEStream.stopCurrentRun` 乐观收尾（本地置终态 + `stopAgentRun().catch(()=>{})`）；后端 `RunService.stop` → `run_manager.stop`（cancel producer、await 终态 future ≤2s）→ producer 的 `CancelledError` 经 `_persist_cancel_or_error` 落 `RunAborted` → run/message 双行 partial + SSE `run.finished(status=interrupted)`。前端乐观收尾是 `755bf352` 引入的无记录回归，违反 `platform-chat` 既有 SHALL。

子任务侧：`astart_async_task` 前台等待分支 `await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), timeout=600)`——shield 使任何对等待者的取消都不波及子任务（为「前台超时转后台」设计），用户停止同样走此路导致子任务幸存。子任务终态经 `schedule_maybe_continue`（60s 去抖）触发 continuation run，用户停止不做压制。

参考语义（dsh / deepseek-harness）：停止信号同步发出、协作收敛；UI 状态由服务端确认驱动；前台子任务经父 step 的取消信号级联取消且父等待其静默；未派发工具调用合成错误结果（`tool call aborted before dispatch`）保证 replay 有效；后台任务用独立取消源，用户停止不级联。

## Goals / Non-Goals

**Goals**

1. 前端停止恢复「等待服务端终态」的同步语义，API 失败可重试。
2. 用户停止主 run 时级联取消前台等待中的子任务；后台任务明确不级联。
3. 手动停止压制该会话的 auto-continue，直到下一条用户消息。
4. 停止终态的模型/用户可见文案单一来源。

**Non-Goals**

- 不改子任务自身停止语义（乐观终态 + 协作退出 + 宽限硬杀 + 部分产出回收，见 `agent-background-tasks` 既有要求）。
- 不改 stop API 路径、鉴权与 RunSnapshot 响应契约。
- 不做「停止时后台任务一并停止」的全停语义（产品决策：对齐 dsh，后台任务属会话，在任务目录单独停止）。
- 不处理主 run 停止遗留的悬空 tool_calls：既有 `PatchToolCallsMiddleware`（deepagents，`middlewares/stack.py` 无条件挂载）在每次图执行前的 `before_agent` 钩子为所有无结果的 tool_calls（含 invalid_tool_calls）补合成 ToolMessage，主/子 Agent 栈共用——停止后的下一轮天然被修复，无需新增逻辑。

## Decisions

### D1 前端同步停止：快照驱动收尾，失败保持运行态

`stopCurrentRun` 的操作顺序是本决策的关键——**先等 API，后关流**：

```
const generation = streamGeneration            // 捕获当前流代
const snapshot = await stopAgentRun(runId)     // SSE 流保持打开，帧照常渲染
if (!isCurrentStream(generation)) return       // await 期间切走会话/开新流 → 丢弃迟到结果
handleRunSnapshot(snapshot)                     // onSnapshot 应用终态 → settle（幂等）
userAborted = true                              // settle 之后才拦帧、关流
abortController?.abort()
```

要点：a) 等待期间不预断 SSE、不预置 `userAborted`——期间到达的增量是真实服务端输出，照常渲染；b) **generation 守卫必须有**：`settleSuccess` 不带代次检查，await 窗口内用户切换会话（`detachSubscription` 重置后新流已开跑）时，迟到的 settle 会把新流的 `isLoading` 误杀——这是旧同步版本没有的坑（守卫是后续加的）；c) **重入守卫**：await 期间 `isLoading` 仍真、停止按钮仍可点，双击会并发两次 stop 调用——加本地 `stopping` 标志挡重入，API 返回后清除；d) API 抛错时什么都不动：流仍在、`isLoading` 仍真、停止按钮仍可点，错误抛给调用方 toast；e) 对已终态 run 调停止，后端返回终态快照、API 成功，正常 settle，不属失败路径。

**中断标注的双路幂等派生**（SSE 与 API 的到达序竞态）：`run.finished` 帧可能先于 stop API 响应到达——settle 触发 `onFinish` 在流式 parts 上追加中断标注后，API 快照再经 `onSnapshot` **整体替换** parts（`chat.vue:1660`），刚追加的标注会被抹掉（服务端快照不含该段）。因此标注派生挂两处、均幂等（`partsContainUserStopNotice` 判重）：`onFinish`（`finish_reason === 'stopped'` 时，覆盖他窗停止 / SSE 先到场景）与 `onSnapshot`（`snapshot.finish_reason === 'stopped'` 时对替换后 parts 追加，覆盖 API 后到场景；同时让断线重连已停止 run 的快照也带上标注）。

备选（维持乐观收尾 + 失败回滚 UI）：回滚需要区分「已应用乐观收尾」与「服务端真终态」两态，且期间用户新消息会与仍在跑的服务端 run 冲突（409 排队）——复杂度远超收益，弃。备选（SSE 事件驱动收尾，run.finished 帧到达再 settle）：依赖服务端投递可达且不丢帧，断流场景退化为重连逻辑；快照直接随 API 响应返回更简单且与既有 `handleRunSnapshot` 幂等语义吻合，不采用。

### D2 前台子任务级联：取消传播点在等待协程，复用既有内外取消判据

`astart_async_task` 前台等待分支**已有一个 `except asyncio.CancelledError` 块**（`async_tools_middleware.py:360`），用 `future.done() and future.cancelled()` 区分两种取消：内层取消（子任务被硬超时/回收，穿透 shield）降级为可收部分结果的文本返回；外层取消（主运行停止）原样 `raise`。级联取消落在**外层取消分支的 `raise` 之前**：`executor.cancel(task_id)`（乐观终态受理，同步锁内、毫秒级；`ValueError` 即任务已终态的竞态，仅告警）。级联关系天然跟随「谁在等谁」——无需在 executor 里登记 task→run 归属，后台分支无等待协程天然不级联。

关键区分维持不变：`TimeoutError`（前台超时）不取消、自动转后台；内层取消不级联（任务自身已终止）；仅外层取消级联。

父 run 不等待子任务完全静止：子任务停止本身是乐观终态（受理即 cancelled，`agent-background-tasks` 既有语义），父的 partial 快照不依赖子任务收尾；子任务协作退出、部分产出回收与终态通知由其自身终态处理路径异步完成。与 dsh「父 step await child.whenIdle()」的差异源于我们的子任务停止受理即终态，无需父侧等待。

### D3 auto-continue 压制：会话级「用户已停止」标记，下一条用户消息解除

`bg_continuation_service` 新增会话级停止标记：置位点在 **`RunService.stop`**（用户停止 API 的唯一入口，`chat_api.py:1128`；channel 无停止入口）——普通路径与 `_force_finalize_stopped` 兜底路径都在该函数内，一处置位全覆盖。**不得**下沉到 `run_manager.stop`：`_finalize_start_failure` 等内部清理路径也调用它，误置会把启动失败清理当成用户停止。置位时同时取消该会话 pending wake（复用 `note_user_activity` 的取消逻辑）；**检查点在 `maybe_continue` 入口**（单一处理入口，覆盖 debounce=0 直调路径与去抖定时器路径两条进路）；`note_user_activity`（用户真实消息到达）清除标记。内存态，与 `_wake_counts` 同生命周期，`reset_for_tests` 一并重置。

备选（仅取消当前 pending wake，不清后续）：停止 1 分钟后后台任务终态仍会冒 continuation run，恰是用户抱怨的场景；且「停止」后需要用户再确认一次才恢复自动续跑，语义反复。弃。

### D4 文案单一来源：「用户已停止生成」，覆盖正常与兜底两条终态处理路径

用户停止有两条终态处理路径，文案须一致：

- **正常路径**：`RunAborted` 终态处理（`chat/runs/projection.py`）对未完成工具的 reconcile 文案由「本次工具执行已停止」改为「用户已停止生成」，与前端 `appendUserStopNotice` 的工具错误文案统一。`RunCompleted` / `HitlRequired` 的 reconcile 各有独立语境（完成时残留、审批暂停），不属用户停止，不改。
- **兜底路径**：`_force_finalize_stopped`（进程重启后停止、producer 收尾失败）现复用 `run_recovery_service.mark_running_tools_unknown`，工具文案「服务中断，操作结果未确认」+ 类别 `server_restart`——用户停止语义下两者皆错。改用 builder 既有的 `reconcile_nonterminal_tools(CANCELLED, "用户已停止生成")`（与正常路径同款终态处理，state/outcome/文案一致），server_restart 恢复路径维持 `mark_running_tools_unknown` 不动。

中断说明段落（*（本轮回复已被用户中断。）*）维持前端按 `extra.finish_reason === 'stopped'` 派生（实时与历史回放已一致）。`failure_notice.py` 死代码删除范围（已复核无生产调用方）：`append_user_stop_notice_to_content`、`append_disconnect_partial_content`；`append_stream_failure_notice_to_content` **有**生产调用方（`services/qa/helpers.py:334`），保留。

## 停止时序（变更后）

```
用户点击停止
  → POST /api/chat/runs/{run_id}/stop（前端 await）
  → RunService.stop
      → run_manager.stop：cancel producer + await 终态 future（≤2s）
      → producer CancelledError 传播至前台等待协程
          → executor.cancel(task_id)：前台子任务乐观落 cancelled（D2）
          → raise（继续传播）
      → bg_continuation_service：置停止标记 + 取消 pending wake（D3）
      → _persist_cancel_or_error → RunAborted
          → reconcile 未完成工具（文案 D4）→ run/message 双行 partial
          → SSE run.finished(status=interrupted, finish_reason=stopped) + [DONE]
      → 返回终态 RunSnapshot
  → 前端：generation 守卫 → handleRunSnapshot 应用快照 → settleSuccess('stopped')
    → 中断标注幂等追加（onFinish / onSnapshot 双路，见 D1）→ 置 userAborted + 关闭 SSE 订阅

（下一轮用户消息到达）
  → note_user_activity 清停止标记（D3）
  → 图执行（悬空 tool_calls 由既有 PatchToolCallsMiddleware 在 before_agent 补齐）
```

## Risks / Trade-offs

- [级联取消误伤：CancelledError 也来自进程停机/部署重启] → 停机时取消前台任务是合理行为（进程退出子任务也活不了），非误伤；后台任务不受影响（注册表内存态，重启即丢是既有限制）。
- [停止标记内存态：进程重启丢失] → 重启本就丢失 `_wake_counts` 与运行中 run；标记与唤醒机制同生命周期，重启后无待唤醒任务，无泄漏后果。
- [PatchToolCallsMiddleware 是上游依赖，deepagents 升级可能变更行为] → 中间件清单装配测试（`test_noesis_stack_assembly`）已钉住其在栈内的存在与顺序；升级 deepagents 时按既有锁定版本策略人工对照。
- [前端 await 期间用户切走会话/重复点击] → 沿用既有 `generation` 流隔离；重复点击由 `userAborted` 幂等守卫。
- [同步停止在极端场景（DB 写入慢）让按钮转圈] → 后端 stop 实测毫秒级落 partial，2s 宽限是上界；此前同步版本（755bf352 之前）长期运行无此投诉。

## Migration Plan

纯行为变更，无数据迁移、无配置新增，部署即生效。实施顺序（每步独立可验）：

1. 后端 D4 文案统一 + D3 压制标记 + D2 级联取消（含单测）。
2. 前端 D1 同步停止（含单测：成功 settle / 失败保持运行态可重试）。
3. 回归：`uv run pytest tests/ -q`（后端）、`pnpm test` + `pnpm lint`（前端）、api_contract 停止契约用例。
