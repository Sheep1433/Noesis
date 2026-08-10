## Why

当前知识库列表、集合详情、分片预览和检索调试采用不同的信息密度与交互层级，列表页还存在卡片横向裁切；用户查看 chunk 时需要从抽屉再次打开弹窗，却只能看到少量元数据。系统已经在 Qdrant payload 中保存来源定位、内容类型、稳定 evidence identity 和处理参数，因此需要把知识库界面调整为可连续检查文档、chunk 与检索质量的工作台。

## What Changes

- 统一知识库列表与集合详情的版式、间距、字体、边框和操作层级，并修复侧栏存在时的横向溢出。
- 精简集合卡片和连接状态展示；主界面不再直接暴露 Qdrant host/port，诊断信息按需查看。
- 将“分片抽屉 → 分片详情弹窗”改为单层 master-detail 检查器，支持连续选择前后 chunk。
- chunk 列表展示序号、标题路径、typed locator、内容类型、字符/token 数与内容摘要；详情按章节内容、来源结构、原文和 JSON 元数据分组展示。
- 原始 payload 与处理参数 JSON 默认折叠，避免工程字段压过主要信息。
- **BREAKING**：`GET /api/kb/collections/{collection_name}/documents/{file_name}/shards` 改为标准分页响应，增加服务端排序和筛选并返回轻量元数据；分片详情接口返回完整检查元数据。仓库内前端调用方在同一变更中迁移，不保留旧列表响应分支。
- 保留文档库、检索测试、策略配置三个任务入口，并统一 chunk 在文档浏览和检索结果中的展示语言。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `knowledge-base`: 增加知识库工作台的响应式布局、chunk 检查体验、元数据可见性，以及分片分页/筛选 API 行为要求。

## Impact

- 前端：`frontend/src/views/knowledge-base/`、`frontend/src/components/KnowledgeBase/`、`frontend/src/api/knowledgeBase.ts`。
- 后端：`/api/kb` 分片列表与详情接口、knowledge base schema/service、Qdrant scroll/retrieve 查询。
- 数据：不迁移既有 Qdrant payload；新界面优先读取现有 `element_type`、`locator`、`document_id`、`document_version_id`、`segment_id`、hash、raw/clean text 与处理参数，历史数据缺失字段时显示为空。
- 测试：补充分页/筛选/元数据 API 回归测试，以及桌面端、窄屏端与三套主题下的前端交互验证。
- 依赖：不新增运行时依赖；不改变 Collection、文档上传和检索接口。直接调用旧分片列表响应的外部客户端需要迁移到分页结构。
