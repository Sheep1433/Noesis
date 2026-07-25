## 1. Provenance 契约与检索身份

- [ ] 1.1 定义 versioned provenance envelope、`source_ref` 规范化算法及 golden tests
- [ ] 1.2 盘点现有 payload 的 `file_hash` / `content_hash` / `chunk_index` 覆盖率，确定 `document_key` 迁移规则
- [ ] 1.3 扩展 `KbSearchHit` 与 rerank 复制路径，保留 point id、document key、chunk index
- [ ] 1.4 扩展 `kb_search_tool` 输出 provenance，并增加 harness 不反向 import 平台的边界测试
- [ ] 1.5 增加 hybrid/BM25/vector 集成测试，验证 locator 可解析且不把裸 content hash 当 point id

## 2. 平台来源模型与生命周期

- [ ] 2.1 新增 `EvidenceSource` / `SourcesPart` 及严格反序列化兼容
- [ ] 2.2 实现 versioned provenance parser，仅接受已知工具与 schema
- [ ] 2.3 在 tool end 调用 builder `upsert_sources`，按 source_ref 去重并记录 tool_call_id
- [ ] 2.4 实现顶层 text 的 `[[source:...]]` 解析，排除 reasoning、嵌套 text、fenced/inline code
- [ ] 2.5 覆盖 completed、partial、disconnect、HITL pending/resume 的 builder 恢复和持久化测试

## 3. RunEvent 与 SSE

- [ ] 3.1 增加 `sources-available` RunEvent/Delivery 映射并锁定 snake_case golden
- [ ] 3.2 增加 `sources-finalized`，在 finish 前输出 cited ids 与展示顺序
- [ ] 3.3 验证 SSE 断连不影响 PersistSink 最终 source part，stop 不依赖跨请求注入事件
- [ ] 3.4 更新 `docs/prd/platform/SSE流式数据设计.md`

## 4. 授权来源详情 API

- [ ] 4.1 新增 `GET /api/chat/messages/{message_id}/sources/{source_id}` schema/service/API
- [ ] 4.2 实现 message/session ownership 与 collection scope 二次校验
- [ ] 4.3 实现 point id 快路径、document key + chunk index 回退和 snapshot stale 状态
- [ ] 4.4 增加越权、伪造 source id、文档删除/重建、collection 权限撤销测试

## 5. COMMON_QA 与前端体验

- [ ] 5.1 更新 COMMON_QA prompt：只引用工具返回的 source_ref，禁止编造 token
- [ ] 5.2 前端 parts/history 支持 SourcesPart tolerant 解析与刷新回放
- [ ] 5.3 SSE 消费 available/finalized，并按正文首次出现顺序映射显示角标
- [ ] 5.4 来源列表区分“引用来源”和折叠的“检索来源”，禁止 fallback 冒充引用
- [ ] 5.5 来源抽屉只调用 assistant-scoped API，不拼接 collection/shard URL

## 6. 验证与发布

- [ ] 6.1 后端 citation/source、KB、SSE、PersistSink、HITL 与权限测试通过
- [ ] 6.2 前端单测、`pnpm lint` 和 `pnpm build` 通过
- [ ] 6.3 用固定 requirement_docs 问题验收 cited/retrieved、刷新、stop、断连和多次检索
- [ ] 6.4 记录 source token 命中率、未知 token 数和 retrieved→cited 比率，产品验收后启用
