## 1. Context 数据模型与计数

- [x] 1.1 为 context snapshot 定义向后兼容的 breakdown、sources、estimated、counting_method 与 caller 结构，并补纯函数单元测试
- [x] 1.2 重构 `context_metrics.py`，在最终 `ModelRequest` 上分别估算 system、conversation、tool results、tool definitions 与 other，保证分类之和与本地 current_tokens 一致
- [x] 1.3 为未知消息类型、无 tokenizer、复杂 tool schema 和多模态内容补安全降级及回归测试
- [x] 1.4 将 context registry 从仅 session 最新值收敛为按 run/caller 隔离的短生命周期快照，并验证并发会话不串数据、终态可清理

## 2. 来源 provenance

- [x] 2.1 定义 request-scoped context provenance 契约，保证内部元数据不会被序列化到 Provider payload
- [x] 2.2 为 Skills system 注入和 `/skills/...` 工具读取结果标记 provenance，缺失标记时保留在父分类
- [x] 2.3 为 memory system 注入和 `/memory/...` 工具结果标记 provenance
- [x] 2.4 为 RAG 与 attachments 注入/工具结果标记 provenance，并覆盖启用与未启用场景
- [x] 2.5 增加 wire payload 契约测试，确认 provenance 不改变 prompt 文本、tool schema 或 Provider 请求字段

## 3. Provider usage 规范化与归属

- [x] 3.1 扩展共享 usage 规范化，保留 cache read、cache write、reasoning details 作为后端规范化与按需调试字段（非默认前端摘要），并兼容 LangChain/OpenAI-compatible 常见字段别名
- [x] 3.2 定义 model call attribution（model run id、caller、model_id、step_kind、parent_tool_call_id）并接入 lead Agent、subagent 与 middleware 模型调用
- [x] 3.3 重构 run 内 usage collector，按 model run id 去重，生成 cumulative、by_caller、by_model 与有界 steps
- [x] 3.4 验证子 Agent usage 只计入总量一次，父 task 仅引用归属，不发生主/子 Agent 重复累计
- [x] 3.5 增加流式 chunk、model end、重放/重复事件、Provider 无 usage 与并行子 Agent 的聚合测试

## 4. RunEvent 与 SSE 契约

- [x] 4.1 扩展统一用量/上下文 RunEvent payload，保持既有总量字段不变，并更新 `/api/chat` `usage-update`、`context-update`、`finish.usage`
- [x] 4.2 让 run snapshot/重订阅保留最新 context 与累计 usage 摘要，确认 sequence 去重不会重复累计
- [x] 4.3 对 breakdown、details、by_caller、by_model 和 debug steps 做边界校验与大小限制，异常扩展字段 SHALL 降级而不阻断文本流
- [x] 4.4 更新 SSE bridge、delivery、assistant parts/消息持久化相关契约测试，确认旧客户端 payload 仍可消费且不按 token delta 写库

## 5. Chat UI

- [x] 5.1 扩展前端 SSE 与消息类型，分别维护最新 context snapshot 和当前 run 累计 usage，兼容缺失新字段的历史消息
- [x] 5.2 将 token 指示器展开内容分为“当前上下文”和“本轮消耗”，展示估算标识、context breakdown 与 input/output 及 caller/model 汇总；cache/reasoning 等 Provider 明细不进入默认摘要
- [x] 5.3 增加 caller/model 汇总和按需调试步骤视图（可按需展示 cache/reasoning 等 Provider 明细），并确保大量 steps 不造成无界 DOM 或状态增长
- [x] 5.4 为旧事件、部分 Provider details、零值与不可用值、刷新恢复及会话切换补前端测试

## 6. 验证与文档

- [x] 6.1 运行 context metrics、SSE bridge、run delivery、Agent/subagent usage 相关后端测试并修复回归
- [x] 6.2 运行前端相关单测、`pnpm lint` 与 `pnpm build`
- [x] 6.3 使用一个 SuperAgent 场景验证 Skills、工具结果和子 Agent 同时出现时，context 与累计 usage 的语义和数值不会混淆
- [x] 6.4 更新现有 chat streaming/Agent 工程文档，说明字段语义、估算限制与排障方法，不新增重复版本文档
- [x] 6.5 使用 `code-review` 检查 spec 完整性、项目规范、重复抽象与兼容分支；如确认存在冗余，再使用 `code-simplification` 收敛本次改动
