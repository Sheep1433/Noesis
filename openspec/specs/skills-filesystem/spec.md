## Purpose

本能力规范 Noesis 磁盘型 Skills 目录管理：在配置的根路径（默认可为仓库下 `backend/skills`）上提供只读树浏览、单文件内容与 ZIP 上传解压，供 Agent 或人工维护技能包，不涉及历史 MySQL Skill 表。
## Requirements
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

### Requirement: 个人 Skills 上传后当前会话元数据 SHALL 可失效

系统在用户经 `POST /api/skills/fs/upload-zip`（或删除个人 Skill）成功变更 `.data/users/{user_id}/skills/` 后，**SHALL** 使该用户进行中 session 的 Skills 元数据失效或触发重扫，使得后续 Agent 轮次能发现新增/删除的 Skill，**SHALL NOT** 要求用户必须新建 session 才能看到变更（清理 checkpoint 不得作为唯一手段）。

#### Scenario: 上传后同 session 可见新 Skill

- **WHEN** 用户在 session `s1` 中上传个人 Skill `foo`，随后在同一 session 继续对话且 Agent 依赖 Skills 列表
- **THEN** 系统 SHALL 使 `foo` 对后续加载可见（重扫或显式 invalidate）

### Requirement: Agent SHALL 只读挂载平台与个人 Skills

使用 `SkillsMiddleware` 的 Agent **SHALL** 将平台 Skills 映射为 **`/skills/public/`**，将用户 Skills 映射为 **`/skills/personal/`**（只读）。`SkillsMiddleware.sources` **SHALL** 包含上述两者，且 **personal 顺序在 public 之后**，使同名个人 Skill 覆盖公共 Skill。

沙箱内对应 ro 挂载（见 `agent-sandbox`）。上传 API 写入 `.data/users/{user_id}/skills/` 后，沙箱 ro 挂载 **SHALL** 立即可见新文件（依赖 bind mount，无需复制进容器层）。

#### Scenario: Agent 读取个人 skill

- **WHEN** Agent 访问 `/skills/personal/my-tool/SKILL.md`
- **THEN** 系统 SHALL 从当前用户 `skills/` 目录读取，**SHALL NOT** 写入该路径

#### Scenario: 同名个人覆盖公共

- **WHEN** 平台与用户目录均存在同名 Skill 文件夹 `foo`
- **THEN** Skills 解析 **SHALL** 优先使用个人 `foo`（sources 顺序）

#### Scenario: 平台与个人路径隔离

- **WHEN** Agent 分别读取 `/skills/public/foo/SKILL.md` 与 `/skills/personal/foo/SKILL.md`
- **THEN** SHALL 解析至不同物理目录

