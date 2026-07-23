## Purpose

统一 Skills 文件系统与沙箱挂载命名：平台与个人 Skills 分目录只读挂载，上传写宿主机个人目录，并支持当前会话 Skills 元数据失效重扫。

## ADDED Requirements

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

## REMOVED Requirements

### Requirement: Agent SHALL 只读挂载用户 Skills

**Reason**: 平台/个人 Skills 分目录挂载（`/skills/public/`、`/skills/personal/`）替代原单一 `/skills/` 只读挂载。
**Migration**: 见 ADDED「Agent SHALL 只读挂载平台与个人 Skills」。
