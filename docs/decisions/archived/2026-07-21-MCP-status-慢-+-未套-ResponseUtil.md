# 决策：MCP status 慢 + 未套 ResponseUtil

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **为何 6s+**：`probe=true` 对每个 server 真实 `get_tools()` 握手；并行后总耗时 ≈ max（Context7 跨境 RTT 常占大头；`remote_ops` 未启动则失败）。
- **响应格式**：`/api/mcp/*` 统一 `ResponseUtil.success(data=...)`；前端 `parseAuthJson` 解包。
- **优化**：探测 HTTP timeout 4s + `asyncio.wait_for`；结果缓存 45s；状态 URL 展开 `${ENV}`；错误展开 TaskGroup 子异常。
