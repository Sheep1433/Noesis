# 决策：MCP probe：Context7 超时误杀 + remote_ops 502

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **Context7**：4s 探测超时过紧（跨境偶发 >4s），改为 12s；成功缓存 60s、失败仅 8s，避免把超时结果锁死。
- **remote_ops 502**：本机 `extensions/mcp/ssh` 未正常起来（默认 `START_MCP=0`）。提示改为引导 `START_MCP=1 ./scripts/run.sh dev`；启动脚本显式 `--transport http --port 8000`。
