# 决策：LLM 429 重试：遵循厂商 Retry-After 但设 cap

状态：implemented
日期：2026-08-25
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **问题/症状**：网关 429 返回 `retryAfterSeconds: 60` 时原实现直接 sleep 60s，单次失败即阻塞一分钟。
- **解法**：`llm_error_handling_middleware.py` 新增类属性 `retry_after_cap_ms = 60_000`（与 `retry_base_delay_ms` 同级，均未走 config，保持一致）；`_build_retry_delay_ms` 对提取到的 retry_after 做 `min(retry_after, cap)`。
- **边界**：仅 header 提取链路生效（网关同时设 header；body 未解析）；无 Retry-After 时照旧指数退避（cap 8s）不受影响。
- **验证**：3 个新增测试（遵循 / 病态值 clamp / 无 header 回退），全量回归 1193 passed；commit `196faa9`。
