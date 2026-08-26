## MODIFIED Requirements

### Requirement: 设置页为用户记忆主编辑入口

`profile` section SHALL 编辑 `users/{user_id}/USER.md`，`memory` section SHALL 提供记忆文件管理：`MEMORY.md` 索引与分类条目文件的查看与编辑（经文件服务写入）、`journal/` 情景日志查看，以及单一「记忆」开关（关闭后停止抽取与注入，文件保留可编辑可搜索）。系统 SHALL 提供不依赖 `session_id` 的用户级读写 API，写入结果 SHALL 与磁盘文件为同一真相。系统 SHALL NOT 为 `USER.md` 维护固定“常用字段”区块或第二套结构化字段保存接口。用户文案 SHALL 使用业务词。

#### Scenario: 设置页保存画像
- **WHEN** 用户在 `profile` 编辑画像并保存成功
- **THEN** `users/{user_id}/USER.md` SHALL 更新，且后续显式上下文加载可见新内容

#### Scenario: 设置页编辑条目
- **WHEN** 用户在 `memory` 区域编辑一个条目文件并保存
- **THEN** 磁盘条目文件 SHALL 更新，索引行 SHALL 同步
- **AND** 引擎后续写入 SHALL 基于修改后文件增量进行

#### Scenario: 开关关闭
- **WHEN** 用户关闭记忆开关
- **THEN** 新会话不再注入、新终态不再抽取
- **AND** 记忆文件保留，用户仍可编辑与检索

#### Scenario: 未登录拒绝
- **WHEN** 未认证客户端请求用户级 memory API
- **THEN** SHALL 返回 HTTP 401

## REMOVED Requirements

### Requirement: 设置页 SHALL 提供四类经验记忆治理入口

移除原因：四类 item 与条目级治理 API 随旧链路删除；治理由文件编辑、journal 检索与整理任务承担。

### Requirement: 经验记忆设置 API SHALL 鉴权并经 Service 执行

移除原因：`/api/user/memory/cortex` 结构化治理 API 删除；文件管理 API 沿用现有 memory 文件 API 的鉴权约定。

### Requirement: 用户 SHALL 通过单一开关控制自动经验记忆

移除原因：开关语义变更——旧开关控制 capture/consolidation/bulletin 管线，新开关只控制「抽取与注入」，已并入上方 MODIFIED requirement。
