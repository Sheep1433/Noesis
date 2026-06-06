## Purpose

本能力规范 Noesis 磁盘型 Skills 目录管理：在配置的根路径（默认可为仓库下 `backend/skills`）上提供只读树浏览、单文件内容与 ZIP 上传解压，供 Agent 或人工维护技能包，不涉及历史 MySQL Skill 表。

## Requirements

### Requirement: 目录树浏览

系统 SHALL 通过 `GET /api/skills/fs/tree` 返回可序列化的树形结构，节点包含路径、类型（文件/目录）及展示所需元数据；请求须经过与项目一致的身份校验依赖。

#### Scenario: 已认证用户拉取树

- **WHEN** 携带有效 JWT 访问树接口
- **THEN** 返回 200 且结构与非认证失败相区分

### Requirement: 单文件读取

系统 SHALL 通过 `GET /api/skills/fs/file`（或查询参数约定）按路径返回文件文本或错误；路径 SHALL 被限制在配置的根目录之下，禁止路径穿越。

#### Scenario: 路径穿越攻击

- **WHEN** 请求路径包含 `..` 或解析后落在根目录之外
- **THEN** 系统 SHALL 拒绝并返回 400 或 404，且不读取系统任意文件

### Requirement: ZIP 上传与大小限制

系统 SHALL 通过 `POST /api/skills/fs/upload-zip` 接受 ZIP 并在服务端校验大小上限（如 `max_zip_bytes`）；解压目标 SHALL 位于允许的技能根目录内。

#### Scenario: 超过大小上限

- **WHEN** 上传 ZIP 超过配置的最大字节数
- **THEN** 系统 SHALL 拒绝存储并返回明确错误信息，不部分写入损坏树
