# 决策：OpenCode deepseek-v4-flash-free 思考流与 trust_env 网络路径

状态：implemented
日期：2026-07-09
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **现象**：默认模型 `deepseek-v4-flash-free` 聊天页不再展示「思考过程」折叠块；`show_thinking_process: true` 且 `LangGraphSseBridge` / 前端 `ReasoningBlock` 链路正常。
- **易误判**：不是 `trust_env` 字段本身「控制」`reasoning_content`；它只是 httpx 是否读取系统代理的开关。
- **因果链**：`bf10552` 在 `llm/factory.py` 为全部 LLM 注入 `trust_env=False` 的 httpx 客户端（绕过 macOS 系统代理 `127.0.0.1:10810`，避免代理挂掉时 OpenCode/DashScope `APIConnectionError`）→ 直连 vs 走代理落到 **不同 Cloudflare 边缘 / 出口** → 同模型同 prompt 的 SSE `delta` 形态不一致。
- **本机实测（2026-07-09）**：
  - `trust_env=False`（Noesis 当前默认）：直连 `opencode.ai`，`cf-ray=…-BOS`，流式 `reasoning_content` **0 个非空 chunk**（5/5 次稳定）。
  - `trust_env=True`：经 `127.0.0.1:10810`，`cf-ray=…-EWR`，`reasoning_content` **数百～上千 chunk**（5/5 次稳定）。
  - 目录内 `big-pickle` 在直连下仍有 `reasoning_content`；flash-free 对网络路径更敏感。
  - flash-free 有时把「逐步推理」写进正文 `content`，不会进 `ReasoningBlock`。
- **结论**：根因偏 **OpenCode / CDN 路由侧**对不同 egress 返回字段不一致，不是 Noesis reasoning 解析回归。
- **暂缓修复**：暂不改动 `trust_env` 策略；需稳定思考时可切 `big-pickle`，或日后仅对 `opencode.ai` 单独恢复代理 egress。
