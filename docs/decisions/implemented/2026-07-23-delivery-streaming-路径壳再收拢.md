# 决策：delivery / streaming / 路径壳再收拢

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** delivery 薄文件过多；`telegram_runtime` 占 services；`agent_workspace_paths` 纯委托壳；`tool_errors` 与 `tool_failure` 同域。

**How to apply：**
- delivery：`inbound`→`channels`；`lifecycle`→`orchestrator`；`mapper`+`sse_codec`+`sse_delivery`→`sse.py`
- `services/telegram_runtime` → `domain/chat/delivery/telegram/runtime.py`
- 删除 `config/agent_workspace_paths`；调用方改 `user_data_paths`（`delete_session_workspace` 别名保留）
- `tool_errors` 并入 `tool_failure`（异常层次 + 分类同文件）
