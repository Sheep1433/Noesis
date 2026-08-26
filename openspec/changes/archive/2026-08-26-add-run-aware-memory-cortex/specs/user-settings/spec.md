## MODIFIED Requirements

### Requirement: 设置页为用户记忆主编辑入口

`profile` section SHALL 编辑 `users/{user_id}/USER.md`，`memory` section SHALL 编辑 `users/{user_id}/AGENTS.md`，且两者 SHALL 仅提供 Markdown 原文编辑。系统 SHALL 提供不依赖 `session_id` 的用户级读写 API（`GET/PUT /api/user/memory/USER.md` 与 `GET/PUT /api/user/memory/AGENTS.md`），写入结果 SHALL 与 Agent `/memory/` 为同一磁盘文件。

系统 SHALL NOT 为 `USER.md` 维护固定“常用字段”区块或第二套结构化字段保存接口；`memory` section SHALL 展示文件最近修改时间（或等价元数据），便于用户发现显式修改。会话上下文面板编辑 `USER.md` / `AGENTS.md` SHALL 作为兼容路径（见 `chat-composer`）。机器经验 SHALL 在独立区域通过结构化 API 治理，SHALL NOT 自动写入或修改 `USER.md` / `AGENTS.md`。设置页 SHALL NOT 再展示 Dream、按日记忆、日期整理、自动补写或 `memory/YYYY-MM-DD.md` 文件入口。

#### Scenario: 设置页保存画像
- **WHEN** 用户在 `profile` 编辑画像并保存成功
- **THEN** `users/{user_id}/USER.md` SHALL 更新，且后续显式上下文加载可见新内容

#### Scenario: 设置页保存偏好记忆
- **WHEN** 用户在 `memory` 编辑 `AGENTS.md` 并保存
- **THEN** 磁盘文件 SHALL 更新且预览/编辑区展示最新内容

#### Scenario: 不展示固定画像字段
- **WHEN** 用户打开 `profile` section
- **THEN** 页面 SHALL 直接展示原文编辑器且不展示称呼、时区、语言、角色固定表单

#### Scenario: 不展示旧按日记忆入口
- **WHEN** 用户打开设置页的 Memory 区域
- **THEN** 页面 SHALL NOT 展示日期整理、Dream 状态、按日文件、自动补写或旧 L2 搜索入口

#### Scenario: 未登录拒绝
- **WHEN** 未认证客户端请求用户级 memory API
- **THEN** SHALL 返回 HTTP 401

## REMOVED Requirements

### Requirement: 记忆分层 L0/L1/L2

**Reason**: 未上线的 L2 按日记忆与新的 Run-aware machine memory 重复提取并形成第二事实来源；L0/L1 命名也不再用于机器经验设计。

**Migration**: 删除 L2/Dream 代码、API、UI、scheduler、索引和 `memory/YYYY-MM-DD.md` 运行时数据；`USER.md` / `AGENTS.md` 作为用户显式维护上下文继续保留，机器经验使用独立 PostgreSQL/item/workspace。

### Requirement: 系统 SHALL 按自然日整理跨会话记忆

**Reason**: 自然日摘要不能可靠表示任务 decision、workflow、gotcha、provenance 和终态失败轨迹，并与统一 Run pipeline 重复。

**Migration**: 不迁移按日条目；功能未上线，删除 `MemoryDreamService`、整理 prompt、写入路径和对应运行时文件。

### Requirement: 系统 SHALL 提供受限的跨会话记忆检索

**Reason**: 该要求只搜索按日文件，与新的 item/source-span/Run evidence 检索重复。

**Migration**: 删除 L2 日期/分类检索和 UI；`search_memory` 只检索新的结构化机器经验，来源通过 Run snapshot/span 读取。

### Requirement: 系统 SHALL 自动补写上一日记忆

**Reason**: 自动补写属于旧 Dream scheduler，会产生独立后台写入链路和不可比较的记忆来源。

**Migration**: 删除 scheduler、last-day 检查、重试状态和测试；不提供兼容开关或替代定时任务。

## ADDED Requirements

### Requirement: 用户显式上下文 SHALL 与机器经验分离

`USER.md` / `AGENTS.md` SHALL 只由用户或明确的文件编辑操作修改，作为显式上下文来源；decision、experience、workflow 和 gotcha SHALL 保存在机器经验事实源，并经独立状态、scope、provenance 和 evidence 治理。自动 extraction/consolidation SHALL NOT 修改显式上下文，设置预览 SHALL 分别标注显式上下文和自动 Bulletin。

#### Scenario: 自动整理不修改显式上下文
- **WHEN** 系统完成 Run extraction 或 consolidation
- **THEN** `USER.md` 与 `AGENTS.md` SHALL 保持不变

#### Scenario: 预览区分来源
- **WHEN** 用户查看下一次 Agent 上下文预览
- **THEN** 页面 SHALL 分别展示显式上下文与自动 Memory Bulletin
- **AND** SHALL NOT 把机器经验伪装成用户手写规则

### Requirement: 设置页 SHALL 提供四类经验记忆治理入口

设置页 Memory section SHALL 展示当前用户的 decision、experience、workflow 和 gotcha，至少包括 type、status、scope、statement、applicability、独立 Run evidence 数、last verified time、版本和来源入口。用户 SHALL 能按 type/status/project scope 搜索过滤，并执行查看来源、编辑、activate、disable、enable、invalidate 和 delete；candidate item SHALL 只能经用户显式确认（activate）转为 active；前端 SHALL 展示加载、成功、冲突和用户可理解的失败状态。

#### Scenario: 查看四类记忆和证据
- **WHEN** 已认证用户打开经验记忆列表
- **THEN** 页面 SHALL 只展示该用户 item 和独立 Run evidence 数
- **AND** 非 active 状态 SHALL 有明确标识且不暗示当前生效

#### Scenario: 编辑记忆形成版本
- **WHEN** 用户修改 statement 或 applicability
- **THEN** Service SHALL 保存可审计的新 revision 和用户来源
- **AND** 页面 SHALL 能识别当前版本

#### Scenario: 显式确认 candidate 全局适用
- **WHEN** 非 Git Run 自动产生的 global candidate 待用户确认
- **THEN** 用户显式 activate 后 item SHALL 转为 active 并记录用户确认来源
- **AND** 未经确认的 candidate SHALL NOT 自动注入

#### Scenario: 删除确认说明再生成语义
- **WHEN** 用户请求删除经验记忆
- **THEN** 页面 SHALL 在确认前说明删除当前记录不阻止未来相似 Run 重新生成
- **AND** 用户确认后 SHALL 清理 item/evidence/relation 并排队清理派生视图

### Requirement: 设置页 SHALL 展示自动处理健康

设置页 SHALL 展示当前用户最近成功 capture/consolidation 时间，以及 pending、partial、failed、dead、skipped-disabled 数量和 workspace/index 是否落后。用户文案 SHALL 使用业务含义，不得显示数据库表、claim token、provider key、服务端路径、内部网络位置或未脱敏错误。健康展示 SHALL 不改变单一用户开关语义。

#### Scenario: 后台任务部分失败
- **WHEN** 用户的长 Run 只有部分 chunk extraction 成功
- **THEN** 设置页 SHALL 显示“部分处理”及安全摘要
- **AND** SHALL NOT 把该 Run 展示为完整成功

#### Scenario: dead job 可诊断
- **WHEN** job 达到最大 attempts 进入 dead
- **THEN** 设置页 SHALL 计入处理失败
- **AND** SHALL 提供不泄露内部细节的处理建议或重试状态

### Requirement: 经验记忆设置 API SHALL 鉴权并经 Service 执行

系统 SHALL 在 `/api/user/memory/cortex` 静态前缀下提供 preference、item list/detail/update、source/evidence、activate、disable、enable、invalidate、delete 和 processing health API。所有接口 SHALL 使用 Cookie Session + CSRF、当前用户/授权 scope 校验、`noesis.schemas.memory`、统一响应和 Service 状态机；API SHALL NOT 定义同义 schema 或直接查询/修改 ORM。旧 Dream/按日记忆 API SHALL 被删除，该前缀 SHALL NOT 与 `USER.md` / `AGENTS.md` 文件 API 冲突。

#### Scenario: 未认证请求
- **WHEN** 未认证客户端请求任一经验记忆设置 API
- **THEN** 系统 SHALL 返回 HTTP 401

#### Scenario: 越权 memory/snapshot id
- **WHEN** 用户提交不属于自己的 memory、evidence 或 snapshot id
- **THEN** 系统 SHALL 返回 404 或等价不泄露响应
- **AND** SHALL NOT 返回内容、scope、provider、来源或处理状态

#### Scenario: 重复状态操作
- **WHEN** 用户重复 activate、disable、enable 或 invalidate 同一 item
- **THEN** Service SHALL 返回确定的幂等结果或明确冲突
- **AND** SHALL NOT 产生两个 current row 或分叉 supersession 链

### Requirement: 用户 SHALL 通过单一开关控制自动经验记忆

设置页 SHALL 只提供一个“经验记忆”用户开关，持久化字段为 `enabled`，默认关闭；系统 SHALL NOT 提供平台总开关或拆分的 capture/extraction/consolidation/injection 用户开关。开启后允许后续有稳定证据的终态主 Run 自动 capture、后台整理和新 Run fast Bulletin；关闭后不创建新的自动任务、不继续后续阶段且新 Run 不自动注入。关闭 SHALL NOT 删除已有 item/snapshot，且 SHALL NOT 禁止已有记忆查看、显式搜索、来源读取或治理。

#### Scenario: 用户开启经验记忆
- **WHEN** 用户开启“经验记忆”
- **THEN** 后续有稳定证据的终态主 Run SHALL 创建自动 capture job
- **AND** 后续新 Run MAY 注入通过门控的 Bulletin

#### Scenario: 用户关闭经验记忆
- **WHEN** 用户关闭“经验记忆”
- **THEN** 后续 eligible Run SHALL NOT 创建自动 capture job，claimed job SHALL 在阶段边界安全停止，后续新 Run SHALL 零自动注入
- **AND** 已有记忆 SHALL 保留并可查看、显式搜索或治理

#### Scenario: 重新开启不默认回放历史
- **WHEN** 用户关闭一段时间后重新开启经验记忆
- **THEN** 系统 SHALL 从重新开启后的有稳定证据终态主 Run 开始自动处理
- **AND** SHALL NOT 未经用户显式操作回放关闭期间全部历史 Run
