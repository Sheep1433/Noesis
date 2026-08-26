# Proposal: 用户级信令流（会话列表实时刷新）

## Why

会话列表的 run_status 徽章只在拉列表时取一次：用户停留在列表上时，其他
会话的 run 开始/结束（后台任务触发的续跑、其他窗口发起的对话、定时任务）
列表都不更新。会话级信令流（`/sessions/{id}/events`）只覆盖单个会话，
列表关心的是用户全部会话——缺一条用户级通道。

## What Changes

- 新增进程内 `UserSignalBus`（与 `SessionSignalBus` 同构，key=user_id），
  hint-not-content 语义一致：队列有界满则丢，丢失靠全量刷新自愈。
- `RunManager._publish_session_signal` 双总线投递：会话信令不变；用户
  信令额外携带 `session_id` + `status`。
- 新端点 `GET /api/chat/events/stream`：连接先下发用户全部活跃 run 作
  首帧对齐，此后推送 run-started / run-hitl-pending / run-terminal。
- 前端登录后一条 EventSource 常连：收到信令 patch 列表行（行不在则全量
  刷新）；断线重连后全量对齐。
- 与 `enable-distributed-sse-pubsub` 规划兼容：本总线是进程内实现，
  Redis fan-out 落地时随会话级信令走同一条广播链。

## Impact

- 后端：`noesis/chat/runs/user_signals.py`（新）、manager 双总线投递、
  `AgentRunRepository.get_active_runs_for_user`、`chat_api.py` 端点。
- 前端：`views/chat/userSignalStream.ts`（新）、chat.vue 接线与行 patch。
- 非目标：条件轮询方案（直接落地推送）、跨进程总线（Redis 变更内）。
