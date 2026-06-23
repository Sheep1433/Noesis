## MODIFIED Requirements

### Requirement: 目录树浏览

系统 SHALL 通过 `GET /api/skills/fs/tree` 返回当前登录用户**可用** Skills 的树形结构，**SHALL** 同时包含：

- **平台**（`source=platform`）：根目录为 `extensions/skills`（或 `skills_filesystem_root` 配置）；
- **用户**（`source=user`）：根目录为 `.data/users/{user_id}/skills/`。

每个节点 SHALL 含 `key`、`label`、`isLeaf`、`children`（如有）及 `source` 字段。树顶 **SHALL** 以两个逻辑根或等价分组区分平台与用户技能，供前端展示「平台自带 / 我的上传」。

请求须经过与项目一致的身份校验依赖。

#### Scenario: 已认证用户拉取合并树

- **WHEN** 携带有效 JWT 访问树接口
- **THEN** 返回 200 且响应中同时包含 `source=platform` 与 `source=user` 的节点（用户目录为空时用户侧可为空树）

#### Scenario: 用户仅见本人用户技能

- **WHEN** 用户 A 访问树接口
- **THEN** `source=user` 部分 SHALL 仅扫描 `.data/users/{user_A}/skills/`，**SHALL NOT** 包含用户 B 的目录

### Requirement: 单文件读取

系统 SHALL 通过 `GET /api/skills/fs/file` 按路径返回文件文本或错误；请求 **SHALL** 含查询参数 `source=platform|user`（默认 `platform` 以保持兼容）。

- `source=platform`：路径 SHALL 限制在平台 Skills 根目录下；
- `source=user`：路径 SHALL 限制在 `.data/users/{current_user_id}/skills/` 下。

禁止路径穿越。

#### Scenario: 读取用户 skill 文件

- **WHEN** 用户请求 `source=user` 且 `path` 为合法相对路径
- **THEN** 系统 SHALL 从该用户 `skills/` 目录读取并返回内容

#### Scenario: 路径穿越攻击

- **WHEN** 请求路径包含 `..` 或解析后落在对应根目录之外
- **THEN** 系统 SHALL 拒绝并返回 400 或 404，且不读取系统任意文件

### Requirement: ZIP 上传与大小限制

系统 SHALL 通过 `POST /api/skills/fs/upload-zip` 接受 ZIP 并在服务端校验大小上限（如 `max_zip_bytes`）；解压目标 **SHALL** 仅为 `.data/users/{current_user_id}/skills/`。

系统 **SHALL NOT** 通过此接口向 `extensions/skills` 或平台公共目录写入。

#### Scenario: 用户上传 skill 成功

- **WHEN** 已认证用户上传合法 ZIP 且未超大小上限
- **THEN** 内容 SHALL 解压至 `.data/users/{user_id}/skills/` 且返回成功消息

#### Scenario: 超过大小上限

- **WHEN** 上传 ZIP 超过配置的最大字节数
- **THEN** 系统 SHALL 拒绝存储并返回明确错误信息，不部分写入损坏树

## ADDED Requirements

### Requirement: Agent SHALL 只读挂载用户 Skills

使用 `SkillsMiddleware` 的 Agent（至少 `DeepResearchAgent`）SHALL 在 `CompositeBackend` 中将 `/user-skills/` 路由映射至 `.data/users/{user_id}/skills/`（只读 `LocalShellBackend`），且 `SkillsMiddleware.sources` **SHALL** 包含 `/skills/` 与 `/user-skills/`。

#### Scenario: Agent 读取用户 skill

- **WHEN** 深度研究 Agent 访问 `/user-skills/my-tool/SKILL.md`
- **THEN** 系统 SHALL 从当前用户 `skills/` 目录读取，**SHALL NOT** 写入该路径

#### Scenario: 平台与用户 skill 路径隔离

- **WHEN** 平台与用户目录均存在同名文件夹 `foo`
- **THEN** `/skills/foo/` 与 `/user-skills/foo/` SHALL 解析至不同物理目录，互不影响
