## ADDED Requirements

### Requirement: 系统 SHALL 提供个人与 Agent 设置壳

系统 SHALL 提供需登录的设置界面（路由建议 `/settings`），以 URL 查询参数（如 `s=`）切换 section。侧栏用户头像（或等价入口）SHALL 可进入该设置壳。

设置壳 SHALL 至少包含下列 section，且 **SHALL NOT** 提供 slash 命令配置类 section：

| Section id | 职责 |
|------------|------|
| `overview` | 用户标识摘要、默认 `qa_type` 展示（若有）、主题快捷入口 |
| `profile` | 用户画像（L0）编辑 |
| `memory` | 记忆与偏好（L1，及 L2 入口若已启用） |
| `capabilities` | 深链至 Skills / MCP / 知识库管理页 |
| `automation` | 定时任务（cron）列表与编辑（见 `agent-scheduled-tasks`） |
| `channels` | 通讯通道配置（见 `agent-messaging-channels`） |
| `account` | 退出登录等账号操作 |

#### Scenario: 从侧栏进入设置

- **WHEN** 已登录用户点击侧栏头像并选择进入设置（或直接打开设置入口）
- **THEN** 系统 SHALL 导航至设置壳且默认 section 为 `overview` 或上次合法 `s` 值

#### Scenario: 切换 section

- **WHEN** 用户选择 `memory` section
- **THEN** URL SHALL 反映该 section，且页面展示记忆编辑面而非整页刷新丢失壳布局

#### Scenario: 无 slash 配置

- **WHEN** 用户打开设置壳并遍历导航
- **THEN** UI **SHALL NOT** 出现 slash 命令注册、绑定或编辑入口

### Requirement: 画像 section SHALL 编辑 USER.md

`profile` section SHALL 允许用户查看与保存与磁盘 `users/{user_id}/USER.md` 一致的内容。系统 SHALL 提供不依赖 `session_id` 的用户级读写 API（例如 `GET/PUT /api/user/memory/USER.md`），且写入结果 SHALL 与 Agent `/memory/USER.md` 为同一文件。

#### Scenario: 设置页保存画像

- **WHEN** 用户在 `profile` 编辑画像并保存成功
- **THEN** `users/{user_id}/USER.md` SHALL 更新，且后续 `MemoryMiddleware` 加载可见新内容

#### Scenario: 未登录拒绝

- **WHEN** 未认证客户端请求用户级 memory API
- **THEN** SHALL 返回 HTTP 401

### Requirement: 记忆 section SHALL 编辑 AGENTS.md

`memory` section SHALL 允许用户查看与保存 `users/{user_id}/AGENTS.md`。系统 SHALL 提供用户级 API（例如 `GET/PUT /api/user/memory/AGENTS.md`），与 Agent `/memory/AGENTS.md` 同盘。

UI SHALL 展示文件最近修改时间（或等价元数据），便于用户发现 Agent 写入。

#### Scenario: 设置页保存偏好记忆

- **WHEN** 用户在 `memory` 编辑 `AGENTS.md` 并保存
- **THEN** 磁盘文件 SHALL 更新且预览/编辑区展示最新内容

### Requirement: 能力 section SHALL 深链既有管理页

`capabilities` section SHALL 提供通往个人 Skills、MCP、知识库管理界面的导航链接，**SHALL NOT** 在设置壳内复制整套管理 UI。

#### Scenario: 跳转 Skills

- **WHEN** 用户在 `capabilities` 点击 Skills 入口
- **THEN** 系统 SHALL 导航至既有 Skills 管理路由

### Requirement: 设置壳导航 SHALL 包含自动化与通讯

设置壳导航 SHALL 包含 `automation` 与 `channels` 两项。`automation` 的行为要求见能力 `agent-scheduled-tasks`；`channels` 的行为要求见能力 `agent-messaging-channels`。即便某通道运行时仍为 stub，导航亦 SHALL 展示可理解的占位或「即将推出」状态，**SHALL NOT** 省略导航项（除非产品配置显式关闭整个通道能力）。

#### Scenario: 导航含自动化与通讯

- **WHEN** 用户打开设置壳
- **THEN** 导航 SHALL 包含 `automation` 与 `channels` 项
