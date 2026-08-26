# Tasks

- [x] `UserSignalBus`（进程内，key=user_id，满则丢，订阅上限 16）
- [x] `RunManager._publish_session_signal` 双总线投递（用户信令带 session_id + status）
- [x] `AgentRunRepository.get_active_runs_for_user` + 端点 `GET /api/chat/events/stream`（首帧活跃 run 对齐，15s keepalive）
- [x] 前端 `userSignalStream.ts` + chat.vue 接线（行 patch / 行不在全量刷新 / 重连对齐）
- [x] 回归：test_user_signals.py（总线与生命周期挂钩）；全量后端 + 前端 lint/test/build
