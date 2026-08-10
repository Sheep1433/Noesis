## ADDED Requirements

### Requirement: 系统 SHALL 以用户任务意图呈现聊天模式

聊天页 SHALL 将 `COMMON_QA` 呈现为“聊天”，将 `SUPER_AGENT_QA` 呈现为“任务”，并将 `FAULT_OPERATION_QA` 作为任务类专项入口“故障排查”展示。用户可见文案 SHALL NOT 暴露 `qa_type` 常量或 Agent 类名。

#### Scenario: 查看模式选择面板

- **WHEN** 用户打开聊天模式选择面板
- **THEN** 系统 SHALL 展示“聊天”“任务”和“故障排查”及简短用途说明
- **AND** 内部 SHALL 分别映射到 `COMMON_QA`、`SUPER_AGENT_QA`、`FAULT_OPERATION_QA`

### Requirement: 模式切换 SHALL 保留已有会话

聊天模式 SHALL 绑定会话。用户从已有消息的会话选择另一模式时，系统 SHALL 进入目标模式的新 COMPOSING 表面，**SHALL NOT** 删除或改写原会话消息；从历史恢复会话时 SHALL 恢复其原 `qa_type` 与用户可见模式。

#### Scenario: 已有消息时切换模式

- **WHEN** 用户在已有消息的“聊天”会话中选择“任务”
- **THEN** 系统 SHALL 创建或进入 `SUPER_AGENT_QA` 的空白 COMPOSING 表面
- **AND** 原 `COMMON_QA` 会话 SHALL 仍可从历史恢复

#### Scenario: 从历史恢复模式

- **WHEN** 用户打开一个 `qa_type=SUPER_AGENT_QA` 的历史会话
- **THEN** 顶栏 SHALL 显示“任务”
- **AND** 系统 SHALL 展示该会话原有消息

### Requirement: 移动端聊天页 SHALL 使用沉浸式布局

在移动端聊天路由中，系统 SHALL 常态展示极简会话顶栏、消息区、当前模式的紧凑能力卡片和 Composer；全局底部导航、常驻问答类型按钮、品牌欢迎区和常驻会话文件按钮 SHALL 隐藏。紧凑能力卡片 SHALL 保留当前模式的原始标题、功能描述和至多两条能力说明。历史、产品导航、会话文件及工具 SHALL 仍可通过抽屉或紧凑菜单访问。

#### Scenario: 打开移动端空白聊天页

- **WHEN** 用户以移动端宽度打开空白聊天页
- **THEN** 页面 SHALL 不显示底部全局导航、问答类型按钮或品牌欢迎区
- **AND** 页面 SHALL 显示模式入口、紧凑能力卡片、消息区和 Composer

#### Scenario: 移动端访问设置或知识库管理页

- **WHEN** 用户以移动端宽度打开设置、知识库列表或知识库详情页
- **THEN** 页面 SHALL 不显示底部全局导航，也 SHALL NOT 保留底部导航高度
- **AND** 知识库为空时，空状态 SHALL 靠近页面状态区展示，不得因占满剩余高度而落到屏幕中下部

#### Scenario: 移动端访问其它模块

- **WHEN** 用户在移动端聊天页打开左侧抽屉
- **THEN** 系统 SHALL 提供会话历史与其它主要产品模块的导航入口

### Requirement: 测试用例 SHALL 不开放前端导航入口

桌面侧栏、移动端底部导航和聊天历史抽屉 SHALL 不展示测试用例入口。现有测试用例页面路由与后端执行能力 MAY 暂时保留，供后续退役步骤处理。

#### Scenario: 用户查看产品导航

- **WHEN** 用户查看桌面侧栏、移动端底部导航或聊天历史抽屉
- **THEN** 导航中 SHALL 不出现测试用例入口

### Requirement: 执行必要状态 SHALL 按需展示

重连、HITL 和 Todo 状态 SHALL 仅在当前执行存在对应状态时出现在 Composer 上方；状态消失后 SHALL 不继续占用移动端聊天空间。

#### Scenario: 普通空闲会话

- **WHEN** 当前会话没有重连、HITL 或 Todo 状态
- **THEN** Composer 上方 SHALL 不显示对应状态面板
