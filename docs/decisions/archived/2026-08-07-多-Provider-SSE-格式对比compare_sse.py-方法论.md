# 决策：多 Provider SSE 格式对比（compare_sse.py 方法论）

状态：implemented
日期：2026-08-07
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 平台适配多 provider 前必须看清各家 SSE 原始格式；SDK 会抹平差异。

**How to apply：**
- 用 httpx 直连读原始 `data:` 行（不走 openai/anthropic SDK），脚本在 `.tmp/scripts/compare_sse.py`（gitignore，不上 GitHub），结果分析在 `.tmp/RUN_RESULT.md`。
- 四种格式：OpenAI Chat Completions（`data: {"choices":[{"delta":{"content":"..."}}]}` + `[DONE]`）、Anthropic Messages（`event: content_block_delta` + `message_stop`，`x-api-key` + `anthropic-version` 头）、OpenAI Responses（`type:response.output_text.delta` + `response.completed`，delta 直接是字符串）、OpenAI 兼容中转。
- 同一 DeepSeek 模型三形态（OpenAI `/v1`、Anthropic `/anthropic`、Responses `/v1`）reasoning 字段名/位置完全不同：`reasoning_content` vs `thinking` content_block vs `response.reasoning_text.delta`——统一抽象会丢差异，适配必须按格式分支。
- usage 位置：OpenAI 在末尾 chunk（`stream_options.include_usage`，中断拿不到）；Anthropic 分两次（`message_start` 给 input、`message_delta` 给 output）；Responses 在 `response.completed`。
- DeepSeek Anthropic 兼容端点是 `https://api.deepseek.com/anthropic`（非 `/v1/messages`）；中转 Anthropic 常见 `/v1/messages`。
- 排障顺序：HTTP/2 兼容性（中转可能只支持 HTTP/1.1）、model_not_found（中转模型名可能不同）、500 do_request_failed（中转不支持某端点）。
