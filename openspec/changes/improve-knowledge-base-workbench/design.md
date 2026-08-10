## Context

知识库前端目前由集合列表 `frontend/src/views/knowledge-base/KnowledgeBase.vue`、集合详情 `CollectionDetail.vue`、分片抽屉 `frontend/src/components/KnowledgeBase/DocumentDrawer.vue` 和分片弹窗 `ShardDetail.vue` 组成。集合列表在带侧栏的可用宽度下会出现横向裁切；文档、chunk 与检索结果使用三套展示结构。分片抽屉调用 `GET /api/kb/collections/{collection_name}/documents/{file_name}/shards` 一次读取最多 10000 个 point，再在浏览器中分页。

Qdrant payload 已由 `backend/packages/noesis-core/src/noesis/knowledge/retrieval/payload.py` 保存 `element_type`、typed `locator`、稳定 document/version/segment identity、hash、raw/clean text 和 `effective_processing_params`。当前 `knowledge_base_schema.py`、service 与 Qdrant adapter 只向管理界面暴露其中少量字段，因此本变更需要同时调整前端信息结构和 `/api/kb` 查询契约。

## Goals / Non-Goals

**Goals:**

- 形成与 Noesis 三套主题一致、在桌面与窄屏下均不横向裁切的知识库工作台。
- 让用户在单层检查器中连续浏览 chunk 列表和当前 chunk 的完整来源、结构、分块及身份信息。
- 将分片读取改为 Qdrant 过滤后的服务端分页，避免一次把整篇文档全部传给浏览器。
- 让文档浏览与检索结果共享 chunk 摘要、元数据标签和详情组件。
- 对历史 payload 缺失新增字段保持可读，缺失值不导致整页失败。

**Non-Goals:**

- 不改变 DeepDoc 解析、分块、Embedding、hybrid 检索或 rerank 算法。
- 不在本变更中实现图片、表格原始文件预览；多模态展示由 `kb-multimodal-retrieval` 继续定义。
- 不修改 Collection 配置存储位置，不做 PostgreSQL 数据迁移。
- 不重新设计全站导航和主题系统。

## Decisions

### 1. 集合页采用任务入口，文档检查采用 master-detail

`CollectionDetail.vue` 继续保留“文档库 / 检索测试 / 策略配置”三个入口。点击文档后打开一个响应式 chunk inspector：桌面端左侧为 chunk 列表、右侧为当前 chunk；窄屏端在同一抽屉内切换列表与详情，并提供明确返回按钮。移除 `DocumentDrawer.vue` 再打开 `ShardDetail.vue` 的二层弹窗流程。

选择单层 inspector 是因为用户的主要动作是连续比较相邻 chunk，而不是孤立查看某一条。备选方案是扩大现有卡片抽屉并保留详情弹窗，但仍会造成上下文中断和重复组件，因此不采用。

### 2. 定义共享的 ChunkSummary 与 ChunkDetail 展示模型

前端 `frontend/src/api/knowledgeBase.ts` 定义分页响应和两个模型：

- `ChunkSummary`：`id`、`chunk_index`、`header_path`、`locator`、`element_type`、`char_length`、可选 `token_count`、受限 `content_preview`、`created_at`。
- `ChunkDetail`：Summary 字段加完整 `content`、可选 `raw_text` / `clean_text`、`file_name`、`file_hash`、`content_hash`、`document_id`、`document_version_id`、`segment_id`、`source`、`Header_1..4`、`vector_dimension` 和 `effective_processing_params`。

详情组件按“章节内容 / 来源结构 / 原文 / 元数据”分组。文档、版本、片段标识与 hash 不再单独展示为“稳定身份”区域，而是与处理参数、补充来源字段合并为可复制、可滚动的 JSON 元数据。检索结果在该模型上追加 recall/rerank/final score，不把分数写回通用 chunk 详情。

备选方案是直接把完整 Qdrant payload 返回前端。该方案会泄露内部字段、放大响应并让 UI 依赖存储结构，因此不采用。

### 3. 分片列表接口改为 cursor 分页

`GET /api/kb/collections/{collection_name}/documents/{file_name}/shards` 接收 `limit`、`cursor`、`element_type`、`locator_type`、`keyword` 和 `sort`。主界面仅展示关键词、内容类型和排序；`locator_type` 保留在 API 侧供精确排查使用，不进入默认工作台工具栏。响应 `data` 改为：

```json
{
  "items": [],
  "total": 0,
  "next_cursor": null
}
```

`limit` SHALL 有服务端上限；`cursor` 为 opaque string，客户端不得解析。默认按 `chunk_index ASC, point_id ASC` 稳定排序。Qdrant adapter 使用 `file_name` filter、payload filter/full-text match、count 与 scroll/order-by，只返回当前页所需 payload，不读取 vector。需要的 `file_name`、`chunk_index`、`element_type`、`content` payload index 在创建 Collection 时建立；既有 Collection 在首次分页读取前执行幂等补建。

现有响应是裸列表，无法同时表达 items、total 和 continuation，因此本接口响应结构明确标记为 breaking。仓库内唯一前端调用方与测试在同一变更中迁移，不保留 legacy 参数或旧响应分支。

备选方案是继续一次读取全部 point 后在 Service 或浏览器分页。它不能降低 Qdrant 到服务端的扫描量，也无法支撑大文档，因此不采用。

### 4. 详情接口只读取 payload，不返回 vector

`GET /api/kb/collections/{collection_name}/shards/{shard_id}` 返回完整检查元数据，但 Qdrant retrieve 使用 `with_vectors=False`。向量维度从 Collection 配置读取，不为展示目的传输向量本体。Service 显式映射允许返回的字段，未知 payload 仅在受控 `raw_metadata` 中返回。

### 5. 视觉规则使用现有 semantic tokens

知识库页面继续使用 `var(--noesis-*)` token，不增加写死主题颜色。集合列表改为轻量卡片或紧凑列表；页面容器和所有 flex/grid 子项设置正确的 `min-width: 0`，网格列使用可收缩的响应式定义。主操作每页只保留一个 primary button；删除进入更多菜单；Qdrant 主界面只展示连接状态，host/port 放入诊断浮层。

桌面、窄屏以及 newsprint、light、deep 三套主题都纳入验收截图。视觉一致性以同一标题层级、8/12/16 间距节奏、边框强度和空状态为准，而不是要求三个主题完全相同。

## Data Flow

1. 用户在文档表选择文件，`CollectionDetail.vue` 打开 chunk inspector。
2. inspector 以文件名和筛选条件调用分页 API；API 经 `knowledge_base_service` 调用 Qdrant adapter。
3. adapter 使用 Collection + file filter 读取一页 point，并将 next offset 编码为 opaque cursor；Service 映射为 `ChunkSummaryPage`，API 用 `ResponseUtil.success` 返回。
4. 用户选择 chunk 后调用现有详情路径；Service 映射允许公开的 payload 字段并返回 `ChunkDetail`。
5. 前端共享详情组件渲染来源、结构与正文；在检索测试场景额外注入检索分数。

## Errors and Compatibility

- Collection、文档或 shard 不存在时分别返回 404；无效 cursor、limit、sort 或筛选值返回 400，不把 Qdrant 参数错误笼统映射为 500。
- 历史 point 缺少 locator、identity 或 processing params 时，对应字段返回 `null`，其余内容仍可查看。
- 分页接口响应结构为 breaking；同仓库前端、类型和测试必须原子迁移。Collection、上传、删除、配置和检索接口保持不变。
- 用户可见错误只描述无法加载、筛选或查看的对象，不暴露 Qdrant 地址、索引名或后端路径。

## Risks / Trade-offs

- [既有 Collection 补建 payload index 可能短时增加 Qdrant 负载] → 幂等逐 Collection 创建，记录可诊断日志；失败时返回明确错误，不退回全量扫描。
- [按 chunk_index 排序时历史数据可能缺少序号] → 缺失值排在有序 chunk 之后，并以 point id 保证稳定次序。
- [部分解析器没有页码或 element type] → UI 使用可选字段和“未提供来源位置”，不伪造页码。
- [token_count 计算增加列表成本] → 优先读取入库元数据；缺失时允许为 null，不在每次分页请求中调用 tokenizer 扫描全文。
- [详情字段增加响应大小] → 完整正文和 raw/clean text 只由详情接口按需加载，列表只返回受限摘要。

## Migration Plan

1. 扩展 schema、Qdrant adapter、Service 与 API 测试，完成新分页响应和详情字段。
2. 更新前端类型及请求函数，再替换 chunk drawer/modal 为共享 inspector。
3. 调整集合列表和详情页视觉样式，验证三套主题及常见宽度。
4. 运行后端定向测试、前端 lint/build 和登录后的知识库端到端检查。
5. 发布后由首次访问触发既有 Collection 的幂等 payload index 补建；若发布失败，回滚前后端同一提交，不删除已创建索引，因为索引不改变 payload 数据和旧代码行为。

## Open Questions

- `token_count` 只有在现有入库元数据可用时展示；是否在后续变更中统一按实际 tokenizer 持久化，留给实施后的数据观察决定。
