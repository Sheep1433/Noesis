# 决策：fallback 返回路径必须覆盖 custom event，而不是假设有 model-end

状态：implemented
日期：2026-08-16
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**问题/症状：** 重试耗尽后用户仍看不到错误，前端可能长时间停在某次 attempt；另一轮修复又出现 fallback 文本重复、残留流式文本丢失和读连接永久等待。

**根因：** `awrap_model_call` 重试耗尽后直接返回 fallback `AIMessage`，不会经过 `model.ainvoke()` 的 `on_chat_model_end`，所以依赖该 hook 的旧消费链路实际上是死代码。与此同时，半开 TCP 连接没有读取超时，bridge 在 fallback 前也没有先 flush 已产生的文本；SSE custom event 的外层 `type` 合并顺序不一致时还会污染事件类型。

**解法/取舍：** fallback 改走与 retry 相同的双通道 `noesis_model_fallback` custom event；bridge 收到后先 flush 残留文本，再写 fallback 文本、发 error 事件并让 projection 进入 ERROR；`useSSEStream` 增加 45 秒读超时并清理每轮 timer；重试次数统一 6 次，移除异常类型的 2 次 override；事件 payload 使用 `{**data, type: ...}` 保证外层类型权威。降级文案改成短句“服务暂时不可用，请稍候重试”。

**可迁移原则：** 设计错误路径时要沿真实返回路径画图，不能默认“模型结束 hook 一定会触发”。任何绕过标准 hook 的 middleware return，都必须有专用事件或显式结果通道；流式系统还要同时处理半开连接、残留 buffer、事件类型覆盖和重复终态。

**验证与遗留：** 提交 `3c5f325`、`a7f6e7a`、`381a6f1` 已补 custom event、flush、读超时和定时器清理；会话列表级 `run_status` 展示在 8/17 继续实现，仍需验证多窗口状态不会互相污染。
