# 决策：usage 累计 dedup 陷阱 + reasoning token 口径（↓2 bug）

状态：implemented
日期：2026-08-07
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 线上显示输出 token 只有 2（↑21.4K ↓2），总 token 停在输入值；同时思考/工具状态文案「已完成/完成」不一致。

**根因（已用红测试锁定 `assert 2 == 593`）：**
1. `_accumulate_usage` 在 `on_chat_model_stream`（partial usage）和 `on_chat_model_end`（权威 usage）用**同一 run_id** 各调一次，dedup（`langgraph_sse.py:336-341`）把 end 的权威 usage 当重复跳过了 → 部分流式 usage 被冻结成最终值。
2. `_normalize_usage` 忽略 `output_token_details.reasoning_tokens`：DeepSeek 系 reasoning 模型把思考 token 放在该字段，`output_tokens` 不含 → 输出被系统性低估。

**修复：** usage 只在 `on_chat_model_end` 累计（该点 usage_metadata 完整）；stream chunk 的 usage 不可靠。dedup 保留，用于防重复 end 事件。

**可迁移原则：**
- 统计类数据的「最后一次权威值」不能被「第一次部分值」的 dedup 吃掉；去重键相同不代表数据相同。
- reasoning 模型 token 口径：`output_tokens` ≠ 全部输出，要看 `output_token_details.reasoning_tokens`；多 provider 平台要按模型分口径。
- 状态文案统一走常量映射（`TOOL_STATE_LABELS`），不要散落硬编码。

**2026-08-10 回归补充：** 简单首轮问答仍出现几十 K 的 ↑/↓ 用量，调试日志显示流式 chunk 携带的 usage 被重复观察/累计的风险仍存在。排查时必须按一次模型请求建立唯一 usage 记录：chunk usage 只用于诊断，终态 usage 才能写入统计；同时保留原始 `run_id`、chunk 序号和 provider 响应，才能区分“真实大 prompt”与“同一 usage 重复累加”。

**新增验收：** 首轮无工具问答、带工具多轮问答、触发摘要后的下一轮分别回放；对比 provider 原始 usage、服务端累计值和前端展示值，确保三者只在口径转换处有可解释差异。
