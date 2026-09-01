# 决策：API 响应统一 ResponseUtil 补齐

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **问题**：除 MCP 外，Skills GET、知识库几乎全部、Chat context/workspace 文件仍返回裸 Pydantic，违反 `backend/AGENTS.md`「禁止手写裸 JSON」。
- **已对齐**：
  - `skill_api`：fs/tree、fs/file、market browse/search/detail
  - `knowledge_base_api`：status/collections/config/documents/shards/upload/delete 等 JSON 端点（search 原本已套）
  - `chat_api`：session context、workspace file GET/PUT
- **前端**：`skills.ts` / `knowledgeBase.ts`（`kbJson`）/ `chat.ts` 对应函数改用 `parseAuthJson` 解包 `data`。
- **刻意不套**：SSE StreamingResponse、workspace archive / 附件 FileResponse、导出 markdown 等二进制流。
- **本来就合规**：auth / user / model / chat 会话消息主路径 / mcp / chat_attachment。
