## 1. 后端契约与回归测试

- [x] 1.1 为分页请求、`ChunkSummaryPage` 和扩展 `ChunkDetail` 增加 Pydantic schema，明确 limit 上限、opaque cursor、排序与筛选枚举
- [x] 1.2 先补充分片分页 API 测试，覆盖首末页、稳定排序、无重复 cursor 翻页、关键词/类型/locator 筛选和历史缺失字段
- [x] 1.3 补充分片详情 API 测试，覆盖受控元数据、禁止返回 vector、404 与参数错误 HTTP/业务 code

## 2. Qdrant 分页与元数据读取

- [x] 2.1 在 Qdrant adapter 中实现按 `file_name` 过滤的 cursor 分页、count 和稳定 `chunk_index + point_id` 排序，删除一次读取 10000 条再分页的路径
- [x] 2.2 为新旧 Collection 幂等建立分页/筛选需要的 payload index，并验证索引失败返回可定位错误而不退回全量扫描
- [x] 2.3 扩展 Service 映射，列表仅返回摘要和受限 preview，详情映射 locator、element type、hash、三层 identity、raw/clean text 与处理参数
- [x] 2.4 将 shard detail retrieve 改为不读取 vector 本体，并从 Collection 信息取得 vector dimension
- [x] 2.5 更新 `/api/kb/collections/{collection_name}/documents/{file_name}/shards` 参数和分页响应，保持 `ResponseUtil`、认证和异常映射约定

## 3. 分块浏览器

- [x] 3.1 更新 `frontend/src/api/knowledgeBase.ts` 的分页、summary/detail 类型和请求函数，移除对旧裸列表响应的依赖
- [x] 3.2 将 `DocumentDrawer.vue` 与 `ShardDetail.vue` 的二层流程替换为单层响应式 master-detail inspector
- [x] 3.3 实现 chunk 列表的序号、标题路径、typed locator、element type、字符/token 数、内容摘要和分页状态
- [x] 3.4 实现章节内容、来源结构、原文与 JSON 元数据分组，移除独立的稳定身份区域
- [x] 3.5 增加关键词、element type 筛选及相邻 chunk 导航；locator type 保留 API 能力但不进入默认工具栏，窄屏返回列表时保留筛选和 cursor 上下文
- [x] 3.6 让文档浏览与检索测试共用 chunk 摘要/详情组件，检索分数仅在检索上下文展示

## 4. 知识库页面视觉调整

- [x] 4.1 修复知识库列表在应用侧栏和常见桌面宽度下的横向裁切，补齐 grid/flex `min-width` 与响应式列规则
- [x] 4.2 精简集合卡片和状态区，统一列表页与详情页的标题、间距、字体、边框、空状态和操作层级
- [x] 4.3 每页只保留一个 primary action，将删除移入更多菜单，并把 Qdrant host/port 移入按需诊断信息
- [x] 4.4 使用现有 `var(--noesis-*)` semantic tokens 验证 newsprint、light、deep 三套主题，不新增写死主题颜色

## 5. 验证与交付

- [x] 5.1 运行知识库后端定向测试并验证无 Qdrant 全量读取和 vector 传输回退
- [x] 5.2 运行前端相关测试、`pnpm lint` 和 `pnpm build`
- [x] 5.3 在桌面与窄屏下完成登录后的端到端检查：集合列表、上传文档、chunk 翻页/筛选/详情、检索结果和三套主题
- [x] 5.4 使用 `code-review` 同时检查仓库规范与本 OpenSpec，修复确认的问题后重新运行受影响验证
