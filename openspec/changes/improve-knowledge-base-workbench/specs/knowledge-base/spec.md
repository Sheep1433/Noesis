## ADDED Requirements

### Requirement: 知识库工作台 SHALL 保持响应式与主题一致

系统 SHALL 在知识库列表、集合详情、文档列表、chunk 检查器和检索结果中使用统一的信息层级、semantic tokens 与操作层级；在应用侧栏存在的桌面和窄屏可用宽度下 SHALL NOT 出现页面级横向裁切。每个页面 SHALL 仅突出当前主要操作，删除等危险操作 SHALL 与主要操作在视觉上区分。

#### Scenario: 侧栏存在时浏览集合列表

- **WHEN** 已登录用户在带应用侧栏的支持宽度下打开知识库列表
- **THEN** 所有集合卡片、页面标题和主要操作 SHALL 位于可见区域
- **AND** 页面 SHALL NOT 因网格最小宽度产生横向裁切

#### Scenario: 切换界面主题

- **WHEN** 用户分别使用 newsprint、light 或 deep 主题打开知识库页面
- **THEN** 页面 SHALL 使用现有 semantic tokens 呈现可读的标题、正文、边框、状态和交互反馈
- **AND** 列表页与集合详情 SHALL 保持相同的标题、间距和操作层级

#### Scenario: 展示向量库连接状态

- **WHEN** 向量库连接正常
- **THEN** 主界面 SHALL 以紧凑状态展示连接可用
- **AND** SHALL NOT 默认展示 host、port 或后端配置路径

### Requirement: 用户 SHALL 在单层检查器中连续浏览 chunk

系统 SHALL 为选中文档提供单层 master-detail chunk 检查器。桌面端 SHALL 同时显示 chunk 列表和当前详情；窄屏端 SHALL 在同一检查器内切换列表与详情。系统 SHALL NOT 要求用户从 chunk 列表再打开第二个 modal 才能查看完整信息。

#### Scenario: 桌面端选择 chunk

- **WHEN** 用户在文档库中打开一个包含多个 chunk 的文档并选择其中一条
- **THEN** 检查器 SHALL 保留可操作的 chunk 列表
- **AND** 同时展示所选 chunk 的详情
- **AND** 用户 SHALL 能直接选择相邻 chunk 而无需关闭详情

#### Scenario: 窄屏端返回列表

- **WHEN** 窄屏用户从 chunk 列表进入某一 chunk 详情
- **THEN** 系统 SHALL 在同一检查器内展示详情
- **AND** 提供明确的返回列表操作并保留筛选和分页上下文

### Requirement: 分块详情 SHALL 以可理解的结构展示来源与元数据

chunk 列表 SHALL 展示序号、标题路径、可用 typed locator、内容类型、字符数、可选 token 数和受限内容摘要。chunk 详情 SHALL 分为章节内容、来源结构、原文和元数据四块；元数据 SHALL 以可复制、可滚动的 JSON 展示字段快照，包括字段存在时的 hash、`document_id`、`document_version_id`、`segment_id`、处理参数和受控补充元数据。系统 SHALL NOT 单独展示名为“稳定身份”的区域，且 SHALL NOT 暴露未筛选的内部对象。

#### Scenario: 查看带来源信息的 chunk

- **WHEN** 所选 chunk payload 含 page locator、element type 和可追溯元数据
- **THEN** 详情 SHALL 以章节内容、来源结构、原文和 JSON 元数据分组展示页码与内容类型
- **AND** 正文预览 SHALL 与元数据区明确分隔
- **AND** SHALL NOT 单独出现稳定身份区域

#### Scenario: 查看历史 chunk

- **WHEN** 历史 chunk 缺少 locator、identity、token count 或处理参数
- **THEN** 系统 SHALL 继续展示已有正文与元数据
- **AND** SHALL NOT 伪造缺失字段或因单个可选字段缺失导致详情加载失败

#### Scenario: 从检索结果查看 chunk

- **WHEN** 用户从检索测试结果打开 chunk 详情
- **THEN** 系统 SHALL 使用与文档检查器相同的 chunk 信息结构
- **AND** 仅在本次检索上下文中额外展示 recall、rerank 和最终分数

### Requirement: 分片列表 API SHALL 提供有界分页与筛选

`GET /api/kb/collections/{collection_name}/documents/{file_name}/shards` SHALL 接受有服务端上限的 `limit`、opaque `cursor`、排序和受支持筛选参数，并通过 `ResponseUtil` 返回含 `items`、`total`、`next_cursor` 的分页数据。系统 SHALL 在 Qdrant 侧按文件及筛选条件读取当前页，SHALL NOT 为返回一页结果先加载文档的全部 chunk。该响应结构替换旧裸列表响应，仓库内调用方 SHALL 同步迁移。

#### Scenario: 读取第一页 chunk

- **WHEN** 客户端请求某文档的首个分页且该文档的 chunk 数超过 limit
- **THEN** 响应 SHALL 只包含不超过 limit 的摘要项、文档总数和非空 next_cursor
- **AND** items SHALL 按 `chunk_index ASC` 与稳定次级键排序

#### Scenario: 使用 cursor 读取下一页

- **WHEN** 客户端携带上一页返回的 next_cursor 和相同筛选条件
- **THEN** 系统 SHALL 返回下一页且不重复上一页条目
- **AND** 最后一页的 next_cursor SHALL 为 null

#### Scenario: 按元数据筛选 chunk

- **WHEN** 客户端按关键词、element type 或 locator type 请求文档 chunk
- **THEN** items 与 total SHALL 只统计匹配当前文档和筛选条件的 chunk
- **AND** 不匹配的其他文档 point SHALL NOT 出现在响应中

#### Scenario: 分页参数无效

- **WHEN** 客户端提交无效 cursor、超出上限的 limit 或不支持的排序/筛选值
- **THEN** API SHALL 返回 400 和可理解的参数错误
- **AND** SHALL NOT 将其笼统映射为 500

### Requirement: 分片详情 API SHALL 返回受控的完整检查元数据

`GET /api/kb/collections/{collection_name}/shards/{shard_id}` SHALL 返回明确 schema 允许的 chunk 内容、来源、结构、分块策略、hash 和稳定 identity 字段。实现 SHALL NOT 为展示详情传输向量本体；向量维度 SHALL 从 Collection 信息或等价元数据取得。

#### Scenario: 获取分片详情

- **WHEN** 客户端请求存在的 shard id
- **THEN** API SHALL 返回完整正文和该 point 中可用的受控检查字段
- **AND** SHALL NOT 返回向量数组或未筛选的内部 payload

#### Scenario: 分片不存在

- **WHEN** 客户端请求不存在的 shard id
- **THEN** API SHALL 返回 HTTP 404 与业务 code 404
