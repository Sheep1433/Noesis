# 决策：对话模型 per-model 上下文（catalog limit）

状态：implemented
日期：2026-07-11
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **字段**：`model.catalog[].limit` 与 models.dev / OpenCode 一致：`context`（窗口上限）、可选 `output` / `input`；`model.limit` 可作无 catalog 或 catalog 项未写 limit 时的默认。
- **解析**：`llm/model_limits.resolve_model_limit(model_id)` → catalog `limit.context` → `context.max_input_tokens`（全局兜底）→ 128K。
- **运行时**：`create_noesis_agent(model_id=...)` 将 `model_id` 传入 `ContextLifecycleMiddleware`；它独占压缩判断并生成 `ContextSnapshot`，`RuntimeTelemetryMiddleware` 只读取 snapshot。压缩触发默认 `trigger_tokens: 0` + `trigger_fraction × limit.context`。
- **API**：`GET /api/models` 每项返回解析后的 `limit` 对象；embedding/rerank 配置未改。

**2026-08-14 设计校正：** 配置字段应明确表达模型的 `contextWindow`，只用于上下文圆环、压缩阈值和输入容量判断；不要再用含义模糊的 `limit` 同时承载输出上限。输出长度应由 provider/model 的 `max_tokens` 或等价运行时参数控制，并为上下文保留空间；输入窗口和输出预算是两个不同约束。
